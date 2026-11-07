"""One deterministic calculator for automatic evaluations and human reviews."""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from backend.app.schemas.checklists import (
    CalculatedResult,
    ChecklistVersion,
    Criterion,
    CriterionCalculation,
    ScoringAnswer,
    SectionCalculation,
)


class ChecklistError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _ratio(criterion: Criterion, answer: ScoringAnswer) -> Decimal | None:
    if answer.decision_status != "evaluated" or answer.applicability == "undetermined":
        raise ChecklistError("unresolved_answer", "Незавершённый ответ нельзя включить в итог.")
    if answer.applicability == "not_applicable":
        if not criterion.allow_na:
            raise ChecklistError("na_not_allowed", f"Для критерия {criterion.id} N/A запрещён.")
        if answer.value is not None:
            raise ChecklistError("invalid_na", "Для N/A значение должно быть null.")
        return None
    if answer.value is None:
        raise ChecklistError("missing_value", f"Не указан ответ по критерию {criterion.id}.")
    if criterion.answer_type in {"boolean", "flag"}:
        if type(answer.value) is not bool:
            raise ChecklistError("invalid_boolean", f"Критерий {criterion.id} требует Да или Нет.")
        return Decimal(int(answer.value))
    if type(answer.value) is bool:
        raise ChecklistError("invalid_scale", "Boolean не является числовой ступенью.")
    value = Decimal(str(answer.value))
    for level in criterion.levels:
        if Decimal(str(level.value)) == value:
            return level.normalized_value
    raise ChecklistError("invalid_scale", f"Значение не входит в шкалу критерия {criterion.id}.")


def _display(value: Decimal, places: int = 2) -> str:
    return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def calculate(
    checklist_version: ChecklistVersion, criterion_answers: list[ScoringAnswer]
) -> CalculatedResult:
    """Validate all answers and calculate against the exact saved rule snapshot.

    This function has no model, database or network dependency. N/A conditions and
    evidence must be checked by the evaluator/reviewer; this validates their allowed
    representation and computes the result without interpreting conversation text.
    """
    ids = [answer.criterion_id for answer in criterion_answers]
    expected = {criterion.id for criterion in checklist_version.criteria}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ChecklistError(
            "criterion_set_mismatch", "Каждый критерий версии должен присутствовать ровно один раз."
        )
    answers = {answer.criterion_id: answer for answer in criterion_answers}
    with localcontext() as context:
        context.prec = 50
        rows = [
            (criterion, answers[criterion.id], _ratio(criterion, answers[criterion.id]))
            for criterion in checklist_version.criteria
        ]
        weighted = [
            (criterion, ratio)
            for criterion, _, ratio in rows
            if ratio is not None and criterion.answer_type != "flag"
        ]
        denominator = sum((Decimal(str(c.weight)) for c, _ in weighted), Decimal(0))
        points = sum((Decimal(str(c.weight)) * ratio for c, ratio in weighted), Decimal(0))
        calculations = []
        for criterion, answer, ratio in rows:
            scored = ratio is not None and criterion.answer_type != "flag"
            weight = Decimal(str(criterion.weight))
            calculations.append(
                CriterionCalculation(
                    criterion_id=criterion.id,
                    raw_value=answer.value,
                    applicability=("not_applicable" if ratio is None else "applicable"),
                    weight=criterion.weight,
                    normalized_value=format(ratio, "f") if scored else None,
                    earned_points=_display(weight * ratio) if scored else None,
                    effective_weight=(
                        _display(100 * weight / denominator, 4) if scored and denominator else None
                    ),
                    weighted_contribution=(
                        _display(100 * weight * ratio / denominator, 4)
                        if scored and denominator
                        else None
                    ),
                )
            )
        sections = []
        for section in checklist_version.sections:
            members = [(c, ratio) for c, ratio in weighted if c.section_id == section.id]
            section_weight = sum((Decimal(str(c.weight)) for c, _ in members), Decimal(0))
            section_points = sum(
                (Decimal(str(c.weight)) * ratio for c, ratio in members), Decimal(0)
            )
            sections.append(
                SectionCalculation(
                    section_id=section.id,
                    applicable_weight=_display(section_weight),
                    weighted_contribution=(
                        _display(100 * section_points / denominator, 4) if denominator else None
                    ),
                    local_percent=(
                        _display(100 * section_points / section_weight) if section_weight else None
                    ),
                )
            )
        return CalculatedResult(
            criteria=calculations,
            sections=sections,
            applicable_weight=_display(denominator),
            earned_points=_display(points),
            total=_display(100 * points / denominator) if denominator else None,
            gross_error=any(
                answer.value is True
                for criterion, answer, _ in rows
                if criterion.answer_type == "flag"
            ),
        )
