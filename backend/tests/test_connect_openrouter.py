"""The desktop connection tests use a loopback OAuth callback and an offline model."""

import base64
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import connect_openrouter, model_app
from backend.app.evaluation.openrouter import OpenRouterChat
from backend.app.evaluation.openrouter_local import profile_path, save_profile
from backend.app.evaluation.provider import ModelError
from backend.tests.test_model_evaluation import answers
from backend.tests.test_openrouter_evaluation import MODEL, envelope


def test_oauth_checks_state_and_exchanges_only_the_bound_code(monkeypatch):
    challenge = []
    requests = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def browser(url):
            query = parse_qs(urlsplit(url).query)
            challenge.append(query["code_challenge"][0])
            callback = query["callback_url"][0]

            def redirect():
                bad = httpx.get(callback, params={"state": "wrong", "code": "bad"})
                assert bad.status_code == 403
                result = httpx.get(callback, params={"state": query["state"][0], "code": "good"})
                assert result.status_code == 200 and "fixture-key" not in result.text
            requests.append(pool.submit(redirect))

        def exchange(url, json, timeout):
            assert url == "https://openrouter.ai/api/v1/auth/keys"
            digest = hashlib.sha256(json["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge[0]
            assert json["code"] == "good" and json["code_challenge_method"] == "S256"
            return httpx.Response(
                200, json={"key": "fixture-key"}, request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(connect_openrouter.webbrowser, "open", browser)
        monkeypatch.setattr(connect_openrouter.httpx, "post", exchange)
        assert connect_openrouter.sign_in() == "fixture-key"
        requests[0].result()


@pytest.mark.skipif(os.name != "nt", reason="Windows user-scoped credential storage")
def test_verified_profile_becomes_visible_without_changing_gemini(monkeypatch, tmp_path):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("CQA_MODEL_ENABLED", "1")
    monkeypatch.setenv("CQA_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-primary")
    monkeypatch.setenv("CQA_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("CQA_CONTEXT_TOKENS", "1048576")
    monkeypatch.delenv("CQA_FALLBACK_PROVIDER", raising=False)
    reply = envelope()
    reply["choices"][0]["message"]["content"] = json.dumps(answers())
    provider = OpenRouterChat(MODEL, "synthetic-reserve", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=reply),
    ))
    with TestClient(model_app.create_configured_app()) as app:
        assert app.get("/api/model-status").json()["fallback"] is None
        connect_openrouter.check_switch(provider, 131072)
        save_profile(profile_path(), provider, 131072)
        assert b"synthetic-reserve" not in profile_path().read_bytes()
        state = app.get("/api/model-status").json()
        assert state["model"] == "gemini-3.8-flash"
        assert state["fallback"] == {"provider": "openrouter", "model": MODEL}
        profile_path().write_bytes(b"invalid encrypted profile")
        after = app.get("/api/model-status").json()
        assert after["ready"] and after["fallback"] is None


@pytest.mark.skipif(os.name != "nt", reason="Windows user-scoped credential storage")
def test_outage_preserves_authorization_until_probe_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "dev.sqlite3"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["connect_openrouter"])
    routes = [(MODEL, 131072, 8192, True, True)]
    monkeypatch.setattr(connect_openrouter, "free_routes", lambda: routes)
    issued = iter(["one-test-key"])
    monkeypatch.setattr(connect_openrouter, "sign_in", lambda: next(issued))
    checks = iter(["rate_limit", "success"])

    def probe(provider, context):
        assert provider.api_key == "one-test-key" and provider.json_object and context == 131072
        if next(checks) == "rate_limit":
            raise ModelError("provider_rate_limit")

    monkeypatch.setattr(connect_openrouter, "check_switch", probe)
    with pytest.raises(SystemExit, match="No free route passed"):
        connect_openrouter.main()
    active = profile_path()
    pending = active.with_suffix(".pending.dpapi")
    assert pending.is_file() and not active.exists()
    connect_openrouter.main()
    assert active.is_file() and not pending.exists()
