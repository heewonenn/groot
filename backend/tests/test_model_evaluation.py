"""Synthetic tests only: no provider requests, paid inference or DEV ground truth."""

import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import team_data
from backend.app.api.checklists import get_repository
from backend.app.evaluation.contract import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    canonical,
    digest,
    evidence_quotes,
    output_schema,
    parse_answers,
)
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.knowledge import Knowledge
from backend.app.evaluation.provider import ModelError, OpenAIResponses, Reply
from backend.app.evaluation.quality import measure, review_template
from backend.app.evaluation.store import RunStore
from backend.app.model_app import create_configured_app, create_model_app

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"


def example(name):
    return json.loads((EXAMPLES / name).read_bytes())


def answers():
    return {"criteria": example("server-response.synthetic.json")["model_result"]["criteria"]}


class Scripted:
    name, model, live = "synthetic_test", "synthetic-model", False

    def __init__(self, steps):
        self.steps, self.requests = list(steps), []

    def generate(self, instructions, payload, schema, timeout):
        self.requests.append((instructions, json.loads(payload), schema, timeout))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(json.loads(payload))
        return Reply(
            step if isinstance(step, str) else canonical(step),
            self.model,
            {"input_tokens": 100, "output_tokens": 50},
        )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "test.db"))
    conv = example("conversation.synthetic.json")
    raw = copy.deepcopy(conv)
    for message in raw["transcript"]:
        message.pop("message_id")
    team_data.store_group("SYNTHETIC", [team_data.SourceConversation.model_validate(raw)])
    rules = get_repository().get_version("freedom-chat@1").model_dump(mode="json")
    return conv, rules, RunStore()


def evaluate(setup, steps, **kwargs):
    conv, rules, store = setup
    provider = Scripted(steps)
    evaluator = ModelEvaluator(provider, store, **kwargs)
    result = evaluator("run", conv, rules, {})
    return result, provider, store.get("run")


def test_success_saved_input_and_server_quotes(setup):
    setup[0]["audio_url"] = "https://private.example/?sig=DO_NOT_SEND"
    setup[0]["transcript"][0]["private_extra"] = "DO_NOT_SEND"
    result, provider, run = evaluate(setup, [answers()])
    assert len(provider.requests) == 1
    assert "DO_NOT_SEND" not in canonical(provider.requests)
    assert provider.requests[0][1]["checklist"] == setup[1]
    assert "scale level descriptions" in provider.requests[0][0]
    assert result["generation"]["mode"] == "demo"
    assert result["generation"]["use_for_model_quality"] is False
    assert run["metadata"]["status"] == "completed"
    assert run["metadata"]["token_usage"] == {"input_tokens": 100, "output_tokens": 50}
    assert run["metadata"]["estimated_cost_usd"] is None
    assert RunStore().get("run") == run
    quotes = evidence_quotes(result, run["snapshot"]["payload"]["conversation"])
    assert quotes[0]["messages"][0]["speaker"] == "operator"
    assert quotes[0]["messages"][0]["text"] == setup[0]["transcript"][3]["text"]


