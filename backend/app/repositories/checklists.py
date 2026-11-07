"""SQLite storage for participant 1's checklists and immutable rule versions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.schemas.checklists import (
    ChecklistContent,
    ChecklistDraft,
    ChecklistSummary,
    ChecklistVersion,
    Section,
)
from backend.app.services.scoring import ChecklistError

SEEDS = Path(__file__).resolve().parents[1] / "resources" / "checklists"


class ChecklistRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS cqa_checklists (
                id TEXT PRIMARY KEY, draft_payload TEXT NOT NULL, revision INTEGER NOT NULL,
                derived_from_version_id TEXT, created_at TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS cqa_checklist_versions (
                version_id TEXT PRIMARY KEY, checklist_id TEXT NOT NULL,
                version INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(checklist_id, version),
                FOREIGN KEY(checklist_id) REFERENCES cqa_checklists(id))"""
            )
        with self._connection(write=True) as connection:
            for filename in ("chat-v1.json", "call-v1.json"):
                seed = ChecklistVersion.model_validate_json((SEEDS / filename).read_bytes())
                if connection.execute(
                    "SELECT 1 FROM cqa_checklists WHERE id = ?", (seed.checklist_id,)
                ).fetchone():
                    continue
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "INSERT INTO cqa_checklists VALUES (?, ?, ?, ?, ?)",
                    (
                        seed.checklist_id, seed.content().model_dump_json(), 1,
                        seed.derived_from_version_id, now,
                    ),
                )
                self._insert_version(connection, seed, now)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record(connection: sqlite3.Connection, checklist_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM cqa_checklists WHERE id = ?", (checklist_id,)
        ).fetchone()
        if row is None:
            raise ChecklistError("checklist_not_found", "Чек-лист не найден.", 404)
        return row

    @staticmethod
    def _latest(connection: sqlite3.Connection, checklist_id: str) -> ChecklistVersion | None:
        row = connection.execute(
            "SELECT payload FROM cqa_checklist_versions WHERE checklist_id = ? "
            "ORDER BY version DESC LIMIT 1", (checklist_id,),
        ).fetchone()
        return ChecklistVersion.model_validate_json(row["payload"]) if row else None

    @staticmethod
    def _version(connection: sqlite3.Connection, version_id: str) -> ChecklistVersion:
        row = connection.execute(
            "SELECT payload FROM cqa_checklist_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise ChecklistError("version_not_found", "Сохранённая версия не найдена.", 404)
        return ChecklistVersion.model_validate_json(row["payload"])

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection, version: ChecklistVersion, created_at: str
    ) -> None:
        connection.execute(
            "INSERT INTO cqa_checklist_versions VALUES (?, ?, ?, ?, ?)",
            (
                version.checklist_version_id, version.checklist_id, version.version,
                version.model_dump_json(), created_at,
            ),
        )

    def _draft(self, connection: sqlite3.Connection, checklist_id: str) -> ChecklistDraft:
        row = self._record(connection, checklist_id)
        latest = self._latest(connection, checklist_id)
        return ChecklistDraft(
            checklist_id=checklist_id, revision=row["revision"],
            base_version_id=latest.checklist_version_id if latest else None,
            derived_from_version_id=row["derived_from_version_id"],
            content=ChecklistContent.model_validate_json(row["draft_payload"]),
        )

    def list_checklists(self) -> list[ChecklistSummary]:
        with self._connection() as connection:
            result = []
            for row in connection.execute("SELECT * FROM cqa_checklists ORDER BY created_at, id"):
                draft = ChecklistContent.model_validate_json(row["draft_payload"])
                latest = self._latest(connection, row["id"])
                result.append(ChecklistSummary(
                    checklist_id=row["id"], title=draft.title, channels=draft.channels,
                    draft_revision=row["revision"],
                    latest_version_id=latest.checklist_version_id if latest else None,
                    version_count=latest.version if latest else 0,
                    has_unpublished_changes=latest is None or draft != latest.content(),
                ))
            return result

    def get_draft(self, checklist_id: str) -> ChecklistDraft:
        with self._connection() as connection:
            return self._draft(connection, checklist_id)

    def get_version(self, version_id: str) -> ChecklistVersion:
        with self._connection() as connection:
            return self._version(connection, version_id)

    def list_versions(self, checklist_id: str) -> list[ChecklistVersion]:
        with self._connection() as connection:
            self._record(connection, checklist_id)
            rows = connection.execute(
                "SELECT payload FROM cqa_checklist_versions WHERE checklist_id = ? "
                "ORDER BY version DESC", (checklist_id,),
            ).fetchall()
            return [ChecklistVersion.model_validate_json(row["payload"]) for row in rows]

    def create(self, title: str, copy_from_version_id: str | None) -> ChecklistDraft:
        with self._connection(write=True) as connection:
            if copy_from_version_id:
                content = self._version(connection, copy_from_version_id).content()
                content.title = title
            else:
                content = ChecklistContent(
                    title=title, channels=["agent chat"],
                    sections=[Section(id="main", title="Критерии")],
                )
            checklist_id = "checklist-" + uuid4().hex[:16]
            connection.execute(
                "INSERT INTO cqa_checklists VALUES (?, ?, ?, ?, ?)",
                (
                    checklist_id, content.model_dump_json(), 1,
                    copy_from_version_id, datetime.now(UTC).isoformat(),
                ),
            )
            return self._draft(connection, checklist_id)

    @staticmethod
    def _check_revision(row: sqlite3.Row, expected_revision: int) -> None:
        if row["revision"] != expected_revision:
            raise ChecklistError(
                "draft_conflict",
                "Черновик уже изменён. Загрузите актуальную версию перед сохранением.",
                409,
            )

    def update_draft(
        self, checklist_id: str, expected_revision: int, content: ChecklistContent
    ) -> ChecklistDraft:
        with self._connection(write=True) as connection:
            row = self._record(connection, checklist_id)
            self._check_revision(row, expected_revision)
            if ChecklistContent.model_validate_json(row["draft_payload"]) != content:
                connection.execute(
                    "UPDATE cqa_checklists SET draft_payload = ?, revision = revision + 1 "
                    "WHERE id = ?", (content.model_dump_json(), checklist_id),
                )
            return self._draft(connection, checklist_id)

    def publish(self, checklist_id: str, expected_revision: int) -> ChecklistVersion:
        with self._connection(write=True) as connection:
            row = self._record(connection, checklist_id)
            self._check_revision(row, expected_revision)
            content = ChecklistContent.model_validate_json(row["draft_payload"])
            if not content.criteria:
                raise ChecklistError("empty_checklist", "Добавьте хотя бы один критерий.")
            latest = self._latest(connection, checklist_id)
            if latest and content == latest.content():
                return latest
            number = latest.version + 1 if latest else 1
            version = ChecklistVersion(
                **content.model_dump(), checklist_id=checklist_id,
                checklist_version_id=f"{checklist_id}@{number}", version=number,
                previous_version_id=latest.checklist_version_id if latest else None,
                derived_from_version_id=row["derived_from_version_id"],
            )
            self._insert_version(connection, version, datetime.now(UTC).isoformat())
            connection.execute(
                "UPDATE cqa_checklists SET revision = revision + 1 WHERE id = ?", (checklist_id,)
            )
            return version
