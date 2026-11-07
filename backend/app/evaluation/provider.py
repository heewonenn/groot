"""Small provider boundary; no network requests happen during configuration."""

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class ModelError(Exception):
    """Controlled codes only: never provider bodies, transcript text or keys."""

    def __init__(
        self, code: str, *, retryable: bool = False, usage: dict | None = None,
        correction_hint: str | None = None,
    ):
        self.code, self.retryable, self.usage = code, retryable, usage
        self.correction_hint = correction_hint
        super().__init__(code)


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    usage: dict | None = None


class Provider(Protocol):
    name: str
    model: str
    live: bool

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply: ...


def token_usage(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    keys = ("input_tokens", "output_tokens")
    if not all(type(value.get(k)) is int and value[k] >= 0 for k in keys):
        return None
    return {k: value[k] for k in keys}


@dataclass
class OpenAIResponses:
    """Responses API + Structured Outputs; the operator must choose a model."""

    model: str
    api_key: str = field(repr=False)
    max_output_tokens: int = 12000
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    name: str = field(default="openai", init=False)
    live: bool = field(default=True, init=False)

    @property
    def parameters(self) -> dict:
        return {"max_output_tokens": self.max_output_tokens, "store": False}

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        started = time.monotonic()
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": payload,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cqa_answers",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            with httpx.Client(
                timeout=timeout, transport=self.transport, follow_redirects=False
            ) as client:
                with client.stream(
                    "POST",
                    "https://api.openai.com/v1/responses",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    if response.status_code in (408, 429) or response.status_code >= 500:
                        raise ModelError("provider_transient", retryable=True)
                    if response.status_code in (401, 403):
                        raise ModelError("provider_auth")
                    if response.status_code != 200:
                        raise ModelError("provider_rejected")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > timeout:
                            raise ModelError("provider_timeout", retryable=True)
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ModelError("provider_response_too_large")
                        chunks.append(chunk)
            data = json.loads(b"".join(chunks))
        except httpx.TransportError:
            raise ModelError("provider_network", retryable=True) from None
        except ValueError, UnicodeError:
            raise ModelError("provider_envelope_invalid") from None
        if not isinstance(data, dict):
            raise ModelError("provider_envelope_invalid")
        usage = token_usage(data.get("usage"))
        output = data.get("output")
        if not isinstance(output, list):
            raise ModelError("provider_envelope_invalid", usage=usage)
        texts = []
        for item in output:
            if not isinstance(item, dict):
                raise ModelError("provider_envelope_invalid", usage=usage)
            if item.get("type") != "message":
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                raise ModelError("provider_envelope_invalid", usage=usage)
            for part in content:
                if not isinstance(part, dict):
                    raise ModelError("provider_envelope_invalid", usage=usage)
                if part.get("type") == "refusal":
                    raise ModelError("provider_refused", usage=usage)
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if data.get("status") != "completed":
            raise ModelError("provider_incomplete", usage=usage)
        model = data.get("model")
        if not texts or not isinstance(model, str) or not model.strip():
            raise ModelError("provider_envelope_invalid", usage=usage)
        return Reply("".join(texts), model, usage)
