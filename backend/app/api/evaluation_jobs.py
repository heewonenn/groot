"""Evaluation jobs for one immutable ZIP group and a saved checklist version."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.services.evaluation_jobs import JobRunner, get_evaluation, get_job
from backend.app.team_data import DataRoute

__all__ = ["JobRunner", "router"]
router = APIRouter(prefix="/api", tags=["evaluations"], route_class=DataRoute)
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=150)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
OptionID = Annotated[str, Field(pattern=r"^[\w.@/+:-]{1,150}$")]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: OptionID | None = None
    model: OptionID | None = None
    prompt_version: OptionID | None = None
    knowledge_version: OptionID | None = None
    audio_version: OptionID | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, ge=0, le=1)
    seed: int | None = None
    response_layout: Literal["turns-v1"] | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    group_id: Identifier
    conversation_ids: list[Identifier] = Field(min_length=1, max_length=10000)
    checklist_version_id: Identifier
    settings: Settings = Field(default_factory=Settings, alias="model_config")

    @field_validator("conversation_ids")
    @classmethod
    def distinct(cls, ids: list[str]) -> list[str]:
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation IDs")
        return ids


class JobReceipt(BaseModel):
    job_id: str


@router.post("/evaluation-jobs", status_code=202, response_model=JobReceipt)
def create(payload: JobRequest, request: Request, idempotency_key: Key) -> dict:
    return request.app.state.jobs.create(
        payload.group_id,
        payload.conversation_ids,
        payload.checklist_version_id,
        payload.settings.model_dump(exclude_none=True),
        idempotency_key,
    )


@router.post("/evaluation-jobs/{job_id}/retry", status_code=202, response_model=JobReceipt)
def retry(job_id: str, request: Request, idempotency_key: Key) -> dict:
    return request.app.state.jobs.retry(job_id, idempotency_key)


@router.get("/evaluation-jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    return get_job(job_id)


@router.get("/evaluation-jobs/{job_id}/export")
def export(job_id: str) -> dict:
    return get_job(job_id, export=True)


@router.get("/evaluations/{evaluation_id}")
def evaluation(evaluation_id: str) -> dict:
    return get_evaluation(evaluation_id)
