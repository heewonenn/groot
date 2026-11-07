"""Conversation API adapted from CQA-team.zip; ZIP routes and IDs are preserved."""

import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Response
from fastapi import Request as APIRequest
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, ValidationError


class DataRoute(APIRoute):
    def get_route_handler(self) -> Callable[[APIRequest], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: APIRequest) -> Response:
            try:
                return await original(request)
            except sqlite3.Error, OSError:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Хранилище диалогов временно недоступно."},
                )

        return handler


router = APIRouter(tags=["team-data"], route_class=DataRoute)
ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "data" / "team-api"
GROUPS = "/api/groups/{group_id}"
CONVERSATIONS = GROUPS + "/conversations"


class SourceMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    timestamp: str
    speaker: Literal["client", "operator", "bot", "system"]
    text: str


class SourceConversation(BaseModel):
    conversation_id: str
    channel: Literal["agent chat", "voice call"]
    language: Literal["RU", "KZ", "EN"]
    transcript: list[SourceMessage]
    audio_url: str | None


class EvidenceMessage(SourceMessage):
    message_id: str


class ConversationSummary(BaseModel):
    group_id: str
    conversation_id: str
    channel: str
    language: str
    message_count: int
    has_audio: bool


class ConversationDetail(ConversationSummary):
    audio_url: str | None
    transcript: list[EvidenceMessage]


class ImportResult(BaseModel):
    group_id: str
    total: int
    inserted: int
    unchanged: int


def source_url(group_id: str) -> str:
    url = os.environ.get("CQA_SOURCE_URL")
    if not url:
        try:
            url = (PRIVATE_DIR / "source_url.txt").read_text(encoding="utf-8-sig").strip()
        except OSError:
            raise HTTPException(503, "Не настроена ссылка на API организаторов.") from None
    try:
        parts = urlsplit(url.strip())
        is_local_http = parts.scheme == "http" and parts.hostname in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
        if not parts.hostname or (parts.scheme != "https" and not is_local_http):
            raise ValueError
    except ValueError:
        raise HTTPException(503, "Проверьте настройку ссылки на API организаторов.") from None

    # Copy the signed query byte-for-byte except for the requested group.
    query = [
        item
        for item in parts.query.split("&")
        if item and unquote_plus(item.partition("=")[0]) != "group_id"
    ]
    query.append("group_id=" + quote(group_id, safe=""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), ""))


def fetch_group(group_id: str) -> dict:
    url = source_url(group_id)
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as exc:
        raise HTTPException(502, f"API организаторов вернул HTTP {exc.code}.") from None
    except OSError, URLError, ValueError:
        raise HTTPException(502, "Не удалось получить JSON от API организаторов.") from None


def validate_group(group_id: str, envelope: dict) -> list[SourceConversation]:
    if (
        not isinstance(envelope, dict)
        or envelope.get("isSuccess") is not True
        or envelope.get("error")
    ):
        raise HTTPException(502, "API организаторов сообщил об ошибке.")
    batch = envelope.get("data")
    if not isinstance(batch, dict) or batch.get("dataset_group_id") != group_id:
        raise HTTPException(502, "Идентификатор группы в ответе отличается от запрошенного.")
    records = batch.get("records")
    total = batch.get("total_records")
    if not isinstance(records, list) or type(total) is not int or total != len(records):
        raise HTTPException(502, "Количество записей в ответе не совпало с total_records.")
    try:
        conversations = [SourceConversation.model_validate(record) for record in records]
    except ValidationError:
        raise HTTPException(
            502, "В полученном наборе нарушена структура диалога или реплики."
        ) from None
    ids = {conv.conversation_id for conv in conversations}
    if any(not cid.strip() for cid in ids) or len(ids) != len(conversations):
        raise HTTPException(502, "В наборе есть пустые или повторяющиеся conversation_id.")
    return conversations


