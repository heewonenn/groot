"""OpenRouter Chat Completions for a selected free model and strict structured output."""

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field

import httpx

from backend.app.evaluation.provider import ModelError, Reply, token_usage

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_SLUG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,159}"


def _ground_evidence(schema: dict, payload: str) -> dict:
    """Constrain generated evidence IDs to the source conversation, including on correction."""
    if "evidence" not in schema.get("$defs", {}):
        return schema
    request = json.loads(payload)
    conversation = request.get("evaluation_input", request).get("conversation")
    if not conversation:
        return schema
    grounded = deepcopy(schema)
    answer = grounded["$defs"]["criterion_answer"]["properties"]
    answer["value"] = {
        "anyOf": [{"type": "boolean"}, {"type": "number"}, {"type": "null"}],
        "description": (
            "Applicable requires a non-null value. Boolean and flag require true or false, "
            "including false for no violation. Scale requires one numeric level.value from "
            "the saved criterion (not normalized_value). Null only for N/A or unresolved."
        ),
    }
    fields = grounded["$defs"]["evidence"]["properties"]
    for field_name in ("group_id", "conversation_id"):
        fields[field_name]["enum"] = [conversation[field_name]]
    identifiers = [m["message_id"] for m in conversation["transcript"]]
    # The evaluator reserves 4096 bytes for the request wrapper. Avoid unbounded schema growth.
    if len(json.dumps(identifiers).encode()) <= 2500:
        grounded["$defs"]["source_message_id"] = {"type": "string", "enum": identifiers}
        reference = {"$ref": "#/$defs/source_message_id"}
        fields["message_id"] = reference
        span = fields["span"]["anyOf"][0]["properties"]
        span["start_message_id"] = span["end_message_id"] = reference
    return grounded


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
    json_object: bool = False
    low_reasoning: bool = False
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
            **({"format": "json_object"} if self.json_object else {}),
            **({"reasoning_effort": "low"} if self.low_reasoning else {}),
        }

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        started = time.monotonic()
        schema = _ground_evidence(schema, payload)
        response_format = {"type": "json_schema", "json_schema": {
            "name": "cqa_answers", "strict": True, "schema": schema,
        }}
        if self.json_object:
            instructions += "\nReturn one JSON object matching response_schema. Copy IDs exactly."
            payload = json.dumps({"response_schema": schema, "input": json.loads(payload)},
                                 ensure_ascii=False)
            response_format = {"type": "json_object"}
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
            "max_tokens": self.max_output_tokens,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "response_format": response_format,
        }
        if self.low_reasoning:
            body["reasoning"] = {"effort": "low"}
        try:
            with (
                httpx.Client(
                    timeout=timeout, transport=self.transport, follow_redirects=False
                ) as client,
                client.stream(
                    "POST", CHAT_URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=body
                ) as response,
            ):
                if response.status_code == 404:
                    # Upstream messages may echo user input. Retain only a known category.
                    raw_error = next(response.iter_bytes(chunk_size=16384), b"").lower()
                    if b"data policy" in raw_error or b"privacy" in raw_error:
                        raise ModelError("provider_no_private_route")
                    raise ModelError("provider_no_route")
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
