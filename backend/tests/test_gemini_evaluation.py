"""Synthetic Gemini HTTP fixtures; never contact a provider or read real API keys."""

import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import model_app, team_data
from backend.app.evaluation.contract import INSTRUCTIONS, canonical, output_schema
from backend.app.evaluation.gemini import GEMINI_MODEL, GeminiGenerateContent, gemini_usage
from backend.app.evaluation.provider import ModelError, OpenAIResponses

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"


def example(name):
    return json.loads((EXAMPLES / name).read_bytes())


def answers():
    return {"criteria": example("server-response.synthetic.json")["model_result"]["criteria"]}


def envelope(text="{}", **changes):
    return {
        "modelVersion": GEMINI_MODEL,
        "candidates": [
            {"finishReason": "STOP", "content": {"role": "model", "parts": [{"text": text}]}}
        ],
        "usageMetadata": {
            "promptTokenCount": 12,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 7,
            "totalTokenCount": 24,
        },
        **changes,
    }


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for key in (
        "CQA_MODEL",
        "CQA_GEMINI_THINKING_LEVEL",
        "CQA_KNOWLEDGE_PATH",
        "CQA_INPUT_USD_PER_MILLION",
        "CQA_OUTPUT_USD_PER_MILLION",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "synthetic.db"))
    monkeypatch.setenv("CQA_MODEL_ENABLED", "1")
    monkeypatch.setenv("CQA_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("CQA_CONTEXT_TOKENS", "128000")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key-not-real")
    return monkeypatch


def test_gemini_wire_format_and_thinking_usage():
    def handler(request):
        assert (
            str(request.url)
            == f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        )
        assert request.headers["x-goog-api-key"] == "synthetic-secret"
        assert not request.url.query
        body = json.loads(request.content)
        assert body["systemInstruction"] == {"parts": [{"text": INSTRUCTIONS}]}
        assert body["contents"] == [{"role": "user", "parts": [{"text": '{"language":"KZ"}'}]}]
        assert body["generationConfig"] == {
            "maxOutputTokens": 12000,
            "thinkingConfig": {"thinkingLevel": "medium"},
            "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": output_schema()}},
        }
        assert "synthetic-secret" not in request.content.decode()
        data = envelope(canonical(answers()))
        data["candidates"][0]["content"]["parts"].insert(
            0, {"thought": True, "text": "Private thought; not the JSON answer."}
        )
        return httpx.Response(200, json=data)

    provider = GeminiGenerateContent(
        GEMINI_MODEL, "synthetic-secret", transport=httpx.MockTransport(handler)
    )
    reply = provider.generate(INSTRUCTIONS, '{"language":"KZ"}', output_schema(), 3)
    assert json.loads(reply.text) == answers()
    assert reply.usage == {"input_tokens": 12, "output_tokens": 12}
    assert reply.model == GEMINI_MODEL
    assert "synthetic-secret" not in repr(provider)


