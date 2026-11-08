"""Callable adapter for participant 4's durable JobRunner."""

import time
from datetime import UTC, datetime

from backend.app.evaluation import compact
from backend.app.evaluation.contract import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    canonical,
    digest,
    output_schema,
    parse_answers,
    prepare,
    validate_result,
)
from backend.app.evaluation.gemini import GEMMA_4_31B
from backend.app.evaluation.knowledge import Knowledge
from backend.app.evaluation.provider import ModelError, Provider, token_usage
from backend.app.evaluation.store import RunStore

FAILOVER_ERRORS = {
    "provider_network", "provider_timeout", "provider_transient", "provider_rate_limit",
}


class ModelEvaluator:
    def __init__(
        self,
        provider: Provider,
        store: RunStore,
        *,
        knowledge: Knowledge | None = None,
        request_timeout: float = 60,
        total_timeout: float = 150,
        max_input_chars: int = 180000,
        context_token_limit: int | None = None,
        fallback_provider: Provider | None = None,
        fallback_context_token_limit: int | None = None,
        quota_provider: Provider | None = None,
        quota_context_token_limit: int | None = None,
        input_usd_per_million: float | None = None,
        output_usd_per_million: float | None = None,
    ):
        self.provider, self.store, self.knowledge = provider, store, knowledge
        self.request_timeout, self.total_timeout = request_timeout, total_timeout
        self.max_input_chars = max_input_chars
        self.context_token_limit = context_token_limit
        self.fallback_provider = fallback_provider
        self.fallback_context_token_limit = fallback_context_token_limit
        self.quota_provider = quota_provider
        self.quota_context_token_limit = quota_context_token_limit
        self.rates = input_usd_per_million, output_usd_per_million

    def _settings(self, settings: dict) -> None:
        expected = {
            "provider": self.provider.name,
            "model": self.provider.model,
            "prompt_version": PROMPT_VERSION,
            "knowledge_version": self.knowledge.source_version_id if self.knowledge else None,
            "response_layout": compact.LAYOUT,
        }
        if self.provider.name == "gemini":
            expected["google_only"] = True
            expected["gemma_only"] = True
            if settings.get("model") == GEMMA_4_31B:
                expected["model"] = GEMMA_4_31B
        # Do not silently ignore a requested model, sampling or audio configuration.
        if any(
            value is not None and (key not in expected or value != expected[key])
            for key, value in settings.items()
        ):
            raise ModelError("settings_not_supported")

    def __call__(self, eid: str, conversation: dict, rules: dict, settings: dict) -> dict:
        started = time.monotonic()
        metadata = {
            "evaluation_id": eid,
            "group_id": conversation.get("group_id", ""),
            "conversation_id": conversation.get("conversation_id", ""),
            "checklist_version_id": rules.get("checklist_version_id"),
            "status": "running",
            "generation_mode": "live" if self.provider.live else "demo",
            "error_code": None,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "provider": self.provider.name,
            "configured_provider": self.provider.name,
            "configured_model": self.provider.model,
            "provider_parameters": getattr(self.provider, "parameters", {}),
            "resolved_model": None,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": digest(INSTRUCTIONS),
            "schema_hash": digest(output_schema()),
            "knowledge_version": self.knowledge.source_version_id if self.knowledge else None,
            "knowledge_hash": self.knowledge.fingerprint if self.knowledge else None,
            "settings": settings,
            "input_fingerprint": None,
            "attempts": [],
            "elapsed_ms": None,
            "token_usage": None,
            "estimated_cost_usd": None,
            "cost_basis": "configured uncached token rates; excludes taxes/provider adjustments",
            "audio_analysed": False,
            "context_token_limit": self.context_token_limit,
            "context_budget_used": None,
        }
        gemma_only = settings.get("gemma_only") is True or (
            self.provider.name == "gemini" and settings.get("model") == GEMMA_4_31B
        )
        google_only = settings.get("google_only") is True or gemma_only
        allow_fallback = self.fallback_provider is not None and (
            not google_only or self.fallback_provider.name == "gemini"
        )
        if allow_fallback:
            metadata["fallback"] = {
                "provider": self.fallback_provider.name,
                "model": self.fallback_provider.model,
                "parameters": getattr(self.fallback_provider, "parameters", {}),
                "context_token_limit": self.fallback_context_token_limit,
            }
        routes = [(self.provider, self.context_token_limit)]
        if self.quota_provider is not None:
            routes.append((self.quota_provider, self.quota_context_token_limit))
            metadata["quota_fallback"] = {
                "provider": self.quota_provider.name, "model": self.quota_provider.model,
                "parameters": getattr(self.quota_provider, "parameters", {}),
                "context_token_limit": self.quota_context_token_limit,
            }
        if allow_fallback:
            routes.append((self.fallback_provider, self.fallback_context_token_limit))
        self.store.begin(metadata)
        snapshot = None
        try:
            self._settings(settings)
            if gemma_only:
                routes = [(p, limit) for p, limit in routes
                          if p.name == "gemini" and p.model == GEMMA_4_31B]
                if not routes:
                    raise ModelError("provider_model_unavailable")
            # Validate before retrieval; unknown fields and signed audio URLs are excluded.
            payload, version = prepare(conversation, rules, [])
            if self.knowledge:
                query = " ".join(m["text"] for m in payload["conversation"]["transcript"])
                payload["knowledge"] = self.knowledge.retrieve(query)
            use_turns = settings.get("response_layout") == compact.LAYOUT or any(
                getattr(route, "prefer_turn_numbers", False) for route, _ in routes
            )
            short = compact.CompactAnswers(payload, version) if use_turns else None
            instructions = compact.INSTRUCTIONS if short else INSTRUCTIONS
            wire_input = short.input if short else payload
            schema = short.schema if short else output_schema()
            if short:
                metadata.update(
                    prompt_version=PROMPT_VERSION + "." + compact.LAYOUT,
                    prompt_hash=digest(instructions), schema_hash=digest(schema),
                )
            snapshot = {"instructions": instructions, "payload": payload}
            if short:
                snapshot["wire_schema"] = schema
            encoded = canonical(wire_input)
            metadata["input_fingerprint"] = digest(
                {
                    **snapshot,
                    "settings": settings,
                    "provider": self.provider.name,
                    "model": self.provider.model,
                    "provider_parameters": metadata["provider_parameters"],
                    **{k: metadata[k] for k in ("quota_fallback", "fallback") if k in metadata},
                    "schema": schema,
                    "knowledge_hash": metadata["knowledge_hash"],
                }
            )
            if len(encoded) + len(instructions) > self.max_input_chars:
                raise ModelError("input_too_large")
            route_index = 0
            provider, context_limit = routes[0]
            correction = None
            format_retries = network_retries = 0
            max_attempts = 4 if len(routes) == 3 else 3
            for attempt_number in range(1, max_attempts + 1):
                remaining = self.total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise ModelError("evaluation_deadline")
                request = (
                    encoded
                    if correction is None
                    else canonical(
                        {
                            "evaluation_input": wire_input,
                            "correction": correction,
                        }
                    )
                )
                # Recheck the actual request and output allowance against this route's window.
                # UTF-8 bytes plus a wrapper reserve deliberately overestimate text tokens.
                parameters = getattr(provider, "parameters", {})
                budget = len((request + instructions + canonical(schema)).encode("utf-8"))
                budget += parameters.get("max_output_tokens", 0) + 4096
                metadata.update(context_token_limit=context_limit, context_budget_used=budget)
                if context_limit is not None and budget > context_limit:
                    raise ModelError("context_budget_exceeded")
                metadata.update(
                    provider=provider.name, provider_parameters=parameters, resolved_model=None,
                    generation_mode="live" if provider.live else "demo",
                )
                attempt_started = time.monotonic()
                attempt = {
                    "number": attempt_number,
                    "provider": provider.name,
                    "configured_model": provider.model,
                    "fallback": provider is not self.provider,
                    "provider_parameters": parameters,
                    "context_budget_used": budget,
                    "status": "running",
                    "error_code": None,
                    "usage": None,
                    "resolved_model": None,
                    "elapsed_ms": None,
                }
                metadata["attempts"].append(attempt)
                self.store.save(metadata, snapshot)
                retry = False
                try:
                    remaining = self.total_timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ModelError("evaluation_deadline")
                    reply = provider.generate(
                        instructions,
                        request,
                        schema,
                        min(self.request_timeout, remaining),
                    )
                    attempt["usage"] = token_usage(reply.usage)
                    metadata["resolved_model"] = reply.model
                    attempt["resolved_model"] = reply.model
                    if time.monotonic() - started > self.total_timeout:
                        raise ModelError("evaluation_deadline")
                    answers = short.decode(reply.text) if short else parse_answers(reply.text)
                    result = {
                        "kind": "model_result",
                        "schema_version": "1.0",
                        "evaluation_id": eid,
                        "group_id": payload["conversation"]["group_id"],
                        "conversation_id": payload["conversation"]["conversation_id"],
                        "checklist_id": version.checklist_id,
                        "checklist_version_id": version.checklist_version_id,
                        "generation": {
                            "mode": metadata["generation_mode"],
                            "origin": provider.name,
                            "model_id": reply.model,
                            "is_ground_truth": False,
                            "use_for_model_quality": provider.live,
                            "note": "Transcript evaluation; no acoustic analysis. "
                            "Model predictions are not human ground truth.",
                        },
                        "criteria": answers["criteria"],
                    }
                    card = validate_result(result, payload, version)
                    attempt["status"] = "completed"
                    metadata["status"] = card["status"]
                except ModelError as exc:
                    attempt.update(status="failed", error_code=exc.code)
                    if exc.correction_hint:
                        attempt["validation_hint"] = exc.correction_hint
                    if exc.usage is not None:
                        attempt["usage"] = token_usage(exc.usage)
                    if exc.code.startswith("answer_") and format_retries == 0:
                        correction = {
                            "error_code": exc.code,
                            "instruction": "Return every criterion again using its saved rules "
                            "and evidence from the supplied conversation.",
                        }
                        if exc.correction_hint:
                            correction["validation_hint"] = exc.correction_hint
                        format_retries += 1
                        retry = True
                    elif route_index + 1 < len(routes) and (
                        exc.code in FAILOVER_ERRORS or (
                            provider is self.quota_provider and (
                                exc.code == "provider_model_unavailable"
                                or exc.code.startswith("answer_")
                            )
                        )
                    ):
                        route_index += 1
                        provider, context_limit = routes[route_index]
                        correction = None
                        network_retries += 1
                        retry = True
                    elif exc.retryable and network_retries == 0:
                        network_retries += 1
                        retry = True
                    if not retry or attempt_number == max_attempts:
                        raise
                except Exception:
                    attempt.update(status="failed", error_code="internal_error")
                    raise
                finally:
                    attempt["elapsed_ms"] = round((time.monotonic() - attempt_started) * 1000, 3)
                    self.store.save(metadata, snapshot)
                if retry:
                    if provider is self.provider and attempt["error_code"] in {
                        "provider_network",
                        "provider_transient",
                        "provider_timeout",
                    }:
                        remaining = self.total_timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise ModelError("evaluation_deadline")
                        time.sleep(min(0.5, remaining))
                    continue
                self._finish(metadata, started)
                self.store.save(metadata, snapshot, result)
                return result
            raise ModelError("attempts_exhausted")
        except Exception as exc:
            metadata.update(
                status="failed",
                error_code=exc.code if isinstance(exc, ModelError) else "internal_error",
            )
            self._finish(metadata, started)
            self.store.save(metadata, snapshot)
            if isinstance(exc, ModelError):
                raise
            raise ModelError("internal_error") from None

    def _finish(self, metadata: dict, started: float) -> None:
        metadata["finished_at"] = datetime.now(UTC).isoformat()
        metadata["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        attempts = metadata["attempts"]
        if attempts and all(a["usage"] is not None for a in attempts):
            total = {
                k: sum(a["usage"][k] for a in attempts) for k in ("input_tokens", "output_tokens")
            }
            metadata["token_usage"] = total
            # Only the primary route has configured prices. Never price an alternate at its rate.
            if not any(a["fallback"] for a in attempts) and all(r is not None for r in self.rates):
                metadata["estimated_cost_usd"] = (
                    total["input_tokens"] * self.rates[0] + total["output_tokens"] * self.rates[1]
                ) / 1_000_000
