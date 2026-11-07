"""Single-process, durable evaluation jobs over ZIP-compatible group/conversation IDs."""

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.app.api.checklists import get_repository
from backend.app.evaluation.provider import ModelError
from backend.app.schemas.checklists import ChecklistVersion
from backend.app.services.evaluation_cards import build_card, export_card, reviewed_card
from backend.app.services.scoring import ChecklistError
from backend.app.team_data import connect, get_conversation

# #2 provides a bounded model call: (evaluation_id, safe_conversation, saved_rules, settings).
Evaluator = Callable[[str, dict, dict, dict], dict]

DDL = """
CREATE TABLE IF NOT EXISTS cqa_batch_jobs (
    id TEXT PRIMARY KEY, parent_id TEXT UNIQUE, group_id TEXT NOT NULL,
    checklist_version_id TEXT NOT NULL, settings TEXT NOT NULL, created_at TEXT NOT NULL,
    request_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cqa_batch_items (
    job_id TEXT NOT NULL, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
    evaluation_id TEXT UNIQUE, card TEXT, error_code TEXT, started_at TEXT, finished_at TEXT,
    PRIMARY KEY(job_id, conversation_id)
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _same_key(db: sqlite3.Connection, key: str, digest: str) -> str | None:
    row = db.execute(
        "SELECT id, request_hash FROM cqa_batch_jobs WHERE request_key=?", (key,)
    ).fetchone()
    if row and row["request_hash"] != digest:
        raise HTTPException(409, "Ключ уже использован с другими параметрами.")
    return row["id"] if row else None


def _job(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM cqa_batch_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Задание не найдено.")
    return row


class JobRunner:
    def __init__(self, evaluator: Evaluator | None = None):
        self.evaluator = evaluator
        self.pool: ThreadPoolExecutor | None = None

    @property
    def available(self) -> bool:
        return self.evaluator is not None and self.pool is not None

    def start(self) -> None:
        workers = int(os.getenv("CQA_EVALUATION_WORKERS", "4"))
        if not 1 <= workers <= 8:
            raise ValueError("CQA_EVALUATION_WORKERS must be between 1 and 8")
        with closing(connect()) as db, db:
            db.executescript(DDL)
            db.execute(
                """UPDATE cqa_batch_items SET status='interrupted', error_code='server_restart',
                   finished_at=? WHERE status IN ('queued','running')""",
                (_now(),),
            )
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cqa-evaluation")

    def stop(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None

    def create(
        self,
        group: str,
        ids: list[str],
        version: str,
        settings: dict,
        key: str,
        parent: str | None = None,
    ) -> dict:
        digest = sha256(_json([group, ids, version, settings, parent]).encode()).hexdigest()
        with closing(connect()) as db:
            previous = _same_key(db, key, digest)
        if previous:
            return {"job_id": previous}
        if not self.available:
            raise HTTPException(503, "Оценщик модели не подключён.")
        try:
            rules = get_repository().get_version(version)
        except ChecklistError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        for cid in ids:
            if get_conversation(group, cid)["channel"] not in rules.channels:
                raise HTTPException(422, "Чек-лист не поддерживает канал выбранного диалога.")

        job_id = str(uuid4())
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = _same_key(db, key, digest)
            if previous:
                return {"job_id": previous}
            if (
                parent
                and db.execute(
                    "SELECT 1 FROM cqa_batch_jobs WHERE parent_id=?", (parent,)
                ).fetchone()
            ):
                raise HTTPException(409, "Повтор уже создан. Откройте дочернее задание.")
            db.execute(
                """INSERT INTO cqa_batch_jobs
                   (id,parent_id,group_id,checklist_version_id,settings,created_at,
                    request_key,request_hash) VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, parent, group, version, _json(settings), _now(), key, digest),
            )
            db.executemany(
                "INSERT INTO cqa_batch_items(job_id,conversation_id,status) VALUES (?,?,'queued')",
                [(job_id, cid) for cid in ids],
            )
        for cid in ids:
            self.pool.submit(self._run, job_id, group, cid, version, settings)
        return {"job_id": job_id}

    def _run(self, job_id: str, group: str, cid: str, version: str, settings: dict) -> None:
        with closing(connect()) as db, db:
            db.execute(
                """UPDATE cqa_batch_items SET status='running',started_at=?
                   WHERE job_id=? AND conversation_id=?""",
                (_now(), job_id, cid),
            )
        evaluation_id = str(uuid4())
        saved = error = None
        try:
            rules = get_repository().get_version(version)
            conv = get_conversation(group, cid)
            safe_conversation = {k: v for k, v in conv.items() if k != "audio_url"}
            result = self.evaluator(
                evaluation_id, safe_conversation, rules.model_dump(mode="json"), dict(settings)
            )
            card = build_card(evaluation_id, conv, rules, result)
            saved = _json(card)
            status = card["status"]
        except Exception as exc:
            # Raw provider failures can contain conversation text or signed URLs.
            status = "failed"
            error = "evaluation_timeout" if isinstance(exc, TimeoutError) else "evaluation_failed"
            if isinstance(exc, ModelError) and exc.code == "provider_rate_limit":
                error = "provider_rate_limit"
            evaluation_id = None
        with closing(connect()) as db, db:
            db.execute(
                """UPDATE cqa_batch_items SET status=?,evaluation_id=?,card=?,error_code=?,
                   finished_at=? WHERE job_id=? AND conversation_id=?""",
                (status, evaluation_id, saved, error, _now(), job_id, cid),
            )

    def retry(self, parent: str, key: str) -> dict:
        with closing(connect()) as db:
            job = _job(db, parent)
            items = db.execute(
                """SELECT conversation_id,status FROM cqa_batch_items
                   WHERE job_id=? ORDER BY rowid""",
                (parent,),
            ).fetchall()
        if any(item["status"] in ("running", "queued") for item in items):
            raise HTTPException(409, "Дождитесь завершения задания.")
        ids = [item["conversation_id"] for item in items if item["status"] != "completed"]
        if not ids:
            raise HTTPException(409, "Нет неуспешных записей для повтора.")
        return self.create(
            job["group_id"],
            ids,
            job["checklist_version_id"],
            json.loads(job["settings"]),
            key,
            parent,
        )


