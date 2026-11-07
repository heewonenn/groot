"""Read-only HTTP downloads of the organizers' validated JSON 1.0 results."""

import json
from contextlib import closing
from sqlite3 import Connection
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.services.evaluation_jobs import get_job
from backend.app.services.submission_results import build_submission
from backend.app.services.submission_validation import validate_dataset
from backend.app.submission import TEAM, check_document
from backend.app.team_data import DataRoute, connect, get_conversation, list_conversations

router = APIRouter(prefix="/api", tags=["submission-downloads"], route_class=DataRoute)
Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class PrepareSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    group_id: Identifier
    split: Literal["dev", "prod"]
    job_ids: list[Identifier] = Field(min_length=1, max_length=1000)
    team_name: Annotated[str, Field(min_length=1, max_length=200)]
    model_mode: Literal["local", "external_api", "hybrid"]

    @field_validator("job_ids")
    @classmethod
    def distinct_jobs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Укажите разные исходные задания.")
        return values


def _has_jobs(db: Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cqa_batch_jobs'"
    ).fetchone() is not None


@router.get("/evaluation-jobs")
def list_jobs(
    group_id: Annotated[str, Query(min_length=1, max_length=200, pattern=r"\S")],
) -> list[dict]:
    """List stored jobs, including retry links, without starting inference."""
    list_conversations(group_id)
    with closing(connect()) as db:
        if not _has_jobs(db):
            return []
        ids = db.execute(
            "SELECT id FROM cqa_batch_jobs WHERE group_id=? ORDER BY created_at,id",
            (group_id,),
        ).fetchall()
    return [get_job(row["id"]) for row in ids]


@router.post("/submissions/prepare", response_class=Response)
def prepare_submission(body: PrepareSubmission) -> Response:
    """Download a validated file; do not write results, publish, or run a model."""
    if body.team_name != TEAM:
        raise HTTPException(422, f"Ожидается название команды {TEAM}.")
    summaries = list_conversations(body.group_id)
    conversations = [get_conversation(body.group_id, row["conversation_id"]) for row in summaries]
    try:
        validate_dataset(conversations, body.split)
        with closing(connect()) as db:
            if not _has_jobs(db):
                raise HTTPException(404, "Сохранённые задания не найдены.")
            for job_id in body.job_ids:
                row = db.execute(
                    "SELECT group_id FROM cqa_batch_jobs WHERE id=?", (job_id,)
                ).fetchone()
                # Do not reveal whether a job exists in another group.
                if row is None or row["group_id"] != body.group_id:
                    raise HTTPException(404, "Задание выбранной группы не найдено.")
        document = build_submission(
            group_id=body.group_id,
            conversations=conversations,
            job_ids=body.job_ids,
            team_name=body.team_name,
            model_mode=body.model_mode,
        )
        check_document(document, conversations, body.split)
        content = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:
        # The reused exporter/validator expose safe domain errors, never provider failures.
        raise HTTPException(422, str(exc)) from None
    except (KeyError, TypeError, ArithmeticError):
        raise HTTPException(422, "Сохранённые результаты не соответствуют формату сдачи.") from None
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="results.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
