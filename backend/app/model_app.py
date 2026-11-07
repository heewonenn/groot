"""Optional model-enabled composition of the shared server, owned by participant 2."""

import math
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from backend.app.evaluation.contract import PROMPT_VERSION, evidence_quotes
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.knowledge import Knowledge
from backend.app.evaluation.provider import OpenAIResponses
from backend.app.evaluation.store import RunStore
from backend.app.main import create_app
from backend.app.team_data import DataRoute


def _number(name: str, default: str, low: float, high: float) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid_configuration")
    return value


def configured_evaluator() -> tuple[ModelEvaluator | None, str | None]:
    if os.getenv("CQA_MODEL_ENABLED", "").lower() not in {"1", "true"}:
        return None, "model_disabled"
    if os.getenv("CQA_MODEL_PROVIDER", "openai") != "openai":
        return None, "provider_not_supported"
    model, key = os.getenv("CQA_MODEL", "").strip(), os.getenv("OPENAI_API_KEY", "").strip()
    if not model or not key or not os.getenv("CQA_CONTEXT_TOKENS"):
        return None, "model_configuration_missing"
    try:
        knowledge_path = os.getenv("CQA_KNOWLEDGE_PATH")
        knowledge = Knowledge.read(knowledge_path) if knowledge_path else None
        rates = {}
        for side in ("input", "output"):
            name = f"CQA_{side.upper()}_USD_PER_MILLION"
            rates[f"{side}_usd_per_million"] = (
                _number(name, "0", 0, 1_000_000) if os.getenv(name) else None
            )
        evaluator = ModelEvaluator(
            OpenAIResponses(
                model,
                key,
                max_output_tokens=int(_number("CQA_MAX_OUTPUT_TOKENS", "12000", 512, 32000)),
            ),
            RunStore(),
            knowledge=knowledge,
            request_timeout=_number("CQA_REQUEST_TIMEOUT", "60", 1, 120),
            total_timeout=_number("CQA_TOTAL_TIMEOUT", "150", 1, 360),
            max_input_chars=int(_number("CQA_MAX_INPUT_CHARS", "180000", 1000, 500000)),
            context_token_limit=int(_number("CQA_CONTEXT_TOKENS", "0", 1024, 2_000_000)),
            **rates,
        )
        return evaluator, None
    except ValueError, OSError, sqlite3.Error:
        return None, "model_configuration_invalid"


def create_model_app(evaluator: ModelEvaluator | None, reason: str | None = None):
    try:
        # A restart can disable the provider while an earlier audit is still running.
        # Recover local history independently of model readiness; never resume inference.
        (evaluator.store if evaluator else RunStore()).recover()
    except (sqlite3.Error, OSError):
        # Match the shared app: health remains available when storage is offline.
        pass
    application = create_app(evaluator)
    router = APIRouter(prefix="/api", tags=["model-audit"], route_class=DataRoute)

    @router.get("/model-status")
    def status() -> dict:
        return {
            "configured": evaluator is not None,
            "ready": evaluator is not None and application.state.jobs.available,
            "reason": reason,
            "provider": evaluator.provider.name if evaluator else None,
            "model": evaluator.provider.model if evaluator else None,
            "prompt_version": PROMPT_VERSION,
            "audio_analysed": False,
            "knowledge_version": (
                evaluator.knowledge.source_version_id if evaluator and evaluator.knowledge else None
            ),
        }

    def store() -> RunStore:
        return evaluator.store if evaluator else RunStore()

    @router.get("/evaluation-runs")
    def runs(
        group_id: str = Query(min_length=1, max_length=200),
        conversation_id: str = Query(min_length=1, max_length=200),
    ) -> list[dict]:
        return store().list(group_id, conversation_id)

    def record(evaluation_id: str) -> dict:
        run = store().get(evaluation_id)
        if run is None:
            raise HTTPException(404, "Запуск модели не найден.")
        return run

    @router.get("/evaluation-runs/{evaluation_id}")
    def metadata(evaluation_id: str) -> dict:
        return record(evaluation_id)["metadata"]

    @router.get("/evaluation-runs/{evaluation_id}/evidence")
    def evidence(evaluation_id: str) -> list[dict]:
        run = record(evaluation_id)
        if run["result"] is None:
            raise HTTPException(409, "В запуске нет проверенного результата.")
        return evidence_quotes(run["result"], run["snapshot"]["payload"]["conversation"])

    application.include_router(router)
    return application


def create_configured_app():
    evaluator, reason = configured_evaluator()
    return create_model_app(evaluator, reason)
