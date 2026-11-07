"""Offline human-review templates and metrics; never label model output as ground truth.

python -m backend.app.evaluation.quality prepare --group DEV --checklist freedom-chat@1 --out ...
python -m backend.app.evaluation.quality measure --labels ... --out ...
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from backend.app.api.checklists import get_repository
from backend.app.evaluation.contract import digest
from backend.app.evaluation.metrics import Metrics
from backend.app.evaluation.store import RunStore
from backend.app.schemas.checklists import ChecklistVersion, ScoringAnswer
from backend.app.services.scoring import ChecklistError, calculate
from backend.app.team_data import get_conversation, list_conversations


def review_template(group: str, checklist_id: str) -> dict:
    version = get_repository().get_version(checklist_id)
    conversations = list_conversations(group)
    cases = []
    for summary in conversations:
        if summary["channel"] not in version.channels:
            continue
        conv = get_conversation(group, summary["conversation_id"])
        safe = {
            k: conv[k]
            for k in (
                "group_id",
                "conversation_id",
                "language",
                "channel",
                "transcript",
            )
        }
        # Hash whole conversation identity, not individual messages. No fitting is performed.
        holdout = int(digest([group, conv["conversation_id"]])[:8], 16) % 5 == 0
        cases.append(
            {
                **safe,
                "split": "heldout" if holdout else "development",
                "input_hash": digest(safe),
                "checklist_version_id": version.checklist_version_id,
                "checklist_hash": digest(version.model_dump(mode="json")),
                "evaluation_id": None,
                "human_reviewed": False,
                "reviewer": None,
                "criteria": [
                    {
                        "criterion_id": c.id,
                        "applicability": None,
                        "value": None,
                        "evidence_ids": [],
                        "reason": None,
                        "operator_action_verified": False,
                        "evidence_support_verified": False,
                        "na_verified": False,
                    }
                    for c in version.criteria
                ],
            }
        )
    return {
        "kind": "human_review_template",
        "is_ground_truth": False,
        "note": "Private transcripts. Fill independently before inspecting model answers. "
        "Heldout is frozen by conversation ID; do not tune on it. "
        "Group/time duplicates still require manual leakage review.",
        "checklist": version.model_dump(mode="json"),
        "cases": cases,
    }


def measure(labels: dict, store: RunStore, split: str = "heldout") -> dict:
    if labels.get("is_ground_truth") is not True:
        raise ValueError("human_ground_truth_not_confirmed")
    counters = defaultdict(Metrics)
    seen = set()
    for case in labels["cases"]:
        if case["split"] != split:
            continue
        identity = case["group_id"], case["conversation_id"], case["checklist_version_id"]
        if identity in seen:
            raise ValueError("duplicate_label_case")
        seen.add(identity)
        if case.get("human_reviewed") is not True or not (case.get("reviewer") or "").strip():
            raise ValueError("unreviewed_case")
        run = store.get(case["evaluation_id"])
        if run is None or run["snapshot"] is None:
            raise ValueError("prediction_missing")
        result, metadata = run["result"], run["metadata"]
        if metadata.get("generation_mode") != "live" or (
            result
            and (
                result["generation"]["mode"] not in {"live", "cache"}
                or not result["generation"]["use_for_model_quality"]
            )
        ):
            raise ValueError("synthetic_prediction_excluded")
        payload = run["snapshot"]["payload"]
        conv, checklist = payload["conversation"], payload["checklist"]
        if (
            identity
            != (conv["group_id"], conv["conversation_id"], checklist["checklist_version_id"])
            or case["input_hash"] != digest(conv)
            or case["checklist_hash"] != digest(checklist)
        ):
            raise ValueError("label_prediction_snapshot_mismatch")
        version = ChecklistVersion.model_validate(checklist)
        rules = {r.id: r for r in version.criteria}
        if (
            len(case["criteria"]) != len(rules)
            or {a["criterion_id"] for a in case["criteria"]} != rules.keys()
        ):
            raise ValueError("label_criteria_mismatch")
        try:
            truth = calculate(
                version,
                [
                    ScoringAnswer(
                        criterion_id=a["criterion_id"],
                        value=a["value"],
                        applicability=a["applicability"],
                    )
                    for a in case["criteria"]
                ],
            )
        except ValueError, ChecklistError:
            raise ValueError("label_rules_invalid") from None
        truth_normalized = {a.criterion_id: a.normalized_value for a in truth.criteria}
        predictions = {a["criterion_id"]: a for a in result["criteria"]} if result else {}
        ids = {m["message_id"] for m in conv["transcript"]}
        groups = ("overall", "language:" + conv["language"], "channel:" + conv["channel"])
        for label in case["criteria"]:
            if (
                not (label.get("reason") or "").strip()
                or not label.get("evidence_ids")
                or not set(label["evidence_ids"]) <= ids
                or not all(
                    label.get(k) is True
                    for k in (
                        "operator_action_verified",
                        "evidence_support_verified",
                        "na_verified",
                    )
                )
            ):
                raise ValueError("incomplete_human_label")
            cid = label["criterion_id"]
            prediction = predictions.get(cid)
            normalized_error = None
            if (
                prediction
                and prediction["decision_status"] == "evaluated"
                and prediction["applicability"] == label["applicability"] == "applicable"
                and rules[cid].answer_type == "scale"
            ):
                # Reuse participant 1's calculator, including custom normalized levels.
                calculated = calculate(
                    version.model_copy(update={"criteria": [rules[cid]]}),
                    [ScoringAnswer(**{k: prediction[k] for k in ScoringAnswer.model_fields})],
                )
                normalized_error = abs(
                    float(calculated.criteria[0].normalized_value) - float(truth_normalized[cid])
                )
            for key in (*groups, "criterion:" + cid):
                counters[key].criterion(label, prediction, rules[cid], normalized_error)
        completed = result is not None and all(
            a["decision_status"] == "evaluated" for a in result["criteria"]
        )
        total_error = None
        if completed:
            calculated = calculate(
                version,
                [
                    ScoringAnswer(**{k: a[k] for k in ScoringAnswer.model_fields})
                    for a in result["criteria"]
                ],
            )
            if truth.total is not None and calculated.total is not None:
                total_error = abs(float(truth.total) - float(calculated.total))
        for key in groups:
            counters[key].case(metadata, completed, total_error)
    return {
        "split": split,
        "conversations": len(seen),
        "metrics": {k: v.report() for k, v in sorted(counters.items())},
        "limitations": "Model predictions before appeals. Failed/unresolved runs remain in "
        "coverage and all-labeled accuracy. MAE uses only valid pairs and reports "
        "their count. Semantic evidence needs separate human review. Empty splits "
        "have no metrics. DEV has no KZ/EN voice calls.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--group", required=True)
    prepare.add_argument("--checklist", required=True)
    score = commands.add_parser("measure")
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--split", choices=["heldout", "development"], default="heldout")
    for child in (prepare, score):
        child.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = review_template(args.group, args.checklist)
    else:
        result = measure(
            json.loads(args.labels.read_text(encoding="utf-8")), RunStore(), args.split
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite human annotations or a previously saved quality result by accident.
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
