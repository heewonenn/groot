"""Shared CQA API. The model adapter can be supplied when constructing the app."""
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from pydantic import BaseModel

from backend.app.api.audio import router as audio_router
from backend.app.api.checklists import router as checklists_router
from backend.app.api.evaluation_jobs import router as jobs_router
from backend.app.api.participant3 import router as participant3_router
from backend.app.api.submission_downloads import router as submission_router
from backend.app.services.evaluation_jobs import Evaluator, JobRunner
from backend.app.services.participant3 import initialize as initialize_reviews
from backend.app.team_data import router as team_data_router


class HealthResponse(BaseModel):
    status: Literal["ok"]


def health() -> HealthResponse:
    return HealthResponse(status="ok")


class Capabilities(BaseModel):
    model_available: bool
    audio_playback: bool = True
    acoustic_scoring: bool = False
    product_reference: bool = False


def capabilities(request: Request) -> Capabilities:
    return Capabilities(model_available=request.app.state.jobs.available)


def create_app(evaluator: Evaluator | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runner = JobRunner(evaluator)
        application.state.jobs = runner
        try:
            runner.start()
            initialize_reviews()
        except (sqlite3.Error, OSError):
            # Keep the health route and unrelated screens available when storage is offline.
            # A later restart recovers persisted work once the database is accessible.
            pass
        try:
            yield
        finally:
            runner.stop()

    application = FastAPI(title=os.getenv("APP_NAME", "Hackathon AI"), lifespan=lifespan)
    for router in (
        checklists_router, team_data_router, audio_router, jobs_router,
        participant3_router, submission_router,
    ):
        application.include_router(router)
    application.get("/api/health", response_model=HealthResponse)(health)
    application.get("/api/capabilities", response_model=Capabilities)(capabilities)
    return application


app = create_app()
