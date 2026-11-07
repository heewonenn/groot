"""Check the team's official DEV export against its local original cards and evidence."""

import argparse
import json
import re
from collections import Counter
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from backend.app import submission
from backend.app.services.submission_validation import OFFICIAL_CRITERION_IDS
from backend.app.team_data import ROOT, connect, get_conversation, list_conversations

DEFAULT_ROOTS = ROOT / "data" / "dev-delivery-roots.json"
RESULT = ROOT / "results" / "dev" / "results.json"
PRIVATE = re.compile(
    r"AIza[\w-]{35}|sk-or-[\w-]{20,}|https?://\S+|[\w.+%-]+@[\w.-]+\.[a-z]{2,}"
    r"|\bKZ\d{2}[A-Z0-9]{16}\b|(?<!\w)(?:\+?7[ -]?(?:\(\d{3}\)|\d{3})"
    r"[ -]?\d{3}[ -]?\d{2}[ -]?\d{2})(?!\w)|(?<!\d)\d{10,}(?!\d)",
    re.I,
)


def inputs(path: Path) -> tuple[dict, list[dict]]:
    roots = submission.read_file(path)
    source = [get_conversation("DEV", row["conversation_id"]) for row in list_conversations("DEV")]
    submission.validate_dataset(source, "dev")
    if (
        not isinstance(roots, dict)
        or set(roots) != {r["conversation_id"] for r in source}
        or not all(isinstance(job, str) and job for job in roots.values())
        or len(set(roots.values())) != len(roots)
    ):
        raise ValueError("Expected one distinct root job per DEV conversation in the manifest")
    return roots, source


def audit(document: dict, roots: dict, source: list[dict]) -> None:
    submission.check_document(document, source, "dev")
    if document["model"]["mode"] != "external_api":
        raise ValueError("Unexpected evaluation mode")
    by_id = {c["conversation_id"]: c for c in source}
    models = set(document["model"]["name"].split(", "))
    with closing(connect()) as db:
        db.execute("BEGIN")
        children = dict(
            db.execute("SELECT parent_id,id FROM cqa_batch_jobs WHERE parent_id IS NOT NULL")
        )
        for row in document["results"]:
            cid = row["conversation_id"]
            job = roots[cid]
            seen = set()
            while job in children:
                if job in seen:
                    raise ValueError("Cycle in retry history")
                seen.add(job)
                job = children[job]
            item = db.execute(
                """SELECT i.status,i.card,j.checklist_version_id,j.group_id FROM cqa_batch_items i
                   JOIN cqa_batch_jobs j ON i.job_id=j.id WHERE j.id=? AND i.conversation_id=?""",
                (job, cid),
            ).fetchone()
            conv = by_id[cid]
            if (
                not item
                or item["group_id"] != "DEV"
                or item["checklist_version_id"] != submission.VERSIONS[conv["channel"]]
            ):
                raise ValueError(f"Job or checklist mismatch for {cid}")
            if row["status"] == "error":
                if item["status"] not in {"partial", "failed", "interrupted"}:
                    raise ValueError(f"No recorded failure for {cid}")
                continue
            card = json.loads(item["card"], parse_float=Decimal)
            if (
                item["status"] != "completed"
                or card["generation"]["mode"] != "live"
                or card["generation"]["model_id"] not in models
            ):
                raise ValueError(f"No matching live score for {cid}")
            check_answers(row, card, conv)
    print("DEV:", dict(Counter(row["status"] for row in document["results"])), flush=True)


def check_answers(row: dict, card: dict, conv: dict) -> None:
    cid = conv["conversation_id"]
    positions = {msg["message_id"]: i for i, msg in enumerate(conv["transcript"], 1)}
    channel = "call" if conv["channel"] == "voice call" else "chat"
    rules = json.loads(
        (ROOT / f"backend/app/resources/checklists/{channel}-v1.json").read_bytes(),
        parse_float=Decimal,
    )["criteria"]
    answers = {a["criterion_id"]: a for a in card["model_result"]["criteria"]}
    exported = {a["criterion_id"]: a for a in row["criteria"]}
    points = denominator = Decimal(0)
    critical = False
    for rule in rules:
        answer = answers[rule["id"]]
        score = exported[OFFICIAL_CRITERION_IDS[rule["id"]]]
        value = score["value"]
        if (
            value != answer["value"]
            or isinstance(value, bool) != isinstance(answer["value"], bool)
            or PRIVATE.search(score["rationale"])
        ):
            raise ValueError(f"Answer or publication check failed for {cid}, {rule['id']}")
        turns = set()
        for ref in answer["evidence"]:
            if (ref["group_id"], ref["conversation_id"]) != ("DEV", cid):
                raise ValueError(f"Evidence belongs to another conversation: {cid}")
            pos = positions[ref["message_id"]]
            turns.add(pos)
            if span := ref.get("span"):
                first, last = positions[span["start_message_id"]], positions[span["end_message_id"]]
                if not first <= pos <= last:
                    raise ValueError(f"Invalid span for {cid}")
                turns.update(range(first, last + 1))
        if not turns or sorted(turns) != score["evidence_turns"]:
            raise ValueError(f"Evidence turns differ for {cid}, {rule['id']}")
        if value is None:
            if not rule["allow_na"] or answer["applicability"] != "not_applicable":
                raise ValueError(f"Invalid N/A for {cid}")
        elif rule["answer_type"] == "flag":
            critical = value
        else:
            ratio = (
                int(value)
                if rule["answer_type"] == "boolean"
                else next(
                    level["normalized_value"] for level in rule["levels"] if level["value"] == value
                )
            )
            denominator += rule["weight"]
            points += Decimal(str(ratio)) * rule["weight"]
    total = (100 * points / denominator).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    if (
        total != Decimal(str(row["score"]["final_score"]))
        or critical != row["score"]["critical_flag"]
    ):
        raise ValueError(f"Score mismatch for {cid}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=Path, default=DEFAULT_ROOTS)
    opts = parser.parse_args()
    audit(submission.read_file(RESULT), *inputs(opts.roots))