def connect() -> sqlite3.Connection:
    path = Path(os.environ.get("CQA_DB_PATH") or ROOT / "data" / "cqa.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS conversations (
            group_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            language TEXT NOT NULL,
            transcript TEXT NOT NULL,
            audio_url TEXT,
            message_count INTEGER NOT NULL,
            PRIMARY KEY (group_id, conversation_id)
        );
    """)
    return db


@router.post(GROUPS + "/import", response_model=ImportResult)
def import_group(group_id: str) -> ImportResult:
    return store_group(group_id, validate_group(group_id, fetch_group(group_id)))


def store_group(group_id: str, conversations: list[SourceConversation]) -> ImportResult:
    """Persist an already validated snapshot with the ZIP conflict policy."""
    inserted = 0
    with closing(connect()) as db, db:
        # Serialize imports for a consistent read/compare/write transaction.
        db.execute("BEGIN IMMEDIATE")
        previous = {
            row["conversation_id"]: row
            for row in db.execute("SELECT * FROM conversations WHERE group_id = ?", (group_id,))
        }
        if previous.keys() - {item.conversation_id for item in conversations}:
            raise HTTPException(409, "В новой выгрузке отсутствует ранее импортированный диалог.")
        db.execute("INSERT OR IGNORE INTO groups VALUES (?)", (group_id,))
        for item in conversations:
            cid = item.conversation_id
            transcript = json.dumps(
                [message.model_dump() for message in item.transcript],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            old = previous.get(cid)
            if old is None:
                db.execute(
                    """INSERT INTO conversations
                       (group_id, conversation_id, channel, language,
                        transcript, audio_url, message_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        group_id,
                        cid,
                        item.channel,
                        item.language,
                        transcript,
                        item.audio_url,
                        len(item.transcript),
                    ),
                )
                inserted += 1
            elif (old["channel"], old["language"], old["transcript"]) != (
                item.channel,
                item.language,
                transcript,
            ):
                raise HTTPException(
                    409, f"Изменился ранее импортированный диалог {cid}. Группа не обновлена."
                )
            elif old["audio_url"] != item.audio_url:
                # Recording URLs may be refreshed without changing the transcript.
                db.execute(
                    "UPDATE conversations SET audio_url = ? "
                    "WHERE group_id = ? AND conversation_id = ?",
                    (item.audio_url, group_id, cid),
                )
    return ImportResult(
        group_id=group_id,
        total=len(conversations),
        inserted=inserted,
        unchanged=len(conversations) - inserted,
    )


META = "group_id, conversation_id, channel, language, message_count"


def metadata(row: sqlite3.Row) -> dict:
    return {
        "group_id": row["group_id"],
        "conversation_id": row["conversation_id"],
        "channel": row["channel"],
        "language": row["language"],
        "message_count": row["message_count"],
        "has_audio": bool(row["audio_url"]),
    }


@router.get(CONVERSATIONS, response_model=list[ConversationSummary])
def list_conversations(
    group_id: str, channel: str | None = None, language: str | None = None
) -> list[dict]:
    conditions = ["group_id = ?"]
    values = [group_id]
    for column, value in (("channel", channel), ("language", language)):
        if value is not None:
            conditions.append(column + " = ?")
            values.append(value)
    with closing(connect()) as db:
        if db.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone() is None:
            raise HTTPException(404, "Группа ещё не импортирована.")
        rows = db.execute(
            f"SELECT {META}, audio_url FROM conversations WHERE "
            + " AND ".join(conditions)
            + " ORDER BY conversation_id",
            values,
        )
        return [metadata(row) for row in rows]


@router.get(CONVERSATIONS + "/{conversation_id}", response_model=ConversationDetail)
def get_conversation(group_id: str, conversation_id: str) -> dict:
    with closing(connect()) as db:
        row = db.execute(
            "SELECT * FROM conversations WHERE group_id = ? AND conversation_id = ?",
            (group_id, conversation_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Диалог не найден.")
    messages = json.loads(row["transcript"])
    for index, message in enumerate(messages, start=1):
        message["message_id"] = f"{conversation_id}:m{index:04d}"
    return {**metadata(row), "audio_url": row["audio_url"], "transcript": messages}


@router.get(
    CONVERSATIONS + "/{conversation_id}/messages/{message_id}", response_model=EvidenceMessage
)
def get_message(group_id: str, conversation_id: str, message_id: str) -> dict:
    for message in get_conversation(group_id, conversation_id)["transcript"]:
        if message["message_id"] == message_id:
            return message
    raise HTTPException(404, "Реплика не найдена в указанном диалоге.")
