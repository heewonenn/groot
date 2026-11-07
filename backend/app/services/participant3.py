"""Durable appeals over participant 4 cards, using participant 1's single calculator.

All review changes and retry receipts share the source SQLite transaction. The
original model answer and its status remain immutable, including partial answers.
"""

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.app.schemas.checklists import ChecklistVersion, ScoringAnswer
from backend.app.schemas.participant3 import OpenAppeal, ReviewRequest
from backend.app.services.evaluation_cards import _validator
from backend.app.services.evaluation_jobs import DDL as JOB_DDL
from backend.app.services.evaluation_jobs import store_review
from backend.app.services.scoring import ChecklistError, calculate
from backend.app.team_data import connect, get_conversation

DDL = """
CREATE TABLE IF NOT EXISTS cqa_p3_appeals (
    appeal_id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, criterion_id TEXT NOT NULL,
    comment TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, decision TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS cqa_p3_one_open_appeal
    ON cqa_p3_appeals(evaluation_id, criterion_id) WHERE status='open';
CREATE INDEX IF NOT EXISTS cqa_p3_appeal_evaluation ON cqa_p3_appeals(evaluation_id);
CREATE TABLE IF NOT EXISTS cqa_p3_receipts (
    request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def initialize() -> None:
    """Add tables only; no dataset or evaluation is generated implicitly."""
    with closing(connect()) as db:
        db.executescript(JOB_DDL + DDL)


def _card(db: sqlite3.Connection, evaluation_id: str) -> tuple[dict, sqlite3.Row]:
    row = db.execute(
        """SELECT i.card, i.job_id, i.started_at, i.finished_at, j.settings, j.created_at
           FROM cqa_batch_items i JOIN cqa_batch_jobs j ON j.id=i.job_id
           WHERE i.evaluation_id=?""", (evaluation_id,),
    ).fetchone()
    if row is None or not row["card"]:
        raise HTTPException(404, "Оценка не найдена.")
    return json.loads(row["card"]), row


def _rules(db: sqlite3.Connection, version_id: str) -> ChecklistVersion:
    row = db.execute(
        "SELECT payload FROM cqa_checklist_versions WHERE version_id=?", (version_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(409, "Исходная версия чек-листа отсутствует.")
    return ChecklistVersion.model_validate_json(row["payload"])


def _appeal(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["decision"] = json.loads(row["decision"]) if row["decision"] else None
    return result


def _appeals(db: sqlite3.Connection, evaluation_id: str) -> list[dict]:
    return [
        _appeal(row) for row in db.execute(
            "SELECT * FROM cqa_p3_appeals WHERE evaluation_id=? ORDER BY rowid", (evaluation_id,),
        )
    ]


def _final_answers(card: dict, appeals: list[dict]) -> list[dict]:
    answers = deepcopy((card["model_result"] or {}).get("criteria", []))
    by_id = {item["criterion_id"]: item for item in answers}
    saved = {item["appeal_id"]: item["decision"] for item in appeals if item["decision"]}
    for review in card["review_history"]:
        answer = by_id.get(review["criterion_id"])
        if answer is None:
            continue
        decision = saved.get(review["appeal_id"])
        value = review["new_value"]
        answer.update(
            value=value,
            decision_status="evaluated",
            applicability=(decision["applicability"] if decision else (
                "not_applicable" if value is None else "applicable"
            )),
            reason=review["reason"],
            na_reason=(decision["na_reason"] if decision else (
                review["reason"] if value is None else None
            )),
            evidence=(decision["evidence"] if decision else [
                {**ref, "kind": "context", "span": None} for ref in review["evidence"]
            ]),
            knowledge_references=[], review_reasons=[], analysis_scope=["transcript"],
        )
    return answers


def _wrapper(db: sqlite3.Connection, evaluation_id: str) -> dict:
    card, runtime = _card(db, evaluation_id)
    appeals = _appeals(db, evaluation_id)
    answers = _final_answers(card, appeals)
    resolved = bool(answers) and all(a["decision_status"] == "evaluated" for a in answers)
    final_status = "completed" if resolved else card["status"]
    return {
        "evaluation": card,
        "checklist": _rules(db, card["checklist_version_id"]).model_dump(mode="json"),
        "final_answers": answers,
        "revision": len(card["review_history"]),
        "final_status": final_status,
        "appeals": appeals,
        "runtime": {
            "job_id": runtime["job_id"],
            "started_at": runtime["started_at"], "finished_at": runtime["finished_at"],
            "model_config": json.loads(runtime["settings"]),
        },
    }


def get_card(evaluation_id: str) -> dict:
    initialize()
    with closing(connect()) as db, db:
        db.execute("BEGIN")
        return _wrapper(db, evaluation_id)


def list_evaluations(group_id: str | None, conversation_id: str | None) -> list[dict]:
    initialize()
    conditions = ["i.card IS NOT NULL"]
    values: list[str] = []
    if group_id is not None:
        conditions.append("j.group_id=?")
        values.append(group_id)
    if conversation_id is not None:
        conditions.append("i.conversation_id=?")
        values.append(conversation_id)
    with closing(connect()) as db, db:
        db.execute("BEGIN")
        rows = db.execute(
            """SELECT i.card, i.job_id, j.created_at FROM cqa_batch_items i
               JOIN cqa_batch_jobs j ON j.id=i.job_id WHERE """
            + " AND ".join(conditions) + " ORDER BY j.created_at DESC, i.rowid DESC", values,
        ).fetchall()
        result = []
        for row in rows:
            card = json.loads(row["card"])
            result.append({
                **{k: card[k] for k in (
                    "evaluation_id", "group_id", "conversation_id", "checklist_version_id",
                    "status", "generation",
                )},
                "revision": len(card["review_history"]),
                "original_total": (card["original_result"] or {}).get("total"),
                "final_total": (card["final_result"] or {}).get("total"),
                "job_id": row["job_id"], "created_at": row["created_at"],
            })
        return result


def list_appeals(status: str | None = None) -> list[dict]:
    initialize()
    with closing(connect()) as db:
        rows = db.execute(
            "SELECT * FROM cqa_p3_appeals" + (" WHERE status=?" if status else "")
            + " ORDER BY rowid DESC", (status,) if status else (),
        )
        return [_appeal(row) for row in rows]


def open_appeal(evaluation_id: str, payload: OpenAppeal) -> dict:
    initialize()
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        card, _ = _card(db, evaluation_id)
        if payload.criterion_id not in {
            a["criterion_id"] for a in (card["model_result"] or {}).get("criteria", [])
        }:
            raise HTTPException(422, "Критерий отсутствует в исходной оценке.")
        existing = db.execute(
            "SELECT * FROM cqa_p3_appeals WHERE evaluation_id=? "
            "AND criterion_id=? AND status='open'",
            (evaluation_id, payload.criterion_id),
        ).fetchone()
        if existing:
            return _appeal(existing)
        appeal = {
            "appeal_id": "appeal-" + uuid4().hex, "evaluation_id": evaluation_id,
            "criterion_id": payload.criterion_id, "comment": payload.comment,
            "status": "open", "created_at": _now(), "decision": None,
        }
        db.execute(
            "INSERT INTO cqa_p3_appeals VALUES (?,?,?,?,?,?,?)", tuple(appeal.values()),
        )
        return appeal


def _evidence(payload: ReviewRequest, card: dict, rule: Any) -> list[dict]:
    """Validate all links, then expand inclusive spans into native flat references."""
    conversation = get_conversation(card["group_id"], card["conversation_id"])
    messages = conversation["transcript"]
    positions = {m["message_id"]: index for index, m in enumerate(messages)}
    if rule.evidence_required and not payload.evidence:
        raise HTTPException(422, "Для критерия требуется доказательство из диалога.")
    flattened = {}
    for ref in payload.evidence:
        if (ref.group_id, ref.conversation_id) != (
            card["group_id"], card["conversation_id"],
        ) or ref.message_id not in positions:
            raise HTTPException(422, "Доказательство не принадлежит группе и диалогу оценки.")
        ids = [ref.message_id]
        if ref.span:
            start = positions.get(ref.span.start_message_id)
            end = positions.get(ref.span.end_message_id)
            if start is None or end is None or start > end:
                raise HTTPException(422, "Некорректный диапазон доказательства.")
            ids.extend(m["message_id"] for m in messages[start:end + 1])
        for mid in ids:
            flattened[mid] = {
                "group_id": ref.group_id, "conversation_id": ref.conversation_id, "message_id": mid,
            }
    return [flattened[mid] for mid in sorted(flattened, key=positions.__getitem__)]


def _validate_decision(
    payload: ReviewRequest, previous: dict, version: ChecklistVersion, criterion_id: str,
) -> Any:
    rule = next((c for c in version.criteria if c.id == criterion_id), None)
    if rule is None:
        raise HTTPException(409, "Критерий отсутствует в исходной версии чек-листа.")
    same_value = payload.value == previous["value"] and (
        isinstance(payload.value, bool) == isinstance(previous["value"], bool)
    )
    changed = not (
        same_value and payload.applicability == previous["applicability"]
        and previous["decision_status"] == "evaluated"
    )
    if (payload.action == "change") != changed:
        raise HTTPException(422, "Действие не соответствует изменению ответа.")
    if payload.applicability == "not_applicable":
        if not rule.allow_na or not rule.na_condition:
            raise HTTPException(422, "Исходная версия запрещает N/A для этого критерия.")
        if not payload.na_reason:
            raise HTTPException(422, "Укажите причину применимости N/A по исходному правилу.")
    elif payload.na_reason is not None:
        raise HTTPException(422, "Причина N/A допустима только для неприменимого критерия.")
    answer = ScoringAnswer(
        criterion_id=criterion_id, value=payload.value, applicability=payload.applicability,
    )
    # The same calculator checks value type, scale membership and the N/A representation.
    calculate(version.model_copy(update={"criteria": [rule]}), [answer])
    return rule


def save_decision(appeal_id: str, payload: ReviewRequest, key: str) -> dict:
    initialize()
    digest = sha256(_json([appeal_id, payload.model_dump(mode="json")]).encode()).hexdigest()
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        receipt = db.execute(
            "SELECT request_hash,response FROM cqa_p3_receipts WHERE request_key=?", (key,),
        ).fetchone()
        if receipt:
            if receipt["request_hash"] != digest:
                raise HTTPException(409, "Ключ уже использован для другого решения.")
            return json.loads(receipt["response"])
        row = db.execute(
            "SELECT * FROM cqa_p3_appeals WHERE appeal_id=?", (appeal_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Апелляция не найдена.")
        if row["status"] != "open":
            raise HTTPException(409, "Для апелляции уже сохранено решение.")
        card, _ = _card(db, row["evaluation_id"])
        if payload.expected_revision != len(card["review_history"]):
            raise HTTPException(409, "Карточка изменилась. Обновите её перед решением.")
        previous = next(
            a for a in _final_answers(card, _appeals(db, row["evaluation_id"]))
            if a["criterion_id"] == row["criterion_id"]
        )
        version = _rules(db, card["checklist_version_id"])
        try:
            rule = _validate_decision(payload, previous, version, row["criterion_id"])
            flattened = _evidence(payload, card, rule)
            native = {
                "appeal_id": appeal_id, "criterion_id": row["criterion_id"],
                "checklist_version_id": card["checklist_version_id"], "comment": row["comment"],
                "decision": "changed" if payload.action == "change" else "confirmed",
                "original_value": previous["value"], "new_value": payload.value,
                "reason": payload.reason, "revision": len(card["review_history"]) + 1,
                "evidence": flattened,
            }
            if card["status"] == "completed":
                store_review(db, row["evaluation_id"], native)
            else:
                if card["status"] != "partial":
                    raise HTTPException(422, "Для ошибочной оценки нужно повторить оценивание.")
                _validator("review_decision").validate(native)
                card["review_history"].append(native)
                answers = _final_answers(card, _appeals(db, row["evaluation_id"]))
                # Current saved metadata is written below; use this request for its final answer.
                current = next(a for a in answers if a["criterion_id"] == row["criterion_id"])
                current.update(
                    value=payload.value, applicability=payload.applicability,
                    decision_status="evaluated",
                )
                if all(a["decision_status"] == "evaluated" for a in answers):
                    card["final_result"] = calculate(version, [
                        ScoringAnswer(**{k: a[k] for k in ScoringAnswer.model_fields})
                        for a in answers
                    ]).model_dump(mode="json")
                _validator("evaluation_response").validate(card)
                db.execute(
                    "UPDATE cqa_batch_items SET card=? WHERE evaluation_id=?",
                    (_json(card), row["evaluation_id"]),
                )
        except ChecklistError as exc:
            raise HTTPException(exc.status_code, exc.message) from None
        except ValueError:
            raise HTTPException(422, "Решение не соответствует исходной версии оценки.") from None
        decision = {
            **payload.model_dump(mode="json", exclude={"expected_revision"}),
            "created_at": _now(), "previous_value": previous["value"],
            "previous_applicability": previous["applicability"],
            "previous_decision_status": previous["decision_status"],
        }
        db.execute(
            "UPDATE cqa_p3_appeals SET status='resolved', decision=? WHERE appeal_id=?",
            (_json(decision), appeal_id),
        )
        response = _wrapper(db, row["evaluation_id"])
        db.execute("INSERT INTO cqa_p3_receipts VALUES (?,?,?)", (key, digest, _json(response)))
        return response