def _status(count: Counter) -> str:
    if count["queued"] == count.total():
        return "queued"
    if count["queued"] or count["running"]:
        return "running"
    if count["interrupted"]:
        return "interrupted"
    if count["failed"] == count.total():
        return "failed"
    return "partial" if count["failed"] or count["partial"] else "completed"


def get_job(job_id: str, *, export: bool = False) -> dict:
    with closing(connect()) as db, db:
        db.execute("BEGIN")
        job = _job(db, job_id)
        columns = "conversation_id,status,evaluation_id,error_code,started_at,finished_at"
        if export:
            columns += ",card"
        rows = db.execute(
            f"SELECT {columns} FROM cqa_batch_items WHERE job_id=? ORDER BY rowid", (job_id,)
        ).fetchall()
        child = db.execute("SELECT id FROM cqa_batch_jobs WHERE parent_id=?", (job_id,)).fetchone()
    items = []
    for row in rows:
        item = dict(row)
        if card := item.pop("card", None):
            item["evaluation"] = export_card(json.loads(card))
        items.append(item)
    count = Counter(item["status"] for item in items)
    return {
        "job_id": job_id,
        "parent_job_id": job["parent_id"],
        "retry_job_id": child and child["id"],
        "group_id": job["group_id"],
        "checklist_version_id": job["checklist_version_id"],
        "model_config": json.loads(job["settings"]),
        "created_at": job["created_at"],
        "status": _status(count),
        "total": len(items),
        "counts": {
            key: count[key]
            for key in ("queued", "running", "completed", "partial", "failed", "interrupted")
        },
        "items": items,
    }


def get_evaluation(evaluation_id: str) -> dict:
    with closing(connect()) as db:
        row = db.execute(
            "SELECT card FROM cqa_batch_items WHERE evaluation_id=?", (evaluation_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Оценка не найдена.")
    return json.loads(row["card"])


def store_review(db: sqlite3.Connection, evaluation_id: str, decision: dict) -> dict:
    """Use in the appeal service's BEGIN IMMEDIATE transaction with its status update."""
    if not db.in_transaction:
        raise RuntimeError("A caller-owned write transaction is required")
    row = db.execute(
        "SELECT card FROM cqa_batch_items WHERE evaluation_id=?", (evaluation_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Оценка не найдена.")
    card = json.loads(row["card"])
    for existing in card["review_history"]:
        if existing["appeal_id"] == decision.get("appeal_id"):
            if _json(existing) == _json(decision):
                return card
            raise HTTPException(409, "Для этой апелляции уже сохранено другое решение.")
    if decision.get("revision") != len(card["review_history"]) + 1:
        raise HTTPException(409, "Карточка изменилась. Обновите результат.")
    conversation = get_conversation(card["group_id"], card["conversation_id"])
    # A cold repository constructor seeds its tables with a separate write transaction.
    # Read the immutable snapshot on the caller's connection while it holds the lock.
    version_row = db.execute(
        "SELECT payload FROM cqa_checklist_versions WHERE version_id=?",
        (card["checklist_version_id"],),
    ).fetchone()
    if version_row is None:
        raise HTTPException(409, "Исходная версия чек-листа отсутствует.")
    rules = ChecklistVersion.model_validate_json(version_row["payload"])
    reviewed = reviewed_card(card, decision, conversation, rules)
    db.execute(
        "UPDATE cqa_batch_items SET card=? WHERE evaluation_id=?", (_json(reviewed), evaluation_id)
    )
    return reviewed
