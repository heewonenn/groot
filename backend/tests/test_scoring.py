"""Synthetic arithmetic and validation checks, not model-quality measurements."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.schemas.checklists import ChecklistVersion, ScoringAnswer
from backend.app.services.scoring import ChecklistError, calculate

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "backend/app/resources/checklists/chat-v1.json").read_bytes())
CASES = json.loads((ROOT / "docs/contracts/examples/scoring-cases.json").read_bytes())["cases"]


def answers(values: dict[str, bool | int | None]) -> list[ScoringAnswer]:
    return [
        ScoringAnswer(
            criterion_id=key, value=value,
            applicability="not_applicable" if value is None else "applicable",
        )
        for key, value in values.items()
    ]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_shared_arithmetic_vectors(case):
    content = copy.deepcopy(SEED)
    if case["test_only_criterion_subset"]:
        content["criteria"] = [
            c for c in content["criteria"] if c["id"] in case["test_only_criterion_subset"]
        ]
    version = ChecklistVersion.model_validate(content)
    if "error" in case["expected"]:
        with pytest.raises(ChecklistError) as error:
            calculate(version, answers(case["answers"]))
        assert error.value.code == case["expected"]["error"]
    else:
        assert calculate(version, answers(case["answers"])).model_dump() == case["expected"]


def test_missing_duplicate_and_unresolved_answers_do_not_publish_a_total():
    version = ChecklistVersion.model_validate(SEED)
    values = answers(CASES[1]["answers"])
    for invalid in (values[:-1], [*values, values[0]]):
        with pytest.raises(ChecklistError, match="ровно один раз"):
            calculate(version, invalid)
    values[0] = values[0].model_copy(update={"decision_status": "unresolved"})
    with pytest.raises(ChecklistError) as error:
        calculate(version, values)
    assert error.value.code == "unresolved_answer"


@pytest.mark.parametrize("mutation", ["duplicate", "section", "na", "scale", "flag", "weight"])
def test_invalid_rule_configuration(mutation):
    data = copy.deepcopy(SEED)
    if mutation == "duplicate":
        data["criteria"][1]["id"] = "01"
    elif mutation == "section":
        data["criteria"][0]["section_id"] = "missing"
    elif mutation == "na":
        data["criteria"][0]["allow_na"] = True
    elif mutation == "scale":
        data["criteria"][3]["levels"][1]["value"] = 0
    elif mutation == "flag":
        data["criteria"][7]["weight"] = 10
    else:
        data["criteria"][0]["weight"] = True
    with pytest.raises(ValidationError):
        ChecklistVersion.model_validate(data)


def test_custom_nonzero_minimum_scale_uses_saved_contribution():
    data = copy.deepcopy(SEED)
    criterion = data["criteria"][3]
    criterion["levels"] = [
        {"value": 1, "description": "Минимум", "normalized_value": "0"},
        {"value": 3, "description": "Частично", "normalized_value": "0.6"},
        {"value": 5, "description": "Максимум", "normalized_value": "1"},
    ]
    data["criteria"] = [criterion]
    result = calculate(ChecklistVersion.model_validate(data), answers({"04": 3}))
    assert result.total == "60.00"
    assert result.criteria[0].earned_points == "12.00"


def test_all_maximum_and_minimum_answers():
    version = ChecklistVersion.model_validate(SEED)
    for maximum, expected in [(True, "100.00"), (False, "0.00")]:
        values = {
            c.id: (c.levels[-1].value if maximum else c.levels[0].value)
            if c.answer_type == "scale" else (maximum if c.answer_type == "boolean" else False)
            for c in version.criteria
        }
        assert calculate(version, answers(values)).total == expected
