"""HTTP adapter for checklist authoring and the shared calculator."""

import os
import sqlite3
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.repositories.checklists import ChecklistRepository
from backend.app.schemas.checklists import (
    CalculateRequest,
    CalculationResponse,
    ChecklistDraft,
    ChecklistSummary,
    ChecklistVersion,
    CreateChecklist,
    PublishVersion,
    UpdateDraft,
)
from backend.app.services.scoring import ChecklistError, calculate


class ChecklistRoute(APIRoute):
    """Keep domain errors consistent without changing other teams' API handlers."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            details = []
            try:
                return await original(request)
            except ChecklistError as error:
                code, message, status = error.code, error.message, error.status_code
            except RequestValidationError as error:
                code, message, status = "validation_error", "Проверьте поля чек-листа.", 422
                details = [
                    {"field": ".".join(map(str, item["loc"][1:])), "message": item["msg"]}
                    for item in error.errors()
                ]
            except (sqlite3.Error, OSError):
                code, message, status = (
                    "storage_unavailable", "Хранилище временно недоступно. Повторите запрос.", 503
                )
            return JSONResponse(
                status_code=status,
                content={"error": {
                    "code": code, "message": message, "details": details,
                    "request_id": uuid4().hex,
                }},
            )

        return handler


router = APIRouter(prefix="/api", tags=["checklists"], route_class=ChecklistRoute)
ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=4)
def _repository(path: str) -> ChecklistRepository:
    return ChecklistRepository(Path(path))


def get_repository() -> ChecklistRepository:
    return _repository(os.getenv("CQA_DB_PATH", str(ROOT / "data" / "cqa.sqlite3")))


Repository = Annotated[ChecklistRepository, Depends(get_repository)]


@router.get("/checklists", response_model=list[ChecklistSummary])
def list_checklists(repository: Repository) -> list[ChecklistSummary]:
    return repository.list_checklists()


@router.post("/checklists", response_model=ChecklistDraft, status_code=201)
def create_checklist(body: CreateChecklist, repository: Repository) -> ChecklistDraft:
    return repository.create(body.title, body.copy_from_version_id)


@router.get("/checklists/{checklist_id}/draft", response_model=ChecklistDraft)
def get_draft(checklist_id: str, repository: Repository) -> ChecklistDraft:
    return repository.get_draft(checklist_id)


@router.patch("/checklists/{checklist_id}/draft", response_model=ChecklistDraft)
def update_draft(
    checklist_id: str, body: UpdateDraft, repository: Repository
) -> ChecklistDraft:
    return repository.update_draft(checklist_id, body.expected_revision, body.content)


@router.get("/checklists/{checklist_id}/versions", response_model=list[ChecklistVersion])
def list_versions(checklist_id: str, repository: Repository) -> list[ChecklistVersion]:
    return repository.list_versions(checklist_id)


@router.post("/checklists/{checklist_id}/versions", response_model=ChecklistVersion)
def publish_version(
    checklist_id: str, body: PublishVersion, repository: Repository
) -> ChecklistVersion:
    return repository.publish(checklist_id, body.expected_revision)


@router.get("/checklist-versions/{version_id}", response_model=ChecklistVersion)
def get_version(version_id: str, repository: Repository) -> ChecklistVersion:
    return repository.get_version(version_id)


@router.post("/checklist-versions/{version_id}/calculate", response_model=CalculationResponse)
def calculate_version(
    version_id: str, body: CalculateRequest, repository: Repository
) -> CalculationResponse:
    version = repository.get_version(version_id)
    return CalculationResponse(
        checklist_version_id=version.checklist_version_id, result=calculate(version, body.answers)
    )
