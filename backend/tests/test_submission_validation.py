from copy import deepcopy

import pytest

from backend.app.services.submission_validation import OFFICIAL_CRITERION_IDS, validate_submission


def example(split: str = "prod") -> tuple[dict, list[dict]]:
    ids = [f"synthetic-{i}" for i in range(10 if split == "prod" else 65)]
    conversations = [
        {
            "conversation_id": cid,
            "channel": "voice call" if i >= 60 else "agent chat",
            "transcript": [{"speaker": "bot"}, {"speaker": "system"}, {"speaker": "operator"}],
        }
        for i, cid in enumerate(ids)
    ]
    answers = [True, None, None, 15, True, 7, True, False]
    criteria = [
        {"criterion_id": code, "value": value, "evidence_turns": [1, 3], "rationale": "Основание."}
        for code, value in zip(OFFICIAL_CRITERION_IDS.values(), answers, strict=True)
    ]
    results = [
        {"conversation_id": cid, "status": "ok", "score": {"final_score": 85.38,
         "critical_flag": False}, "criteria": deepcopy(criteria)}
        for cid in ids
    ]
    return {
        "schema_version": "1.0", "team_name": "Synthetic test", "model": {
            "mode": "local", "name": "synthetic-model",
        }, "results": results,
    }, conversations


@pytest.mark.parametrize("split", ["dev", "prod"])
def test_accepts_exact_dataset_and_genuine_error(split):
    payload, source = example(split)
    payload["results"][0] = {
        "conversation_id": source[0]["conversation_id"], "status": "error",
        "criteria": [], "score": None, "error": "Не удалось получить ответ модели.",
    }
    # Neither the order of conversations nor the order of criteria is prescribed.
    payload["results"][1]["criteria"].reverse()
    validate_submission(payload, list(reversed(source)), split)


def test_half_up_and_separate_critical_flag():
    data, source = example()
    row = data["results"][0]
    row["criteria"][1]["value"] = True
    row["criteria"][7]["value"] = True
    row["score"] = {"final_score": 88.13, "critical_flag": True}
    validate_submission(data, source, "prod")
    row["score"]["final_score"] = 88.12
    with pytest.raises(ValueError, match="final_score"):
        validate_submission(data, source, "prod")
    row["score"] = {"final_score": 88.13, "critical_flag": False}
    with pytest.raises(ValueError, match="critical_flag"):
        validate_submission(data, source, "prod")


@pytest.mark.parametrize(("index", "field", "invalid"), [
    (0, "value", 1), (0, "value", None), (3, "value", True), (3, "value", 7),
    (5, "value", None), (5, "value", 8), (7, "value", 0), (1, "value", "true"),
    (5, "evidence_turns", [0]), (5, "evidence_turns", [4]), (0, "evidence_turns", []),
    (0, "evidence_turns", [True]), (0, "evidence_turns", [2.0]), (0, "rationale", "   "),
    (0, "criterion_id", "empathy"),
])
def test_rejects_wrong_values_evidence_or_ids(index, field, invalid):
    payload, source = example()
    payload["results"][0]["criteria"][index][field] = invalid
    with pytest.raises(ValueError):
        validate_submission(payload, source, "prod")


@pytest.mark.parametrize("problem", [
    "wrong_case", "duplicate_result", "missing_result", "extra_result", "duplicate_source",
    "missing_source", "prod_has_call", "dev_wrong_mix", "missing_criteria", "wrong_score_type",
    "string_score", "flag_type", "no_model", "wrong_mode", "error_with_scores", "no_error_reason",
    "empty_team", "unexpected_key", "wrong_version",
])
def test_rejects_incomplete_or_nonofficial_shape(problem):
    split = "dev" if problem == "dev_wrong_mix" else "prod"
    data, source = example(split)
    row = data["results"][0]
    match problem:
        case "wrong_case":
            row["conversation_id"] = row["conversation_id"].upper()
        case "duplicate_result":
            data["results"][1] = row
        case "missing_result":
            data["results"].pop()
        case "extra_result":
            data["results"].append(row)
        case "duplicate_source":
            source[1] = source[0]
        case "missing_source":
            source.pop()
        case "prod_has_call":
            source[0]["channel"] = "voice call"
        case "dev_wrong_mix":
            source[0]["channel"] = "voice call"
        case "missing_criteria":
            row["criteria"].pop()
        case "wrong_score_type":
            row["score"]["final_score"] = True
        case "string_score":
            row["score"]["final_score"] = "85.38"
        case "flag_type":
            row["score"]["critical_flag"] = 0
        case "no_model":
            data["model"]["name"] = " "
        case "wrong_mode":
            data["model"]["mode"] = "demo"
        case "error_with_scores":
            row.update(status="error", error="таймаут")
        case "no_error_reason":
            row.update(status="error", criteria=[], score=None, error=" ")
        case "empty_team":
            data["team_name"] = " "
        case "unexpected_key":
            row["private_data"] = "confidential-sample"
        case "wrong_version":
            data["schema_version"] = 1.0
    with pytest.raises(ValueError) as exc:
        validate_submission(data, source, split)
    assert "confidential-sample" not in str(exc.value)
