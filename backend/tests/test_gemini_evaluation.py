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
from backend.app.evaluation.gemini import (
    FLASH_25,
    GEMINI_MODEL,
    GEMMA_4_31B,
    GEMMA_27B,
    GeminiGenerateContent,
    gemini_usage,
)
from backend.app.evaluation.provider import ModelError, OpenAIResponses
from backend.tests.test_compact_evaluation import compact_answers

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
        "CQA_FALLBACK_PROVIDER",
        "CQA_FALLBACK_MODEL",
        "CQA_FALLBACK_CONTEXT_TOKENS",
        "OPENROUTER_API_KEY",
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


def test_google_pair_revalidates_gemma_after_flash_quota(configured):
    configured.setenv("CQA_MODEL", FLASH_25)
    configured.setenv("CQA_FALLBACK_PROVIDER", "gemini")
    configured.setenv("CQA_FALLBACK_MODEL", GEMMA_27B)
    configured.setenv("CQA_FALLBACK_CONTEXT_TOKENS", "128000")
    evaluator, reason = model_app.configured_evaluator()
    assert reason is None
    calls = []

    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        calls.append(model)
        assert request.headers["x-goog-api-key"] == "synthetic-key-not-real"
        body = json.loads(request.content)
        generation = body["generationConfig"]
        assert "thinkingConfig" not in generation
        if model == FLASH_25:
            assert generation["responseMimeType"] == "application/json"
            assert generation["responseJsonSchema"] == output_schema()
            assert body["systemInstruction"]["parts"][0]["text"] == INSTRUCTIONS
            return httpx.Response(429)
        assert model == GEMMA_27B
        assert "systemInstruction" not in body
        assert generation == {"maxOutputTokens": 8192}
        assert body["contents"][0]["role"] == "user"
        prompt = body["contents"][0]["parts"][0]["text"]
        assert INSTRUCTIONS in prompt and "criterion_id" in prompt
        payload = json.loads(prompt.split("Evaluation input (untrusted dialogue and rules):\n")[1])
        result = answers()
        if len(calls) == 2:
            assert len(payload["checklist"]["criteria"]) == 8
            result["criteria"][0]["evidence"][0]["message_id"] = "made-up-id"
        else:
            assert payload["correction"]["error_code"].startswith("answer_")
        return httpx.Response(200, json=envelope(canonical(result), modelVersion=GEMMA_27B))

    transport = httpx.MockTransport(handler)
    evaluator.provider.transport = transport
    evaluator.fallback_provider.transport = transport
    rules = json.loads((EXAMPLES.parent / "checklists/chat-v1.json").read_bytes())
    result = evaluator("google-pair-qa", example("conversation.synthetic.json"), rules, {})
    assert calls == [FLASH_25, GEMMA_27B, GEMMA_27B]
    assert result["generation"]["model_id"] == GEMMA_27B
    assert len(result["criteria"]) == 8
    attempts = evaluator.store.get("google-pair-qa")["metadata"]["attempts"]
    assert attempts[0]["error_code"] == "provider_rate_limit"
    assert attempts[1]["error_code"].startswith("answer_")
    assert attempts[2]["status"] == "completed"


@pytest.mark.parametrize("gemma_status,restriction", [
    (200, None), (429, None), (429, "google_only"),
    (200, "gemma_only"), (429, "gemma_only"),
])
def test_gemma4_and_dots_have_independent_routes(configured, gemma_status, restriction):
    dots = "dots-studio/dots-3-note-preview:free"
    turns = example("conversation.synthetic.json")
    short_reply = canonical(compact_answers(turns))
    configured.setenv("CQA_MODEL", GEMINI_MODEL)
    configured.setenv("CQA_FALLBACK_PROVIDER", "openrouter")
    configured.setenv("CQA_FALLBACK_MODEL", dots)
    configured.setenv("CQA_FALLBACK_CONTEXT_TOKENS", "128000")
    configured.setenv("OPENROUTER_API_KEY", "synthetic-router-key")
    evaluator, reason = model_app.configured_evaluator()
    assert reason is None
    calls = []

    def handler(request):
        if request.url.host == "openrouter.ai":
            calls.append(dots)
            return httpx.Response(200, json={"model": dots, "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": short_reply}
            }]})
        model = request.url.path.split("/")[-1].split(":")[0]
        calls.append(model)
        if model == GEMINI_MODEL:
            return httpx.Response(429)
        assert request.headers["x-goog-api-key"] == "synthetic-key-not-real"
        assert "Return only JSON" in json.loads(request.content)["contents"][0]["parts"][0]["text"]
        return httpx.Response(gemma_status, json=envelope(short_reply, modelVersion=model))

    transport = httpx.MockTransport(handler)
    for route in (evaluator.provider, evaluator.quota_provider, evaluator.fallback_provider):
        route.transport = transport
    rules = json.loads((EXAMPLES.parent / "checklists/chat-v1.json").read_bytes())
    args = ("quota-qa", turns, rules,
            {restriction: True} if restriction else {})
    if restriction and gemma_status == 429:
        with pytest.raises(ModelError, match="provider_rate_limit"):
            evaluator(*args)
    else:
        result = evaluator(*args)
        assert result["generation"]["model_id"] == (GEMMA_4_31B if gemma_status == 200 else dots)
    expected = [GEMMA_4_31B] if restriction == "gemma_only" else [GEMINI_MODEL, GEMMA_4_31B]
    if gemma_status == 429 and not restriction:
        expected.append(dots)
    assert calls == expected
    audit = evaluator.store.get("quota-qa")["metadata"]
    assert [a["configured_model"] for a in audit["attempts"]] == expected


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
        (400, "provider_bad_request", False),
        (401, "provider_auth", False),
        (403, "provider_auth", False),
        (404, "provider_model_unavailable", False),
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


