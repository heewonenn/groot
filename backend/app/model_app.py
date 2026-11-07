"""Optional model-enabled composition of the shared server, owned by participant 2."""

import math
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.app.evaluation.contract import PROMPT_VERSION, evidence_quotes
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.gemini import GEMINI_MODEL, GeminiGenerateContent
from backend.app.evaluation.knowledge import Knowledge
from backend.app.evaluation.openrouter import OpenRouterChat
from backend.app.evaluation.provider import OpenAIResponses, Provider
from backend.app.evaluation.store import RunStore
from backend.app.main import create_app
from backend.app.team_data import ROOT, DataRoute

KEY_ENV = {
    "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
}


class LocalGoogleModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    model: str = Field(pattern=r"^gemini-[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    context_tokens: int = Field(ge=1024, le=2_000_000)


def _number(name: str, default: str, low: float, high: float) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid_configuration")
    return value


def _provider(name: str, model: str, key: str, max_output: int) -> Provider:
    if name == "gemini":
        return GeminiGenerateContent(
            model, key, max_output_tokens=max_output,
            thinking_level=os.getenv(
                "CQA_GEMINI_THINKING_LEVEL", "low" if model == GEMINI_MODEL else "medium",
            ),
        )
    if name == "openrouter":
        return OpenRouterChat(model, key, max_output_tokens=max_output)
    return OpenAIResponses(model, key, max_output_tokens=max_output)


def configured_evaluator() -> tuple[ModelEvaluator | None, str | None]:
    if os.getenv("CQA_MODEL_ENABLED", "").lower() not in {"1", "true"}:
        return None, "model_disabled"
    provider_name = os.getenv("CQA_MODEL_PROVIDER", "openai").strip()
    fallback_name = os.getenv("CQA_FALLBACK_PROVIDER", "").strip()
    if provider_name not in KEY_ENV or fallback_name not in {"", "gemini", "openrouter"}:
        return None, "provider_not_supported"
    model = os.getenv("CQA_MODEL", GEMINI_MODEL if provider_name == "gemini" else "").strip()
    key = os.getenv(KEY_ENV[provider_name], "").strip()
    if not model or not key or not os.getenv("CQA_CONTEXT_TOKENS"):
        return None, "model_configuration_missing"
    try:
        context_limit = int(_number("CQA_CONTEXT_TOKENS", "0", 1024, 2_000_000))
        if provider_name == "gemini" and os.getenv("CQA_GOOGLE_CATALOG_RESOLVED") != "1":
            database = Path(os.getenv("CQA_DB_PATH") or ROOT / "data" / "cqa.sqlite3")
            profile = database.with_name("google-model.local.json")
            if profile.is_file():
                selection = LocalGoogleModel.model_validate_json(profile.read_bytes())
                model, context_limit = selection.model, selection.context_tokens
        knowledge_path = os.getenv("CQA_KNOWLEDGE_PATH")
        knowledge = Knowledge.read(knowledge_path) if knowledge_path else None
        rates = {}
        for side in ("input", "output"):
            name = f"CQA_{side.upper()}_USD_PER_MILLION"
            rates[f"{side}_usd_per_million"] = (
                _number(name, "0", 0, 1_000_000) if os.getenv(name) else None
            )
        max_output = int(_number("CQA_MAX_OUTPUT_TOKENS", "12000", 512, 32000))
        provider = _provider(provider_name, model, key, max_output)
        fallback = fallback_context = None
        if fallback_name:
            fallback_model = os.getenv("CQA_FALLBACK_MODEL", "").strip()
            fallback_key = os.getenv(KEY_ENV[fallback_name], "").strip()
            if not (fallback_model and fallback_key and os.getenv("CQA_FALLBACK_CONTEXT_TOKENS")):
                return None, "model_configuration_missing"
            if (fallback_name, fallback_model) == (provider_name, model):
                return None, "model_configuration_invalid"
            fallback_context = int(_number("CQA_FALLBACK_CONTEXT_TOKENS", "0", 1024, 2_000_000))
            fallback = _provider(fallback_name, fallback_model, fallback_key, max_output)
        use_flash_38 = provider_name == "gemini" and model == GEMINI_MODEL
        evaluator = ModelEvaluator(
            provider,
            RunStore(),
            fallback_provider=fallback,
            fallback_context_token_limit=fallback_context,
            knowledge=knowledge,
            request_timeout=_number("CQA_REQUEST_TIMEOUT", "110" if use_flash_38 else "60", 1, 120),
            total_timeout=_number("CQA_TOTAL_TIMEOUT", "240" if use_flash_38 else "150", 1, 360),
            max_input_chars=int(_number("CQA_MAX_INPUT_CHARS", "180000", 1000, 500000)),
            context_token_limit=context_limit,
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
            "fallback": (
                {"provider": evaluator.fallback_provider.name,
                 "model": evaluator.fallback_provider.model}
                if evaluator and evaluator.fallback_provider else None
            ),
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