@pytest.mark.parametrize(
    "issue",
    [
        "missing",
        "duplicate",
        "foreign_group",
        "foreign_message",
        "scale_bool",
        "scale_range",
        "na_forbidden",
        "na_reason",
        "blank_reason",
        "audio",
        "knowledge",
        "absence",
        "anchor",
        "reverse_span",
        "unresolved",
        "empty_scope",
    ],
)
def test_bad_answers_one_correction_then_fail(setup, issue):
    value = answers()
    first = value["criteria"][0]
    if issue == "missing":
        value["criteria"].pop()
    elif issue == "duplicate":
        value["criteria"][1] = copy.deepcopy(first)
    elif issue == "foreign_group":
        first["evidence"][0]["group_id"] = "FOREIGN"
    elif issue == "foreign_message":
        first["evidence"][0]["message_id"] = "foreign:m0001"
    elif issue == "scale_bool":
        value["criteria"][5]["value"] = True
    elif issue == "scale_range":
        value["criteria"][5]["value"] = 3.14159
    elif issue == "na_forbidden":
        first.update(applicability="not_applicable", value=None, na_reason="because")
    elif issue == "na_reason":
        value["criteria"][1]["na_reason"] = " "
    elif issue == "blank_reason":
        first["reason"] = " "
    elif issue == "audio":
        first["analysis_scope"] = ["audio"]
    elif issue == "knowledge":
        first["knowledge_references"] = [{"source_version_id": "made-up", "fragment_id": "x"}]
    elif issue == "absence":
        first["evidence"][0].update(kind="absence", span=None)
    elif issue in {"anchor", "reverse_span"}:
        first["evidence"][0]["span"] = {
            "start_message_id": "synthetic-contract-001:m0005",
            "end_message_id": "synthetic-contract-001:m0006"
            if issue == "anchor"
            else "synthetic-contract-001:m0001",
        }
    elif issue == "unresolved":
        first.update(
            decision_status="unresolved",
            applicability="undetermined",
            value=None,
            review_reasons=[],
        )
    else:
        first["analysis_scope"] = []
    provider = Scripted([value, value])
    evaluator = ModelEvaluator(provider, setup[2])
    with pytest.raises(ModelError, match="answer_"):
        evaluator("bad", setup[0], setup[1], {})
    assert len(provider.requests) == 2
    assert provider.requests[1][1]["correction"]["error_code"].startswith("answer_")
    if issue == "scale_range":
        hint = provider.requests[1][1]["correction"]["validation_hint"]
        assert value["criteria"][5]["criterion_id"] in hint
    if issue == "foreign_message":
        hint = provider.requests[1][1]["correction"]["validation_hint"]
        assert hint == "Evidence outside conversation"
    run = setup[2].get("bad")
    assert run["result"] is None
    assert run["metadata"]["status"] == "failed"
    assert run["metadata"]["token_usage"]["input_tokens"] == 200


@pytest.mark.parametrize(
    "invalid",
    [
        "not JSON",
        '{"criteria":[],"criteria":[]}',
        '{"criteria": NaN}',
        '{"criteria":[]} trailing',
        '{"criteria": [], "total": 100}',
    ],
)
def test_invalid_json_corrected_and_cost_counts_both_attempts(setup, invalid):
    result, provider, run = evaluate(
        setup,
        [invalid, answers()],
        input_usd_per_million=1,
        output_usd_per_million=2,
    )
    assert len(provider.requests) == 2
    assert result["criteria"]
    assert run["metadata"]["estimated_cost_usd"] == pytest.approx(0.0004)


def test_retry_budgets_independent_and_unknown_cost_not_zero(setup):
    result, provider, run = evaluate(
        setup,
        [
            ModelError("provider_network", retryable=True),
            "{",
            answers(),
        ],
        input_usd_per_million=1,
        output_usd_per_million=1,
    )
    assert len(provider.requests) == 3 and result
    assert run["metadata"]["token_usage"] is None
    assert run["metadata"]["estimated_cost_usd"] is None


def test_network_retry_exhaustion_and_duplicate_id_does_not_call(setup):
    provider = Scripted([ModelError("provider_transient", retryable=True)] * 3)
    evaluator = ModelEvaluator(provider, setup[2])
    with pytest.raises(ModelError, match="provider_transient"):
        evaluator("network", setup[0], setup[1], {})
    assert len(provider.requests) == 2
    with pytest.raises(ModelError, match="already_used"):
        evaluator("network", setup[0], setup[1], {})
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    "settings",
    [
        {"model": "other"},
        {"provider": "other"},
        {"seed": 2},
        {"temperature": 0},
        {"audio_version": "raw"},
        {"knowledge_version": "missing"},
        {"prompt_version": "old"},
    ],
)
def test_unsupported_settings_rejected_without_call(setup, settings):
    provider = Scripted([])
    with pytest.raises(ModelError, match="settings_not_supported"):
        ModelEvaluator(provider, setup[2])("settings", setup[0], setup[1], settings)
    assert not provider.requests


def test_partial_result_has_no_score_and_no_na_fallback(setup):
    value = answers()
    value["criteria"][0].update(
        decision_status="unresolved",
        applicability="undetermined",
        value=None,
        evidence=[],
        na_reason=None,
        reason="Product source absent.",
        review_reasons=["Missing knowledge"],
    )
    result, _, run = evaluate(setup, [value])
    assert run["metadata"]["status"] == "partial"
    from backend.app.evaluation.contract import validate_result
    from backend.app.schemas.checklists import ChecklistVersion

    card = validate_result(
        result, run["snapshot"]["payload"], ChecklistVersion.model_validate(setup[1])
    )
    assert card["original_result"] is None and card["final_result"] is None