@pytest.mark.parametrize(
    "usage,expected",
    [
        (None, None),
        ({}, None),
        ({"promptTokenCount": 1}, None),
        ({"promptTokenCount": 1, "totalTokenCount": 1}, {"input_tokens": 1, "output_tokens": 0}),
        (
            {"promptTokenCount": 2, "candidatesTokenCount": 3},
            {"input_tokens": 2, "output_tokens": 3},
        ),
        ({"promptTokenCount": 2, "thoughtsTokenCount": 4}, {"input_tokens": 2, "output_tokens": 4}),
        ({"promptTokenCount": True, "candidatesTokenCount": 1}, None),
        ({"promptTokenCount": 1, "candidatesTokenCount": "5"}, None),
        ({"promptTokenCount": 1, "thoughtsTokenCount": -1}, None),
        ({"promptTokenCount": 1, "candidatesTokenCount": 2, "totalTokenCount": 9}, None),
    ],
)
def test_usage_is_not_fabricated(usage, expected):
    assert gemini_usage(usage) == expected


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (400, "provider_rejected", False),
        (401, "provider_auth", False),
        (403, "provider_auth", False),
        (404, "provider_rejected", False),
        (429, "provider_rate_limit", False),
        (408, "provider_transient", True),
        (503, "provider_transient", True),
        (307, "provider_rejected", False),
    ],
)
def test_http_failures_are_sanitized(status, code, retry):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": {"message": "SECRET"}},
            headers={"location": "https://other.example/"},
        )

    provider = GeminiGenerateContent(GEMINI_MODEL, "key", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as caught:
        provider.generate(INSTRUCTIONS, "{}", output_schema(), 1)
    assert str(caught.value) == code
    assert caught.value.retryable is retry
    assert len(requests) == 1


@pytest.mark.parametrize(
    "kind,code",
    [
        ("blocked", "provider_refused"),
        ("safety", "provider_refused"),
        ("truncated", "provider_incomplete"),
        ("missing_finish", "provider_envelope_invalid"),
        ("bad_finish", "provider_envelope_invalid"),
        ("no_candidates", "provider_envelope_invalid"),
        ("two_candidates", "provider_envelope_invalid"),
        ("bad_content", "provider_envelope_invalid"),
        ("tool_call", "provider_envelope_invalid"),
        ("thought_only", "provider_envelope_invalid"),
        ("missing_model", "provider_envelope_invalid"),
        ("empty_text", "provider_envelope_invalid"),
    ],
)
def test_response_failures_retain_usage(kind, code):
    data = envelope()
    candidate = data["candidates"][0]
    if kind == "blocked":
        data.update(promptFeedback={"blockReason": "SAFETY"}, candidates=[])
    elif kind == "safety":
        candidate["finishReason"] = "SAFETY"
    elif kind == "truncated":
        candidate["finishReason"] = "MAX_TOKENS"
    elif kind == "missing_finish":
        candidate.pop("finishReason")
    elif kind == "bad_finish":
        candidate["finishReason"] = []
    elif kind == "no_candidates":
        data["candidates"] = []
    elif kind == "two_candidates":
        data["candidates"].append(copy.deepcopy(candidate))
    elif kind == "bad_content":
        candidate["content"] = []
    elif kind == "tool_call":
        candidate["content"]["parts"] = [{"functionCall": {"name": "unexpected"}}]
    elif kind == "thought_only":
        candidate["content"]["parts"][0]["thought"] = True
    elif kind == "missing_model":
        data.pop("modelVersion")
    elif kind == "empty_text":
        candidate["content"]["parts"][0]["text"] = " "
    provider = GeminiGenerateContent(
        GEMINI_MODEL, "key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    )
    with pytest.raises(ModelError) as caught:
        provider.generate(INSTRUCTIONS, "{}", output_schema(), 1)
    assert str(caught.value) == code
    assert caught.value.usage == {"input_tokens": 12, "output_tokens": 12}


@pytest.mark.parametrize(
    "failure,code",
    [
        ("network", "provider_network"),
        ("json", "provider_envelope_invalid"),
        ("size", "provider_response_too_large"),
        ("list", "provider_envelope_invalid"),
    ],
)
def test_transport_limits(failure, code):
    def handler(request):
        if failure == "network":
            raise httpx.ConnectError("SECRET", request=request)
        return httpx.Response(
            200, content={"json": b"not json", "size": b"x" * 2_000_001, "list": b"[]"}[failure]
        )

    provider = GeminiGenerateContent(GEMINI_MODEL, "key", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError, match=f"^{code}$"):
        provider.generate(INSTRUCTIONS, "{}", output_schema(), 3)


@pytest.mark.parametrize(
    "model",
    ["../secret", "gemini-3.8-flash?key=x", "gemini-3.8-flash/other", "https://example.org"],
)
def test_model_cannot_override_endpoint(model):
    with pytest.raises(ValueError, match="invalid_gemini_model"):
        GeminiGenerateContent(model, "key")


def test_configuration_selects_provider_and_key_without_requests(configured):
    engine, reason = model_app.configured_evaluator()
    assert reason is None and isinstance(engine.provider, GeminiGenerateContent)
    assert engine.provider.model == GEMINI_MODEL
    configured.setenv("OPENAI_API_KEY", "openai-test")
    configured.setenv("GOOGLE_API_KEY", "ignored-google-test")
    assert model_app.configured_evaluator()[0].provider.api_key == "synthetic-key-not-real"
    configured.delenv("GEMINI_API_KEY")
    assert model_app.configured_evaluator()[1] == "model_configuration_missing"
    configured.setenv("CQA_MODEL", "chosen-openai-model")
    configured.delenv("CQA_MODEL_PROVIDER")
    engine, reason = model_app.configured_evaluator()
    assert reason is None and isinstance(engine.provider, OpenAIResponses)
    assert engine.provider.api_key == "openai-test"
    configured.setenv("CQA_MODEL_PROVIDER", "other")
    assert model_app.configured_evaluator()[1] == "provider_not_supported"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("CQA_GEMINI_THINKING_LEVEL", "minimal"),
        ("CQA_MODEL", "../../wrong"),
        ("CQA_CONTEXT_TOKENS", "nan"),
    ],
)
def test_invalid_configuration_stays_unavailable(configured, setting, value):
    configured.setenv(setting, value)
    assert model_app.configured_evaluator() == (None, "model_configuration_invalid")


