"""Participant 3's additive card and appeal endpoints."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import Response

from backend.app.schemas.participant3 import Appeal, OpenAppeal, ReviewRequest
from backend.app.services import participant3
from backend.app.team_data import DataRoute

router = APIRouter(prefix="/api", tags=["cards-and-appeals"], route_class=DataRoute)
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=150)]
Filter = Annotated[str | None, Query(min_length=1, max_length=200)]


@router.get("/evaluations")
def evaluations(group_id: Filter = None, conversation_id: Filter = None) -> list[dict]:
    return participant3.list_evaluations(group_id, conversation_id)


@router.get("/evaluation-cards/{evaluation_id}")
def card(evaluation_id: str) -> dict:
    return participant3.get_card(evaluation_id)


@router.get("/evaluation-cards/{evaluation_id}/audit")
def audit(evaluation_id: str) -> Response:
    # The wrapper contains source IDs, reasons and rules, never transcripts or audio URLs.
    result = participant3.get_card(evaluation_id)
    return Response(
        json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="evaluation-audit.json"'},
    )


@router.get("/appeals", response_model=list[Appeal])
def appeals(status: Literal["open", "resolved"] | None = None) -> list[dict]:
    return participant3.list_appeals(status)


@router.post("/evaluations/{evaluation_id}/appeals", response_model=Appeal, status_code=201)
def open_appeal(evaluation_id: str, payload: OpenAppeal) -> dict:
    return participant3.open_appeal(evaluation_id, payload)


@router.post("/appeals/{appeal_id}/decision")
def decision(appeal_id: str, payload: ReviewRequest, idempotency_key: Key) -> dict:
    return participant3.save_decision(appeal_id, payload, idempotency_key)
