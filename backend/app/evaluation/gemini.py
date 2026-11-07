"""Gemini generateContent adapter; shared engine owns validation, scoring and retries."""

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from backend.app.evaluation.provider import ModelError, Reply

GEMINI_MODEL = "gemini-3.8-flash"


def gemini_usage(value: object) -> dict | None:
    """Include thinking tokens in output usage; unknown usage must not look free."""
    if not isinstance(value, dict):
        return None
    prompt = value.get("promptTokenCount")
    output = value.get("candidatesTokenCount", 0)
    thoughts = value.get("thoughtsTokenCount", 0)
    if not all(type(n) is int and n >= 0 for n in (prompt, output, thoughts)):
        return None
    if not any(
        k in value for k in ("candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")
    ):
        return None
    total = value.get("totalTokenCount", prompt + output + thoughts)
    if type(total) is not int or total != prompt + output + thoughts:
        return None
    return {"input_tokens": prompt, "output_tokens": output + thoughts}


@dataclass
class GeminiGenerateContent:
    model: str
    api_key: str = field(repr=False)
    max_output_tokens: int = 12000
    thinking_level: str = "medium"
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    name: str = field(default="gemini", init=False)
    live: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        # The model is a path component, never a caller-supplied URL or query.
        if not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{0,119}", self.model):
            raise ValueError("invalid_gemini_model")
        if self.thinking_level not in {"low", "medium", "high"}:
            raise ValueError("invalid_gemini_thinking_level")

    @property
    def parameters(self) -> dict:
        return {
            "max_output_tokens": self.max_output_tokens,
            "thinking_level": self.thinking_level,
            "api": "generateContent",
        }

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        started = time.monotonic()
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": payload}]}],
            "generationConfig": {
                "maxOutputTokens": self.max_output_tokens,
                "thinkingConfig": {"thinkingLevel": self.thinking_level},
                # TextResponseFormat uses an enum, unlike legacy responseMimeType.
                "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}},
            },
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )
        try:
            with httpx.Client(
                timeout=timeout, transport=self.transport, follow_redirects=False
            ) as client:
                with client.stream(
                    "POST", url, headers={"x-goog-api-key": self.api_key}, json=body
                ) as response:
                    if response.status_code == 429:
                        # Daily quota exhaustion cannot be repaired by the engine's short retry.
                        raise ModelError("provider_rate_limit")
                    if response.status_code == 408 or response.status_code >= 500:
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
        usage = gemini_usage(data.get("usageMetadata"))
        feedback = data.get("promptFeedback", {})
        if not isinstance(feedback, dict):
            raise ModelError("provider_envelope_invalid", usage=usage)
        if feedback.get("blockReason") not in (None, "BLOCK_REASON_UNSPECIFIED"):
            raise ModelError("provider_refused", usage=usage)
        candidates = data.get("candidates")
        if (
            not isinstance(candidates, list)
            or len(candidates) != 1
            or not isinstance(candidates[0], dict)
        ):
            raise ModelError("provider_envelope_invalid", usage=usage)
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if not isinstance(finish, str):
            raise ModelError("provider_envelope_invalid", usage=usage)
        if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            raise ModelError("provider_refused", usage=usage)
        if finish != "STOP":
            raise ModelError("provider_incomplete", usage=usage)
        content = candidate.get("content")
        if not isinstance(content, dict) or content.get("role") != "model":
            raise ModelError("provider_envelope_invalid", usage=usage)
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise ModelError("provider_envelope_invalid", usage=usage)
        texts = []
        for part in parts:
            if not isinstance(part, dict) or type(part.get("thought", False)) is not bool:
                raise ModelError("provider_envelope_invalid", usage=usage)
            if part.get("thought"):
                continue
            if not isinstance(part.get("text"), str):
                raise ModelError("provider_envelope_invalid", usage=usage)
            texts.append(part["text"])
        model = data.get("modelVersion")
        text = "".join(texts)
        if not text.strip() or not isinstance(model, str) or not model.strip():
            raise ModelError("provider_envelope_invalid", usage=usage)
        return Reply(text, model, usage)