@pytest.mark.parametrize("language", ["RU", "KZ", "EN"])
def test_full_input_ids_roles_language_preserved(setup, language):
    setup[0]["language"] = language
    _, provider, _ = evaluate(setup, [answers()])
    conv = provider.requests[0][1]["conversation"]
    assert conv["language"] == language
    assert conv["transcript"] == setup[0]["transcript"]


def test_changed_rule_and_new_criterion_change_payload_and_fingerprint(setup):
    provider = Scripted([answers()])
    engine = ModelEvaluator(provider, setup[2])
    engine("old", setup[0], setup[1], {})
    rules = copy.deepcopy(setup[1])
    rules.update(
        version=2, checklist_version_id="freedom-chat@2", previous_version_id="freedom-chat@1"
    )
    rules["criteria"][0]["instruction"] = "Check greeting only. Introduction is not required."
    extra = {**rules["criteria"][0], "id": "new-rule", "title": "New criterion"}
    rules["criteria"].append(extra)
    new_answers = answers()
    new_answers["criteria"].append(
        {**copy.deepcopy(new_answers["criteria"][0]), "criterion_id": "new-rule"}
    )
    provider.steps.append(new_answers)
    result = engine("new", setup[0], rules, {})
    assert len(result["criteria"]) == 9
    assert provider.requests[1][1]["checklist"] == rules
    assert (
        setup[2].get("old")["metadata"]["input_fingerprint"]
        != (setup[2].get("new")["metadata"]["input_fingerprint"])
    )
    assert setup[2].get("old")["result"]["checklist_version_id"] == "freedom-chat@1"


def test_knowledge_references_and_version(setup):
    knowledge = Knowledge.model_validate(
        {
            "source_version_id": "official-2026-01",
            "reviewed_by": "test reviewer",
            "as_of": "2026-01-01",
            "fragments": [
                {
                    "fragment_id": "download",
                    "title": "Справки",
                    "source_url": "https://example.org/official",
                    "text": "Как скачать справку.",
                }
            ],
        }
    )
    value = answers()
    value["criteria"][0]["analysis_scope"].append("knowledge")
    value["criteria"][0]["knowledge_references"] = [
        {
            "source_version_id": knowledge.source_version_id,
            "fragment_id": "download",
        }
    ]
    _, provider, run = evaluate(setup, [value], knowledge=knowledge)
    assert provider.requests[0][1]["knowledge"][0]["fragment_id"] == "download"
    assert run["metadata"]["knowledge_hash"] == knowledge.fingerprint
    assert knowledge.retrieve("unmatched") == []


def envelope(text, **changes):
    return {
        "status": "completed",
        "model": "chosen-model-snapshot",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 12, "output_tokens": 5},
        **changes,
    }


def test_responses_wire_format_and_usage():
    def handler(request):
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"] == output_schema()
        assert body["model"] == "chosen-model"
        assert request.headers["authorization"] == "Bearer secret-test"
        return httpx.Response(200, json=envelope(canonical(answers())))

    provider = OpenAIResponses(
        "chosen-model", "secret-test", transport=httpx.MockTransport(handler)
    )
    reply = provider.generate(INSTRUCTIONS, "{}", output_schema(), 3)
    assert parse_answers(reply.text) == answers()
    assert reply.usage == {"input_tokens": 12, "output_tokens": 5}
    assert "secret-test" not in repr(provider)


