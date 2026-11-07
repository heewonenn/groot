"""OpenRouter Chat Completions for a selected free model and strict structured output."""

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from backend.app.evaluation.provider import ModelError, Reply, token_usage

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_SLUG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,159}"


def _status_error(status: int, usage: dict | None = None) -> ModelError:
    if status == 429:
        return ModelError("provider_rate_limit", usage=usage)
    if status == 401:
        return ModelError("provider_auth", usage=usage)
    if status == 408 or 500 <= status <= 599:
        return ModelError("provider_transient", retryable=True, usage=usage)
    # A 403 may be a moderation or budget rejection even with a valid API key.
    return ModelError("provider_rejected", usage=usage)


def _usage(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    # completion_tokens already includes completion_tokens_details.reasoning_tokens.
    counts = token_usage(
        {
            "input_tokens": value.get("prompt_tokens"),
            "output_tokens": value.get("completion_tokens"),
        }
    )
    if counts is not None and "total_tokens" in value:
        total = value["total_tokens"]
        if type(total) is not int or total != sum(counts.values()):
            return None
    return counts


@dataclass
class OpenRouterChat:
    model: str
    api_key: str = field(repr=False)
    max_output_tokens: int = 12000
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    name: str = field(default="openrouter", init=False)
    live: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if self.model.startswith("openrouter/") or not re.fullmatch(
            MODEL_SLUG + ":free", self.model
        ):
            raise ValueError("invalid_openrouter_free_model")

    @property
    def parameters(self) -> dict:
        return {
            "max_output_tokens": self.max_output_tokens,
            "require_parameters": True,
            "data_collection": "deny",
        }

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        started = time.monotonic()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
            "max_tokens": self.max_output_tokens,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "cqa_answers", "strict": True, "schema": schema},
            },
        }
        try:
            with (
                httpx.Client(
                    timeout=timeout, transport=self.transport, follow_redirects=False
                ) as client,
                client.stream(
                    "POST", CHAT_URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=body
                ) as response,
            ):
                if response.status_code != 200:
                    raise _status_error(response.status_code)
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > timeout:
                        raise ModelError("provider_timeout", retryable=True)
                    if len(raw) + len(chunk) > 2_000_000:
                        raise ModelError("provider_response_too_large")
                    raw.extend(chunk)
            data = json.loads(raw)
        except httpx.TimeoutException:
            raise ModelError("provider_timeout", retryable=True) from None
        except httpx.TransportError:
            raise ModelError("provider_network", retryable=True) from None
        except ValueError, UnicodeError:
            raise ModelError("provider_envelope_invalid") from None

        if not isinstance(data, dict):
            raise ModelError("provider_envelope_invalid")
        usage = _usage(data.get("usage"))
        error = data.get("error")
        if error is not None:
            if not isinstance(error, dict) or type(error.get("code")) is not int:
                raise ModelError("provider_envelope_invalid", usage=usage)
            raise _status_error(error["code"], usage)
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelError("provider_envelope_invalid", usage=usage)
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            raise ModelError("provider_refused", usage=usage)
        if finish != "stop":
            raise ModelError("provider_incomplete", usage=usage)
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ModelError("provider_envelope_invalid", usage=usage)
        if message.get("refusal"):
            raise ModelError("provider_refused", usage=usage)
        if message.get("tool_calls") or message.get("function_call"):
            raise ModelError("provider_envelope_invalid", usage=usage)
        text = message.get("content")
        model = data.get("model")
        if not isinstance(text, str) or not text.strip() or not isinstance(model, str):
            raise ModelError("provider_envelope_invalid", usage=usage)
        # The API can resolve a pinned alias to a dated version; audit the returned ID.
        if not re.fullmatch(MODEL_SLUG + r"(?::[a-z0-9_-]+)?", model):
            raise ModelError("provider_envelope_invalid", usage=usage)
        return Reply(text, model, usage)
