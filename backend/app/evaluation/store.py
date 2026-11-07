"""Private audit snapshots in the existing SQLite database, separate from team-v1 cards."""

import json
import sqlite3
from contextlib import closing

from backend.app.evaluation.contract import canonical
from backend.app.evaluation.provider import ModelError
from backend.app.team_data import connect

DDL = """CREATE TABLE IF NOT EXISTS cqa_model_runs (
evaluation_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
metadata TEXT NOT NULL, snapshot TEXT, result TEXT
);
CREATE INDEX IF NOT EXISTS cqa_model_runs_conversation
ON cqa_model_runs(group_id, conversation_id);"""


class RunStore:
    def __init__(self):
        with closing(connect()) as db, db:
            db.executescript(DDL)

    def recover(self) -> None:
        """Call once during single-process app startup, before any workers are started."""
        with closing(connect()) as db, db:
            rows = db.execute("SELECT evaluation_id,metadata FROM cqa_model_runs").fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"])
                if metadata["status"] == "running":
                    metadata.update(status="interrupted", error_code="server_restart")
                    db.execute(
                        "UPDATE cqa_model_runs SET metadata=? WHERE evaluation_id=?",
                        (canonical(metadata), row["evaluation_id"]),
                    )

    def begin(self, metadata: dict) -> None:
        with closing(connect()) as db, db:
            try:
                db.execute(
                    "INSERT INTO cqa_model_runs VALUES(?,?,?,?,NULL,NULL)",
                    (
                        metadata["evaluation_id"],
                        metadata["group_id"],
                        metadata["conversation_id"],
                        canonical(metadata),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ModelError("evaluation_id_already_used") from None

    def save(self, metadata: dict, snapshot: dict | None, result: dict | None = None) -> None:
        with closing(connect()) as db, db:
            db.execute(
                "UPDATE cqa_model_runs SET metadata=?,snapshot=?,result=? WHERE evaluation_id=?",
                (
                    canonical(metadata),
                    canonical(snapshot),
                    canonical(result),
                    metadata["evaluation_id"],
                ),
            )

    def get(self, evaluation_id: str) -> dict | None:
        with closing(connect()) as db:
            row = db.execute(
                "SELECT * FROM cqa_model_runs WHERE evaluation_id=?", (evaluation_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            key: json.loads(row[key]) if row[key] else None
            for key in ("metadata", "snapshot", "result")
        }

    def list(self, group: str, conversation: str) -> list[dict]:
        with closing(connect()) as db:
            rows = db.execute(
                "SELECT metadata FROM cqa_model_runs WHERE group_id=? AND conversation_id=? "
                "ORDER BY rowid DESC LIMIT 100",
                (group, conversation),
            ).fetchall()
        return [json.loads(row["metadata"]) for row in rows]
