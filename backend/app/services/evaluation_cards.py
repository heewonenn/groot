"""Build team-v1 cards from model answers and the saved participant-1 rule snapshot."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from backend.app.schemas.checklists import ChecklistVersion, ScoringAnswer
from backend.app.services.scoring import calculate

SCHEMA = Path(__file__).resolve().parents[3] / "docs/contracts/cqa-team-v1.schema.json"


@lru_cache(maxsize=3)
def _validator(kind: str) -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_bytes())
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{kind}"})


def _check_evidence(evidence: list[dict], conversation: dict, positions: dict[str, int]) -> None:
    for ref in evidence:
        if (ref["group_id"], ref["conversation_id"]) != (
            conversation["group_id"],
            conversation["conversation_id"],
        ) or ref["message_id"] not in positions:
            raise ValueError("Evidence outside conversation")
        span = ref.get("span")
        if span:
            start = positions.get(span["start_message_id"])
            end = positions.get(span["end_message_id"])
            if start is None or end is None or start > end:
                raise ValueError("Invalid evidence range")


def build_card(
    evaluation_id: str, conversation: dict, checklist: ChecklistVersion, model_result: dict
) -> dict:
    _validator("model_result").validate(model_result)
    identity = {
        "group_id": conversation["group_id"],
        "conversation_id": conversation["conversation_id"],
        "evaluation_id": evaluation_id,
        "checklist_id": checklist.checklist_id,
        "checklist_version_id": checklist.checklist_version_id,
    }
    if any(model_result[key] != value for key, value in identity.items()):
        raise ValueError("Model result belongs to a different evaluation")
    rules = {criterion.id: criterion for criterion in checklist.criteria}
    answers = model_result["criteria"]
    if len(answers) != len(rules) or {answer["criterion_id"] for answer in answers} != rules.keys():
        raise ValueError("Missing, unknown or duplicate criterion")
    positions = {m["message_id"]: i for i, m in enumerate(conversation["transcript"])}
    resolved = []
    for answer in answers:
        rule = rules[answer["criterion_id"]]
        _check_evidence(answer["evidence"], conversation, positions)
        if not answer["reason"].strip():
            raise ValueError("No explanation")
        if answer["decision_status"] == "unresolved":
            if answer["applicability"] != "undetermined" or answer["value"] is not None:
                raise ValueError("Invalid unresolved answer")
            continue
        if rule.evidence_required and not answer["evidence"]:
            raise ValueError("Missing evidence")
        if answer["applicability"] == "not_applicable" and not (answer["na_reason"] or "").strip():
            raise ValueError("Missing N/A justification")
        resolved.append(ScoringAnswer(**{key: answer[key] for key in ScoringAnswer.model_fields}))
    partial = len(resolved) != len(answers)
    if partial:
        # Validate any resolved values with exactly the same rules. Partial answers have no total.
        if resolved:
            subset = [rules[answer.criterion_id] for answer in resolved]
            calculate(checklist.model_copy(update={"criteria": subset}), resolved)
        calculated = None
    else:
        calculated = calculate(checklist, resolved).model_dump(mode="json")
    generation = model_result["generation"]
    if (
        generation["is_ground_truth"]
        or generation["mode"] == "demo"
        and generation["use_for_model_quality"]
    ):
        raise ValueError("Invalid automatic result provenance")
    card = {
        **identity,
        "kind": "evaluation_response",
        "schema_version": "1.0",
        "status": "partial" if partial else "completed",
        "generation": generation,
        "model_result": model_result,
        "original_result": calculated,
        "final_result": calculated,
        "review_history": [],
    }
    _validator("evaluation_response").validate(card)
    return card


def reviewed_card(
    card: dict, decision: dict, conversation: dict, version: ChecklistVersion
) -> dict:
    """Calculate a team-v1 review against its originally saved rules."""
    _validator("review_decision").validate(decision)
    if (
        card["status"] != "completed"
        or card["checklist_version_id"] != version.checklist_version_id
    ):
        raise ValueError("A completed card and its original checklist version are required")
    if decision["checklist_version_id"] != version.checklist_version_id:
        raise ValueError("The decision references a different checklist version")
    if not decision["comment"].strip() or not decision["reason"].strip():
        raise ValueError("An appeal comment and review reason are required")
    positions = {m["message_id"]: i for i, m in enumerate(conversation["transcript"])}
    _check_evidence(decision["evidence"], conversation, positions)
    previous = {r["criterion_id"]: r["raw_value"] for r in card["final_result"]["criteria"]}
    cid = decision["criterion_id"]
    if cid not in previous:
        raise ValueError("Unknown criterion")
    old, new = decision["original_value"], decision["new_value"]
    if old != previous[cid] or isinstance(old, bool) != isinstance(previous[cid], bool):
        raise ValueError("Decision references another starting value")
    changed = (old != new) or (isinstance(old, bool) != isinstance(new, bool))
    if (decision["decision"] == "changed") != changed:
        raise ValueError("Action differs from the specified change")
    previous[cid] = new
    answers = [
        ScoringAnswer(
            criterion_id=key,
            value=value,
            applicability="not_applicable" if value is None else "applicable",
        )
        for key, value in previous.items()
    ]
    reviewed = {
        **card,
        "final_result": calculate(version, answers).model_dump(mode="json"),
        "review_history": [*card["review_history"], decision],
    }
    _validator("evaluation_response").validate(reviewed)
    return reviewed


def export_card(card: dict) -> dict:
    """A score export, not a full card: omit arbitrary free text and quotes by default."""
    result = {
        k: card[k]
        for k in (
            "evaluation_id",
            "group_id",
            "conversation_id",
            "checklist_id",
            "checklist_version_id",
            "status",
            "original_result",
            "final_result",
        )
    }
    unsafe_text = {"note", "reason", "na_reason", "review_reasons", "comment"}

    def project(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: project(v) for k, v in obj.items() if k not in unsafe_text}
        if isinstance(obj, list):
            return [project(v) for v in obj]
        if isinstance(obj, str) and ("://" in obj or "sig=" in obj.lower()):
            return None
        return obj

    result.update({k: card[k] for k in ("generation", "model_result", "review_history")})
    return project(result)