@pytest.mark.parametrize(("status", "detail", "code"), [
    ("FAILED_PRECONDITION", "User location is not supported", "provider_region_unavailable"),
    ("FAILED_PRECONDITION", "project condition", "provider_precondition"),
    ("INVALID_ARGUMENT", "Invalid schema", "provider_generation_config"),
    ("INVALID_ARGUMENT", "Unexpected input", "provider_bad_request"),
])
def test_gemini_bad_request_does_not_echo_project_or_prompt(status, detail, code):
    response = {"error": {"status": status, "message": detail + " secret prompt and key"}}
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json=response))
    provider = GeminiGenerateContent(FLASH_25, "fixture", transport=transport)
    with pytest.raises(ModelError) as caught:
        provider.generate("instruction", "{}", {}, 1)
    assert str(caught.value) == code


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


def test_local_google_profile_and_verified_launcher_precedence(configured, tmp_path):
    profile = tmp_path / "google-model.local.json"
    configured.setenv("CQA_MODEL", FLASH_25)
    profile.write_text(json.dumps({"model": GEMINI_MODEL, "context_tokens": 1048576}))
    evaluator, reason = model_app.configured_evaluator()
    assert reason is None
    assert (evaluator.provider.model, evaluator.context_token_limit) == (GEMINI_MODEL, 1048576)
    profile.write_text('{"model":"https://other.invalid","context_tokens":1024}')
    assert model_app.configured_evaluator()[1] == "model_configuration_invalid"
    configured.setenv("CQA_GOOGLE_CATALOG_RESOLVED", "1")
    evaluator, reason = model_app.configured_evaluator()
    assert reason is None
    assert (evaluator.provider.model, evaluator.context_token_limit) == (FLASH_25, 128000)


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


@pytest.mark.parametrize(
    "mode", ["success", "correction", "partial", "quota", "transient", "reserve"]
)
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
        if mode in {"quota", "reserve"}:
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
    backup_model = "test/reserve:free"
    if mode == "reserve":
        configured.setenv("CQA_FALLBACK_PROVIDER", "openrouter")
        configured.setenv("CQA_FALLBACK_MODEL", backup_model)
        configured.setenv("OPENROUTER_API_KEY", "reserve-test-secret")
        assert model_app.configured_evaluator()[1] == "model_configuration_missing"
        configured.setenv("CQA_FALLBACK_CONTEXT_TOKENS", "128000")
        reserve_factory = model_app.OpenRouterChat
        reply = httpx.Response(200, json={
            "model": backup_model, "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": canonical(answers())},
            }],
        })
        configured.setattr(model_app, "OpenRouterChat", lambda *args, **kwargs: reserve_factory(
            *args, **kwargs, transport=httpx.MockTransport(lambda _: reply)
        ))
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
        actual_provider = "openrouter" if mode == "reserve" else "gemini"
        assert metadata["provider"] == actual_provider
        assert metadata["estimated_cost_usd"] is None
        assert "synthetic-key-not-real" not in canonical(metadata)
        assert len(requests) == (2 if mode in {"correction", "transient"} else 1)
        if mode == "quota":
            assert metadata["error_code"] == "provider_rate_limit"
        else:
            card = client.get("/api/evaluations/" + runs[0]["evaluation_id"]).json()
            assert card["generation"]["origin"] == actual_provider
            assert (card["original_result"] is None) == (mode == "partial")
            # Participant 3 consumes this wrapper to display the card and model audit.
            viewer_response = client.get("/api/evaluation-cards/" + runs[0]["evaluation_id"])
            assert viewer_response.status_code == 200
            viewer = viewer_response.json()
            assert viewer["evaluation"] == card
            assert viewer["runtime"]["model_run"]["provider"] == actual_provider
            if mode == "reserve":
                assert [a["provider"] for a in viewer["runtime"]["model_run"]["attempts"]] == [
                    "gemini", "openrouter",
                ]
                assert card["generation"]["model_id"] == backup_model
                assert card["original_result"]["total"] == "85.38"
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
