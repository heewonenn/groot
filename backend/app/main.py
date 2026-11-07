"""Shared API: CQA-team data routes and participant 1's checklist module."""
import os
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from backend.app.api.checklists import router as checklists_router
from backend.app.team_data import router as team_data_router

app = FastAPI(title=os.getenv("APP_NAME", "Hackathon AI"))
app.include_router(checklists_router)
app.include_router(team_data_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