@pytest.mark.parametrize(
    "status,body,code,retry",
    [
        (401, {"error": "SECRET"}, "provider_auth", False),
        (429, {"error": "SECRET"}, "provider_transient", True),
        (500, {"error": "SECRET"}, "provider_transient", True),
        (400, {"error": "SECRET"}, "provider_rejected", False),
        (200, envelope("{}", status="incomplete"), "provider_incomplete", False),
        (
            200,
            envelope(
                "{}",
                output=[{"type": "message", "content": [{"type": "refusal", "refusal": "SECRET"}]}],
            ),
            "provider_refused",
            False,
        ),
        (200, [], "provider_envelope_invalid", False),
    ],
)
def test_provider_failures_safe(status, body, code, retry):
    provider = OpenAIResponses(
        "chosen-model",
        "key",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(ModelError) as caught:
        provider.generate(INSTRUCTIONS, "{}", output_schema(), 1)
    assert str(caught.value) == code and caught.value.retryable is retry
    assert "SECRET" not in str(caught.value)
    if code in {"provider_incomplete", "provider_refused"}:
        assert caught.value.usage == {"input_tokens": 12, "output_tokens": 5}


def test_job_api_to_model_card_metadata_and_persistence(setup):
    provider = Scripted([answers()])
    engine = ModelEvaluator(provider, setup[2])
    with TestClient(create_model_app(engine)) as client:
        assert client.get("/api/capabilities").json()["model_available"] is True
        assert client.get("/api/model-status").json()["audio_analysed"] is False
        payload = {
            "group_id": "SYNTHETIC",
            "conversation_ids": [setup[0]["conversation_id"]],
            "checklist_version_id": "freedom-chat@1",
        }
        url = "/api/evaluation-jobs"
        created = client.post(url, json=payload, headers={"Idempotency-Key": "once"})
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        for _ in range(100):
            job = client.get(f"{url}/{job_id}").json()
            if job["items"][0]["status"] in {"completed", "failed", "partial"}:
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        eid = job["items"][0]["evaluation_id"]
        card = client.get("/api/evaluations/" + eid).json()
        assert card["original_result"] is not None
        assert client.get("/api/evaluation-runs/" + eid).json()["status"] == "completed"
        assert client.get(f"/api/evaluation-runs/{eid}/evidence").json()
        assert (
            client.get(
                "/api/evaluation-runs",
                params={
                    "group_id": "SYNTHETIC",
                    "conversation_id": setup[0]["conversation_id"],
                },
            ).json()[0]["evaluation_id"]
            == eid
        )
        assert client.post(url, json=payload, headers={"Idempotency-Key": "once"}).json() == (
            created.json()
        )
        assert len(provider.requests) == 1
    with TestClient(create_model_app(None, "model_disabled")) as client:
        assert client.get("/api/evaluation-runs/" + eid).json()["status"] == "completed"
        assert client.get("/api/evaluation-runs/missing").status_code == 404


def test_configuration_stays_disabled_without_explicit_enable(setup, monkeypatch):
    monkeypatch.delenv("CQA_MODEL_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "TEST-NOT-A-REAL-KEY")
    with TestClient(create_configured_app()) as client:
        assert client.get("/api/model-status").json()["reason"] == "model_disabled"
    monkeypatch.setenv("CQA_MODEL_ENABLED", "1")
    monkeypatch.delenv("CQA_MODEL", raising=False)
    with TestClient(create_configured_app()) as client:
        assert client.get("/api/model-status").json()["reason"] == "model_configuration_missing"


def test_quality_refuses_unreviewed_and_synthetic_labels(setup):
    template = review_template("SYNTHETIC", "freedom-chat@1")
    assert template["is_ground_truth"] is False
    with pytest.raises(ValueError, match="ground_truth"):
        measure(template, setup[2])
    _, _, run = evaluate(setup, [answers()])
    case = template["cases"][0]
    assert case["input_hash"] == digest(run["snapshot"]["payload"]["conversation"])
    template["is_ground_truth"] = True
    case.update(split="heldout", human_reviewed=True, reviewer="Test", evaluation_id="run")
    with pytest.raises(ValueError, match="synthetic_prediction"):
        measure(template, setup[2])


def test_invalid_input_and_size_rejected_without_provider(setup):
    provider = Scripted([])
    engine = ModelEvaluator(provider, setup[2], max_input_chars=10)
    with pytest.raises(ModelError, match="input_too_large"):
        engine("large", setup[0], setup[1], {})
    setup[0]["transcript"][1]["message_id"] = setup[0]["transcript"][0]["message_id"]
    with pytest.raises(ModelError, match="input_invalid"):
        engine("duplicate", setup[0], setup[1], {})
    assert provider.requests == []


def test_configured_context_budget_is_checked_before_call(setup):
    provider = Scripted([])
    with pytest.raises(ModelError, match="context_budget_exceeded"):
        ModelEvaluator(provider, setup[2], context_token_limit=1024)(
            "context", setup[0], setup[1], {}
        )
    assert provider.requests == []


def test_strict_schema_requires_every_property():
    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(output_schema())
    assert PROMPT_VERSION


def test_quality_counts_unresolved_and_rejects_invalid_labels(setup):
    value = answers()
    value["criteria"][0].update(
        decision_status="unresolved",
        applicability="undetermined",
        value=None,
        na_reason=None,
        reason="Ambiguous",
        review_reasons=["Review"],
    )
    provider = Scripted([value])
    # This is isolated synthetic test data, never a product-quality claim.
    provider.live = True
    ModelEvaluator(provider, setup[2])("quality-test", setup[0], setup[1], {})
    template = review_template("SYNTHETIC", "freedom-chat@1")
    template["is_ground_truth"] = True
    case = template["cases"][0]
    case.update(
        split="heldout",
        human_reviewed=True,
        reviewer="Synthetic test",
        evaluation_id="quality-test",
    )
    for label, expected in zip(case["criteria"], answers()["criteria"], strict=True):
        label.update(
            applicability=expected["applicability"],
            value=expected["value"],
            evidence_ids=[e["message_id"] for e in expected["evidence"]],
            reason="Synthetic test assertion",
            operator_action_verified=True,
            evidence_support_verified=True,
            na_verified=True,
        )
    report = measure(template, setup[2])
    assert report["conversations"] == 1
    expected = {
        "labeled": 8,
        "resolved": 7,
        "correct": 7,
        "coverage": 7 / 8,
        "accuracy_all_labeled": 7 / 8,
        "accuracy_resolved": 1,
    }
    assert {k: report["metrics"]["overall"][k] for k in expected} == expected
    assert report["metrics"]["overall"]["completion_rate"] == 0
    assert report["metrics"]["overall"]["total_mae"] is None
    assert report["comparisons"][0]["differences"] == ["prediction_unresolved"]
    assert report["disagreement_count"] == 1
    assert measure(template, setup[2], "development")["metrics"] == {}
    assert measure(template, setup[2], "development")["comparisons"] == []
    case["criteria"][0]["value"] = 0  # A boolean rule cannot accept a numeric label.
    with pytest.raises(ValueError):
        measure(template, setup[2])


def test_refusal_is_not_retried_and_usage_saved(setup):
    provider = Scripted(
        [
            ModelError(
                "provider_refused",
                usage={"input_tokens": 7, "output_tokens": 3},
            )
        ]
    )
    engine = ModelEvaluator(provider, setup[2])
    with pytest.raises(ModelError, match="provider_refused"):
        engine("refused", setup[0], setup[1], {})
    run = setup[2].get("refused")
    assert len(provider.requests) == 1
    assert run["metadata"]["token_usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert run["result"] is None


def test_network_error_then_second_invalid_reply_stops_at_three(setup):
    provider = Scripted(["{", ModelError("provider_network", retryable=True), "{", answers()])
    with pytest.raises(ModelError, match="answer_json_invalid"):
        ModelEvaluator(provider, setup[2])("three", setup[0], setup[1], {})
    assert len(provider.requests) == 3


def test_deadline_does_not_save_late_result(setup):
    def delayed(_):
        time.sleep(0.02)
        return answers()

    provider = Scripted([delayed])
    engine = ModelEvaluator(provider, setup[2], total_timeout=0.01)
    with pytest.raises(ModelError, match="evaluation_deadline"):
        engine("late", setup[0], setup[1], {})
    assert setup[2].get("late")["result"] is None


def test_provider_transport_error_safe():
    def fail(_):
        raise httpx.ReadTimeout("SECRET")

    provider = OpenAIResponses("chosen", "key", transport=httpx.MockTransport(fail))
    with pytest.raises(ModelError, match="^provider_network$") as caught:
        provider.generate(INSTRUCTIONS, "{}", output_schema(), 1)
    assert caught.value.retryable


def test_voice_call_uses_transcript_only(setup):
    conv, _, store = setup
    conv["channel"] = "voice call"
    conv["audio_url"] = "https://private.example/?sig=NEVER-SEND"
    for i, message in enumerate(conv["transcript"]):
        message["timestamp"] = f"2026-01-01T12:00:{i:02d}Z"
    rules = get_repository().get_version("freedom-call@1").model_dump(mode="json")
    provider = Scripted([answers()])
    ModelEvaluator(provider, store)("call", conv, rules, {})
    assert "NEVER-SEND" not in canonical(provider.requests)
    assert provider.requests[0][1]["available_analysis"] == ["transcript", "timestamps"]
    assert store.get("call")["metadata"]["audio_analysed"] is False


def test_recovery_marks_abandoned_runs(setup):
    setup[2].begin(
        {
            "evaluation_id": "abandoned",
            "group_id": "SYNTHETIC",
            "conversation_id": setup[0]["conversation_id"],
            "status": "running",
        }
    )
    setup[2].recover()
    assert setup[2].get("abandoned")["metadata"]["status"] == "interrupted"


def reviewed_labels(setup, eid):
    template = review_template("SYNTHETIC", "freedom-chat@1")
    template["is_ground_truth"] = True
    case = template["cases"][0]
    case.update(split="heldout", human_reviewed=True, reviewer="Synthetic test", evaluation_id=eid)
    for label, expected in zip(case["criteria"], answers()["criteria"], strict=True):
        label.update(
            applicability=expected["applicability"],
            value=expected["value"],
            evidence_ids=[e["message_id"] for e in expected["evidence"]],
            reason="Synthetic test only",
            operator_action_verified=True,
            evidence_support_verified=True,
            na_verified=True,
        )
    return template


def test_quality_failed_run_stays_in_denominator(setup):
    provider = Scripted([ModelError("provider_auth")])
    provider.live = True
    with pytest.raises(ModelError):
        ModelEvaluator(provider, setup[2])("failed-quality", setup[0], setup[1], {})
    report = measure(reviewed_labels(setup, "failed-quality"), setup[2])
    overall = report["metrics"]["overall"]
    assert overall["labeled"] == 8 and overall["resolved"] == 0
    assert overall["failed"] == 1 and overall["accuracy_all_labeled"] == 0
    assert overall["total_mae"] is None
    assert overall["flag_precision"] is None and overall["flag_recall"] is None
    assert overall["total_cost_usd"] is None
    assert report["disagreement_count"] == 8
    assert all(r["differences"] == ["prediction_missing"] for r in report["comparisons"])
    assert all(r["prediction"] is None for r in report["comparisons"])


def test_quality_completed_calculation_and_slices(setup):
    provider = Scripted([answers()])
    provider.live = True
    ModelEvaluator(provider, setup[2])("complete-quality", setup[0], setup[1], {})
    report = measure(reviewed_labels(setup, "complete-quality"), setup[2])
    overall = report["metrics"]["overall"]
    assert overall["completion_rate"] == 1 and overall["total_mae"] == 0
    assert overall["normalized_scale_mae"] == 0 and overall["scale_pairs"] == 2
    assert overall["false_na"] == overall["missed_na"] == 0
    assert overall["semantic_evidence_supported_fraction"] is None
    assert overall == report["metrics"]["language:RU"]
    assert overall == report["metrics"]["channel:agent chat"]
    assert report["disagreement_count"] == 0
    assert len(report["comparisons"]) == 8
    assert report["comparisons"][0]["prediction_evidence_correct"] is None


def test_quality_comparison_keeps_na_reasons_and_manual_evidence_review(setup):
    provider = Scripted([answers()])
    provider.live = True
    ModelEvaluator(provider, setup[2])("compare-quality", setup[0], setup[1], {})
    labels = reviewed_labels(setup, "compare-quality")
    case = labels["cases"][0]
    contact = case["criteria"][0]
    contact["value"] = not contact["value"]
    contact["prediction_evidence_correct"] = False
    # The model marked identification N/A; the human marks it applicable.
    identification = next(a for a in case["criteria"] if a["criterion_id"] == "03")
    identification.update(applicability="applicable", value=True)
    report = measure(labels, setup[2])
    rows = {r["criterion_id"]: r for r in report["comparisons"]}
    assert rows["01"]["differences"] == ["value", "evidence_not_supported"]
    assert rows["03"]["differences"] == ["applicability"]
    assert rows["03"]["human"]["reason"] == identification["reason"]
    assert rows["01"]["evaluation_id"] == "compare-quality"
    assert rows["01"]["human"]["evidence_ids"] == contact["evidence_ids"]
    assert rows["01"]["prediction"]["evidence"] == answers()["criteria"][0]["evidence"]
    assert report["disagreement_count"] == 2


def test_quality_matching_na_can_still_have_unsupported_reason(setup):
    output = answers()
    answer = next(a for a in output["criteria"] if a["criterion_id"] == "03")
    answer.update(
        applicability="not_applicable", value=None,
        na_reason="Synthetic general question", reason="Synthetic general question",
    )
    provider = Scripted([output])
    provider.live = True
    ModelEvaluator(provider, setup[2])("na-quality", setup[0], setup[1], {})
    labels = reviewed_labels(setup, "na-quality")
    label = next(a for a in labels["cases"][0]["criteria"] if a["criterion_id"] == "03")
    label.update(
        applicability="not_applicable", value=None, prediction_na_reason_correct=False,
    )
    report = measure(labels, setup[2])
    row = next(r for r in report["comparisons"] if r["criterion_id"] == "03")
    assert row["differences"] == ["na_reason_not_supported"]
    assert row["prediction"]["na_reason"] == "Synthetic general question"
    assert report["metrics"]["overall"]["accuracy_all_labeled"] == 1
    assert report["disagreement_count"] == 1
