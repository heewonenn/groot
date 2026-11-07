"""Wire-level OpenRouter checks with local HTTP fixtures; no provider key required."""

import json

import httpx
import pytest

from backend.app.evaluation.contract import INSTRUCTIONS, output_schema
from backend.app.evaluation.openrouter import OpenRouterChat
from backend.app.evaluation.provider import ModelError

MODEL = "testing/fixture-1:free"
UNRESOLVED = "private upstream body"


def envelope():
    return {
        "model": "testing/fixture-1-20260101",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"criteria":[]}',
                    "reasoning": "ignored",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


def run(handler):
    provider = OpenRouterChat(MODEL, "synthetic-token", transport=httpx.MockTransport(handler))
    assert "synthetic-token" not in repr(provider)
    return provider.generate(INSTRUCTIONS, '{"language":"KZ"}', output_schema(), 2)


def test_strict_json_and_no_training_route_with_actual_resolved_model_and_usage():
    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        body = json.loads(request.content)
        assert body == {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": '{"language":"KZ"}'},
            ],
            "max_tokens": 12000,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "cqa_answers", "strict": True, "schema": output_schema()},
            },
        }
        return httpx.Response(200, json=envelope())

    reply = run(handler)
    assert reply.text == '{"criteria":[]}'
    assert reply.model == "testing/fixture-1-20260101"
    assert reply.usage == {"input_tokens": 20, "output_tokens": 5}


@pytest.mark.parametrize("slug", ["openrouter/free", "openrouter/auto:free", "testing/paid", ""])
def test_only_fixed_free_model_is_selected(slug):
    with pytest.raises(ValueError, match="invalid_openrouter_free_model"):
        OpenRouterChat(slug, "synthetic-token")


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"prompt_tokens": True, "completion_tokens": 0},
        {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 9},
    ],
)
def test_unknown_or_inconsistent_usage_is_not_billed_as_zero(usage):
    assert run(lambda _: httpx.Response(200, json={**envelope(), "usage": usage})).usage is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"choices": []}, "provider_envelope_invalid"),
        ({"model": None}, "provider_envelope_invalid"),
        ({"choices": [{"finish_reason": "length"}]}, "provider_incomplete"),
        ({"choices": [{"finish_reason": "content_filter"}]}, "provider_refused"),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "", "refusal": "policy"},
                    }
                ]
            },
            "provider_refused",
        ),
        ({"error": {"code": 429, "message": UNRESOLVED}}, "provider_rate_limit"),
    ],
)
def test_rejects_failed_envelope_even_for_http_ok(changes, code):
    with pytest.raises(ModelError) as err:
        run(lambda _: httpx.Response(200, json={**envelope(), **changes}))
    assert str(err.value) == code
    assert err.value.usage == {"input_tokens": 20, "output_tokens": 5}


@pytest.mark.parametrize(
    ("status", "code", "retry"),
    [
        (307, "provider_rejected", False),
        (401, "provider_auth", False),
        (403, "provider_rejected", False),
        (402, "provider_rejected", False),
        (429, "provider_rate_limit", False),
        (503, "provider_transient", True),
    ],
)
def test_http_errors_are_sanitized_and_keys_cannot_follow_redirect(status, code, retry):
    def handler(request):
        assert request.url.host == "openrouter.ai"
        return httpx.Response(status, text=UNRESOLVED, headers={"Location": "https://elsewhere/"})

    with pytest.raises(ModelError) as err:
        run(handler)
    assert str(err.value) == code
    assert err.value.retryable is retry
