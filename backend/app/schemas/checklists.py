"""Participant 1 contracts: drafts, immutable versions and calculation inputs."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Title = Annotated[str, Field(min_length=1, max_length=160)]
Text = Annotated[str, Field(min_length=1, max_length=12000)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1_000_000, le=1_000_000)]
Channel = Literal["agent chat", "voice call"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Section(Contract):
    id: Identifier
    title: Title


class ScaleLevel(Contract):
    value: Number
    description: Text
    normalized_value: Annotated[
        Decimal, Field(ge=0, le=1, allow_inf_nan=False, max_digits=29, decimal_places=28)
    ]

    @field_validator("normalized_value")
    @classmethod
    def normalize_zero(cls, value: Decimal) -> Decimal:
        # Normalize on input/read so calculation results also never contain -0.
        return Decimal(0) if value.is_zero() else value

    @field_serializer("normalized_value", when_used="json")
    def serialize_normalized_value(self, value: Decimal) -> str:
        return format(value, "f")


class Criterion(Contract):
    id: Identifier
    title: Title
    section_id: Identifier
    instruction: Text
    answer_type: Literal["boolean", "scale", "flag"]
    weight: Annotated[float, Field(strict=True, ge=0, le=100_000, allow_inf_nan=False)]
    allow_na: StrictBool = False
    na_condition: Text | None = None
    levels: list[ScaleLevel] = Field(default_factory=list, max_length=30)
    evidence_required: StrictBool = True
    flag_when: StrictBool | None = None

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if self.allow_na != (self.na_condition is not None):
            raise ValueError("Разрешение N/A и его условие должны быть заданы вместе.")
        if self.answer_type == "flag":
            if self.weight != 0 or self.flag_when is not True:
                raise ValueError("Флаг нарушения имеет нулевой вес и flag_when=true.")
        elif self.weight <= 0 or self.flag_when is not None:
            raise ValueError("Оцениваемый критерий должен иметь положительный вес без flag_when.")
        if self.answer_type != "scale":
            if self.levels:
                raise ValueError("Ступени допустимы только для числовой шкалы.")
            return self
        if len(self.levels) < 2:
            raise ValueError("Шкала должна содержать хотя бы две ступени.")
        values = [level.value for level in self.levels]
        ratios = [level.normalized_value for level in self.levels]
        if values != sorted(set(values)):
            raise ValueError("Ступени должны быть уникальными и идти по возрастанию.")
        if ratios != sorted(ratios) or ratios[0] != 0 or ratios[-1] != 1:
            raise ValueError("Вклад ступеней должен возрастать от 0 до 1.")
        return self


class ScoringRules(Contract):
    total_max: Literal[100] = 100
    rounding: Literal["ROUND_HALF_UP"] = "ROUND_HALF_UP"
    decimal_places: Literal[2] = 2
    exclude_na_from_denominator: Literal[True] = True
    flag_affects_total: Literal[False] = False


class ChecklistContent(Contract):
    title: Title
    channels: list[Channel] = Field(min_length=1, max_length=2)
    source: Text = "Пользовательский чек-лист"
    sections: list[Section] = Field(min_length=1, max_length=20)
    criteria: list[Criterion] = Field(default_factory=list, max_length=100)
    scoring: ScoringRules = Field(default_factory=ScoringRules)

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        section_ids = [section.id for section in self.sections]
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Каналы не должны повторяться.")
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("ID секций не должны повторяться.")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("ID критериев не должны повторяться.")
        if any(criterion.section_id not in section_ids for criterion in self.criteria):
            raise ValueError("Каждый критерий должен относиться к существующей секции.")
        return self


class ChecklistVersion(ChecklistContent):
    kind: Literal["checklist_version"] = "checklist_version"
    schema_version: Literal["1.0"] = "1.0"
    checklist_id: Identifier
    checklist_version_id: Annotated[str, Field(min_length=1, max_length=100)]
    version: int = Field(ge=1, strict=True)
    previous_version_id: str | None = None
    derived_from_version_id: str | None = None

    @model_validator(mode="after")
    def validate_published_version(self) -> Self:
        if not self.criteria:
            raise ValueError("В сохранённой версии нужен хотя бы один критерий.")
        if self.checklist_version_id != f"{self.checklist_id}@{self.version}":
            raise ValueError("ID версии не соответствует чек-листу и номеру версии.")
        return self

    def content(self) -> ChecklistContent:
        return ChecklistContent.model_validate(
            self.model_dump(include=set(ChecklistContent.model_fields))
        )


class ChecklistDraft(Contract):
    checklist_id: str
    revision: int
    base_version_id: str | None
    derived_from_version_id: str | None
    content: ChecklistContent


class ChecklistSummary(Contract):
    checklist_id: str
    title: str
    channels: list[Channel]
    draft_revision: int
    latest_version_id: str | None
    version_count: int
    has_unpublished_changes: bool


class CreateChecklist(Contract):
    title: Title
    copy_from_version_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None


class UpdateDraft(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    content: ChecklistContent


class PublishVersion(Contract):
    expected_revision: int = Field(ge=1, strict=True)


class ScoringAnswer(Contract):
    criterion_id: Identifier
    value: StrictBool | Number | None
    applicability: Literal["applicable", "not_applicable", "undetermined"]
    decision_status: Literal["evaluated", "unresolved"] = "evaluated"


class CalculateRequest(Contract):
    answers: list[ScoringAnswer] = Field(min_length=1, max_length=100)


class CriterionCalculation(Contract):
    criterion_id: str
    raw_value: StrictBool | float | None
    applicability: Literal["applicable", "not_applicable"]
    weight: float
    normalized_value: str | None
    earned_points: str | None
    effective_weight: str | None
    weighted_contribution: str | None


class SectionCalculation(Contract):
    section_id: str
    applicable_weight: str
    weighted_contribution: str | None
    local_percent: str | None


class CalculatedResult(Contract):
    criteria: list[CriterionCalculation]
    sections: list[SectionCalculation]
    applicable_weight: str
    earned_points: str
    total: str | None
    gross_error: bool


class CalculationResponse(Contract):
    checklist_version_id: str
    result: CalculatedResult