@pytest.mark.parametrize("mode", ["success", "correction", "partial", "quota", "transient"])
def test_configured_gemini_job_preserves_contract_and_audit(configured, mode):
    conversation = example("conversation.synthetic.json")
    raw = copy.deepcopy(conversation)
    for message in raw["transcript"]:
        message.pop("message_id")
    team_data.store_group("SYNTHETIC", [team_data.SourceConversation.model_validate(raw)])
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if mode == "quota":
            return httpx.Response(429, json={"error": "secret"})
        if mode == "transient" and len(requests) == 1:
            return httpx.Response(503)
        result = answers()
        if mode == "correction" and len(requests) == 1:
            result["criteria"][0]["evidence"][0]["message_id"] = "invented"
        if mode == "partial":
            result["criteria"][0].update(
                decision_status="unresolved",
                applicability="undetermined",
                value=None,
                na_reason=None,
                review_reasons=["Insufficient evidence"],
            )
        return httpx.Response(200, json=envelope(canonical(result)))

    real_factory = model_app.GeminiGenerateContent
    configured.setattr(
        model_app,
        "GeminiGenerateContent",
        lambda *args, **kwargs: real_factory(
            *args, **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    with TestClient(model_app.create_configured_app()) as client:
        status = client.get("/api/model-status").json()
        assert (
            status["ready"] and status["provider"] == "gemini" and status["model"] == GEMINI_MODEL
        )
        assert requests == []  # Status and startup never call the provider.
        payload = {
            "group_id": "SYNTHETIC",
            "conversation_ids": [conversation["conversation_id"]],
            "checklist_version_id": "freedom-chat@1",
        }
        headers = {"Idempotency-Key": "gemini-once"}
        created = client.post("/api/evaluation-jobs", json=payload, headers=headers)
        assert created.status_code == 202
        for _ in range(200):
            job = client.get("/api/evaluation-jobs/" + created.json()["job_id"]).json()
            if job["items"][0]["status"] in {"completed", "partial", "failed"}:
                break
            time.sleep(0.01)
        expected = "failed" if mode == "quota" else "partial" if mode == "partial" else "completed"
        assert job["items"][0]["status"] == expected
        runs = client.get(
            "/api/evaluation-runs",
            params={"group_id": "SYNTHETIC", "conversation_id": conversation["conversation_id"]},
        ).json()
        metadata = client.get("/api/evaluation-runs/" + runs[0]["evaluation_id"]).json()
        assert metadata["provider"] == "gemini"
        assert metadata["estimated_cost_usd"] is None
        assert "synthetic-key-not-real" not in canonical(metadata)
        assert len(requests) == (2 if mode in {"correction", "transient"} else 1)
        if mode == "quota":
            assert metadata["error_code"] == "provider_rate_limit"
        else:
            card = client.get("/api/evaluations/" + runs[0]["evaluation_id"]).json()
            assert card["generation"]["origin"] == "gemini"
            assert (card["original_result"] is None) == (mode == "partial")
            # Participant 3 consumes this wrapper to display the card and model audit.
            viewer_response = client.get("/api/evaluation-cards/" + runs[0]["evaluation_id"])
            assert viewer_response.status_code == 200
            viewer = viewer_response.json()
            assert viewer["evaluation"] == card
            assert viewer["runtime"]["model_run"]["provider"] == "gemini"
            assert viewer["runtime"]["model_run"]["configured_model"] == GEMINI_MODEL
            assert "synthetic-key-not-real" not in canonical(viewer)
            if mode == "correction":
                assert metadata["token_usage"] == {"input_tokens": 24, "output_tokens": 24}
                correction = json.loads(requests[1]["contents"][0]["parts"][0]["text"])
                assert correction["correction"]["error_code"].startswith("answer_")
        assert (
            client.post("/api/evaluation-jobs", json=payload, headers=headers).json()
            == created.json()
        )
