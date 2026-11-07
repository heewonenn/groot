"""Offline failover policy and provenance checks, using only synthetic data."""

import pytest

from backend.app.api.checklists import get_repository
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.provider import ModelError
from backend.app.evaluation.store import RunStore
from backend.tests.test_model_evaluation import Scripted, answers, example


@pytest.fixture
def dialogue(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "runs.sqlite3"))
    rules = get_repository().get_version("freedom-chat@1").model_dump(mode="json")
    return example("conversation.synthetic.json"), rules


def alternate(steps):
    provider = Scripted(steps)
    provider.name = "alternate"
    provider.model = "alternate-model"
    return provider


def test_failover_records_actual_source_and_does_not_change_the_next_job(dialogue):
    # The switch consumes the network retry; the fallback can still correct invalid JSON once.
    primary = Scripted([ModelError("provider_rate_limit", usage={
        "input_tokens": 7, "output_tokens": 0,
    }), answers()])
    fallback = alternate(["{", answers()])
    evaluator = ModelEvaluator(
        primary, RunStore(), fallback_provider=fallback,
        input_usd_per_million=1, output_usd_per_million=2,
    )
    result = evaluator("switched", *dialogue, {"model": primary.model})
    run = evaluator.store.get("switched")["metadata"]
    assert result["generation"]["origin"] == run["provider"] == fallback.name
    assert result["generation"]["model_id"] == run["resolved_model"] == fallback.model
    assert run["configured_provider"] == primary.name and run["configured_model"] == primary.model
    assert [a["provider"] for a in run["attempts"]] == [primary.name, fallback.name, fallback.name]
    assert fallback.requests[1][1]["correction"]["error_code"] == "answer_json_invalid"
    assert run["token_usage"] == {"input_tokens": 207, "output_tokens": 100}
    assert run["estimated_cost_usd"] is None
    assert evaluator("next", *dialogue, {})["generation"]["origin"] == primary.name
    assert evaluator.store.get("next")["metadata"]["estimated_cost_usd"] == 0.0002


@pytest.mark.parametrize("code", ["provider_auth", "provider_refused", "provider_rejected"])
def test_auth_refusal_and_configuration_errors_never_send_to_another_service(dialogue, code):
    fallback = alternate([])
    evaluator = ModelEvaluator(Scripted([ModelError(code)]), RunStore(), fallback_provider=fallback)
    with pytest.raises(ModelError, match=code):
        evaluator("reject", *dialogue, {})
    assert not fallback.requests


def test_fabricated_evidence_is_only_corrected_at_original_provider(dialogue):
    wrong = answers()
    wrong["criteria"][0]["evidence"][0]["message_id"] = "missing"
    primary = Scripted([wrong, wrong])
    fallback = alternate([])
    evaluator = ModelEvaluator(primary, RunStore(), fallback_provider=fallback)
    with pytest.raises(ModelError, match="answer_"):
        evaluator("evidence", *dialogue, {})
    assert len(primary.requests) == 2 and not fallback.requests
    assert evaluator.store.get("evidence")["result"] is None


def test_fallback_has_its_own_context_window_and_output_reserve(dialogue):
    primary = Scripted([ModelError("provider_transient", retryable=True)])
    fallback = alternate([])
    fallback.parameters = {"max_output_tokens": 100000}
    evaluator = ModelEvaluator(primary, RunStore(), context_token_limit=200000,
                               fallback_provider=fallback, fallback_context_token_limit=40000)
    with pytest.raises(ModelError, match="context_budget_exceeded"):
        evaluator("small-context", *dialogue, {})
    assert len(primary.requests) == 1 and not fallback.requests
    run = evaluator.store.get("small-context")["metadata"]
    assert run["context_budget_used"] > run["context_token_limit"]
    assert len(run["attempts"]) == 1


def test_fallback_repeated_transient_error_leaves_failure_and_unknown_usage(dialogue):
    primary = Scripted([ModelError("provider_network", retryable=True)])
    fallback = alternate([ModelError("provider_timeout", retryable=True)])
    evaluator = ModelEvaluator(primary, RunStore(), fallback_provider=fallback)
    with pytest.raises(ModelError, match="provider_timeout"):
        evaluator("both-down", *dialogue, {})
    run = evaluator.store.get("both-down")
    assert run["result"] is None and run["metadata"]["token_usage"] is None
    assert [a["error_code"] for a in run["metadata"]["attempts"]] == [
        "provider_network", "provider_timeout",
    ]
