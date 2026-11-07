"""Callable adapter for participant 4's durable JobRunner."""

import time
from datetime import UTC, datetime

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
from backend.app.evaluation.knowledge import Knowledge
from backend.app.evaluation.provider import ModelError, Provider, token_usage
from backend.app.evaluation.store import RunStore


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
        input_usd_per_million: float | None = None,
        output_usd_per_million: float | None = None,
    ):
        self.provider, self.store, self.knowledge = provider, store, knowledge
        self.request_timeout, self.total_timeout = request_timeout, total_timeout
        self.max_input_chars = max_input_chars
        self.context_token_limit = context_token_limit
        self.rates = input_usd_per_million, output_usd_per_million

    def _settings(self, settings: dict) -> None:
        expected = {
            "provider": self.provider.name,
            "model": self.provider.model,
            "prompt_version": PROMPT_VERSION,
            "knowledge_version": self.knowledge.source_version_id if self.knowledge else None,
        }
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
        self.store.begin(metadata)
        snapshot = None
        try:
            self._settings(settings)
            # Validate before retrieval; unknown fields and signed audio URLs are excluded.
            payload, version = prepare(conversation, rules, [])
            if self.knowledge:
                query = " ".join(m["text"] for m in payload["conversation"]["transcript"])
                payload["knowledge"] = self.knowledge.retrieve(query)
            snapshot = {"instructions": INSTRUCTIONS, "payload": payload}
            encoded = canonical(payload)
            metadata["input_fingerprint"] = digest(
                {
                    **snapshot,
                    "settings": settings,
                    "provider": self.provider.name,
                    "model": self.provider.model,
                    "provider_parameters": metadata["provider_parameters"],
                    "schema": output_schema(),
                    "knowledge_hash": metadata["knowledge_hash"],
                }
            )
            if len(encoded) + len(INSTRUCTIONS) > self.max_input_chars:
                raise ModelError("input_too_large")
            # UTF-8 bytes are a deliberately conservative BPE token upper bound.
            # Reserve space for the response, schema, wrapper and corrective instruction.
            budget = len((encoded + INSTRUCTIONS + canonical(output_schema())).encode("utf-8"))
            budget += metadata["provider_parameters"].get("max_output_tokens", 0) + 4096
            metadata["context_budget_used"] = budget
            if self.context_token_limit is not None and budget > self.context_token_limit:
                raise ModelError("context_budget_exceeded")
            self.store.save(metadata, snapshot)
            correction = None
            format_retries = network_retries = 0
            for attempt_number in range(1, 4):
                remaining = self.total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise ModelError("evaluation_deadline")
                request = (
                    encoded
                    if correction is None
                    else canonical(
                        {
                            "evaluation_input": payload,
                            "correction": {
                                "error_code": correction,
                                "instruction": "Return a complete answer satisfying all rules.",
                            },
                        }
                    )
                )
                attempt_started = time.monotonic()
                attempt = {
                    "number": attempt_number,
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
                    reply = self.provider.generate(
                        INSTRUCTIONS,
                        request,
                        output_schema(),
                        min(self.request_timeout, remaining),
                    )
                    attempt["usage"] = token_usage(reply.usage)
                    metadata["resolved_model"] = reply.model
                    attempt["resolved_model"] = reply.model
                    if time.monotonic() - started > self.total_timeout:
                        raise ModelError("evaluation_deadline")
                    answers = parse_answers(reply.text)
                    result = {
                        "kind": "model_result",
                        "schema_version": "1.0",
                        "evaluation_id": eid,
                        "group_id": payload["conversation"]["group_id"],
                        "conversation_id": payload["conversation"]["conversation_id"],
                        "checklist_id": version.checklist_id,
                        "checklist_version_id": version.checklist_version_id,
                        "generation": {
                            "mode": "live" if self.provider.live else "demo",
                            "origin": self.provider.name,
                            "model_id": reply.model,
                            "is_ground_truth": False,
                            "use_for_model_quality": self.provider.live,
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
                    if exc.usage is not None:
                        attempt["usage"] = token_usage(exc.usage)
                    if exc.code.startswith("answer_") and format_retries == 0:
                        correction = exc.code
                        format_retries += 1
                        retry = True
                    elif exc.retryable and network_retries == 0:
                        network_retries += 1
                        retry = True
                    if not retry or attempt_number == 3:
                        raise
                except Exception:
                    attempt.update(status="failed", error_code="internal_error")
                    raise
                finally:
                    attempt["elapsed_ms"] = round((time.monotonic() - attempt_started) * 1000, 3)
                    self.store.save(metadata, snapshot)
                if retry:
                    if attempt["error_code"] in {
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
            if all(rate is not None for rate in self.rates):
                metadata["estimated_cost_usd"] = (
                    total["input_tokens"] * self.rates[0] + total["output_tokens"] * self.rates[1]
                ) / 1_000_000
