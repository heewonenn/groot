"""P1/P2/P3/P4 integration with explicit synthetic providers, never live inference."""

import copy
import json
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import team_data
from backend.app.api.checklists import get_repository
from backend.app.evaluation.contract import canonical
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.provider import ModelError, OpenAIResponses, Reply
from backend.app.evaluation.store import RunStore
from backend.app.model_app import create_configured_app, create_model_app
from backend.app.participant3_demo import seed

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"
GROUP, CID = "SYNTHETIC", "synthetic-contract-001"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_bytes())


def answers(payload: dict | None = None) -> dict:
    """Public hand-authored answers; this helper does not evaluate their meaning."""
    result = {"criteria": example("server-response.synthetic.json")["model_result"]["criteria"]}
    if payload:
        conversation = payload["conversation"]
        positions = {
            message["message_id"]: index for index, message in enumerate(
                example("conversation.synthetic.json")["transcript"]
            )
        }
        for answer in result["criteria"]:
            for ref in answer["evidence"]:
                ref.update(
                    group_id=conversation["group_id"],
                    conversation_id=conversation["conversation_id"],
                )
                ref["message_id"] = conversation["transcript"][positions[ref["message_id"]]][
                    "message_id"
                ]
                if ref["span"]:
                    ref["span"] = {
                        key: conversation["transcript"][positions[value]]["message_id"]
                        for key, value in ref["span"].items()
                    }
    return result


class SyntheticProvider:
    name, model, live = "p3_synthetic_integration", "synthetic-integration-model", False

    def __init__(self, steps: list):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        request = json.loads(payload)
        self.requests.append(request)
        assert instructions and schema and timeout > 0
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(request.get("evaluation_input", request))
        return Reply(
            step if isinstance(step, str) else canonical(step),
            self.model,
            {"input_tokens": 100, "output_tokens": 50},
        )


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "integrated.sqlite3"))
    record = example("conversation.synthetic.json")
    record["audio_url"] = "https://private.invalid/recording?sig=DO_NOT_SEND"
    for message in record["transcript"]:
        message.pop("message_id")
        message["private_extra"] = "DO_NOT_SEND"
    team_data.store_group(GROUP, [team_data.SourceConversation.model_validate(record)])
    get_repository()
    return RunStore()


def engine(store: RunStore, steps: list, **options) -> tuple[ModelEvaluator, SyntheticProvider]:
    provider = SyntheticProvider(steps)
    return ModelEvaluator(provider, store, **options), provider


def job(
    client: TestClient, key: str, version: str = "freedom-chat@1", conversation_id: str = CID,
) -> dict:
    created = client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": key},
        json={
            "group_id": GROUP, "conversation_ids": [conversation_id],
            "checklist_version_id": version,
        },
    )
    assert created.status_code == 202, created.text
    return settled(client, created.json()["job_id"])


def settled(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get("/api/evaluation-jobs/" + job_id)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("Synthetic integrated job did not finish")


def card(client: TestClient, result: dict) -> dict:
    eid = result["items"][0]["evaluation_id"]
    assert eid, result
    response = client.get("/api/evaluation-cards/" + eid)
    assert response.status_code == 200, response.text
    return response.json()


def review(client: TestClient, original: dict, criterion: str = "04", value=20) -> dict:
    eid = original["evaluation"]["evaluation_id"]
    created = client.post(
        f"/api/evaluations/{eid}/appeals",
        json={"criterion_id": criterion, "comment": "Учебный пересмотр общего сценария."},
    )
    assert created.status_code == 201, created.text
    response = client.post(
        f"/api/appeals/{created.json()['appeal_id']}/decision",
        headers={"Idempotency-Key": "review-" + eid + "-" + criterion},
        json={
            "action": "change", "value": value, "applicability": "applicable",
            "reason": "Проверено по исходному правилу и учебному фрагменту.",
            "na_reason": None, "expected_revision": original["revision"],
            "evidence": [{
                "group_id": GROUP, "conversation_id": CID, "message_id": CID + ":m0006",
                "kind": "support", "span": None,
            }],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_model_factory_composes_all_modules_without_implicit_provider(source, monkeypatch):
    monkeypatch.delenv("CQA_MODEL_ENABLED", raising=False)

    def no_request(*_args, **_kwargs):
        pytest.fail("Configuration or disabled evaluation must not call a paid provider")

    monkeypatch.setattr(OpenAIResponses, "generate", no_request)
    with TestClient(create_configured_app()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/model-status").json()["reason"] == "model_disabled"
        assert client.get("/api/capabilities").json()["model_available"] is False
        assert client.get("/api/checklist-versions/freedom-chat@1").status_code == 200
        assert client.get(f"/api/groups/{GROUP}/conversations/{CID}").status_code == 200
        assert client.get(f"/api/evaluations?group_id={GROUP}").json() == []
        assert client.get("/api/appeals").json() == []
        assert client.get(f"/api/evaluation-jobs?group_id={GROUP}").json() == []
        assert client.get(
            f"/api/evaluation-runs?group_id={GROUP}&conversation_id={CID}"
        ).json() == []
        response = client.post(
            "/api/evaluation-jobs", headers={"Idempotency-Key": "disabled"},
            json={"group_id": GROUP, "conversation_ids": [CID],
                  "checklist_version_id": "freedom-chat@1"},
        )
        assert response.status_code == 503
        assert client.get("/api/evaluation-cards/missing").status_code == 404


def test_pipeline_card_audit_and_review_preserve_model_run_after_restart(source):
    evaluator, provider = engine(source, [answers])
    with TestClient(create_model_app(evaluator)) as client:
        assert client.get("/api/model-status").json()["ready"] is True
        result = job(client, "integrated")
        original = card(client, result)
        eid = original["evaluation"]["evaluation_id"]
        assert original["evaluation"]["original_result"]["total"] == "85.38"
        assert original["evaluation"]["generation"]["mode"] == "demo"
        assert original["evaluation"]["generation"]["use_for_model_quality"] is False
        assert "DO_NOT_SEND" not in canonical(provider.requests)
        assert [m["speaker"] for m in provider.requests[0]["conversation"]["transcript"]] == [
            "client", "bot", "system", "operator", "client", "operator",
        ]
        metadata = client.get("/api/evaluation-runs/" + eid).json()
        exported_metadata = original["runtime"]["model_run"]
        assert {k: v for k, v in exported_metadata.items() if k != "attempts"} == {
            k: metadata[k] for k in exported_metadata if k != "attempts"
        }
        public_attempt_fields = {
            "number", "status", "error_code", "usage", "provider", "configured_model",
            "resolved_model", "elapsed_ms",
        }
        assert exported_metadata["attempts"] == [
            {k: a[k] for k in public_attempt_fields} for a in metadata["attempts"]
        ]
        assert "input_fingerprint" not in exported_metadata
        assert metadata["estimated_cost_usd"] is None
        assert metadata["token_usage"] == {"input_tokens": 100, "output_tokens": 50}
        quoted = client.get(f"/api/evaluation-runs/{eid}/evidence").json()
        context = next(q for q in quoted if q["criterion_id"] == "02")
        assert [m["message_id"] for m in context["messages"]] == [
            f"{CID}:m{position:04d}" for position in range(1, 7)
        ]
        assert context["messages"][1]["timestamp"] == context["messages"][2]["timestamp"]
        updated = review(client, original)
        assert updated["evaluation"]["final_result"]["total"] == "93.08"
        assert updated["evaluation"]["original_result"] == original["evaluation"]["original_result"]
        assert updated["evaluation"]["model_result"] == original["evaluation"]["model_result"]
        assert updated["runtime"]["model_run"] == exported_metadata
        audit = client.get(f"/api/evaluation-cards/{eid}/audit")
        assert audit.status_code == 200 and "attachment" in audit.headers["content-disposition"]
        assert audit.json()["runtime"]["model_run"] == exported_metadata
        assert "snapshot" not in audit.json()
        assert "audio_url" not in audit.text and "DO_NOT_SEND" not in audit.text
        assert example("conversation.synthetic.json")["transcript"][0]["text"] not in audit.text
        same_job = job(client, "integrated")
        assert same_job["job_id"] == result["job_id"] and len(provider.requests) == 1
        assert client.get("/api/evaluation-runs/" + eid).json() == metadata
    with TestClient(create_model_app(None, "model_disabled")) as client:
        assert client.get("/api/evaluation-cards/" + eid).json() == updated
        assert client.get("/api/evaluation-runs/" + eid).json() == metadata
        # A replay remains a replay even when the provider is no longer available.
        assert job(client, "integrated")["job_id"] == result["job_id"]


def test_attempt_cost_metadata_survives_review_and_matches_audit(source):
    evaluator, provider = engine(
        source, ["not-json", answers], input_usd_per_million=1, output_usd_per_million=2,
    )
    with TestClient(create_model_app(evaluator)) as client:
        original = card(client, job(client, "correction"))
        metadata = original["runtime"]["model_run"]
        assert len(provider.requests) == 2
        assert [a["status"] for a in metadata["attempts"]] == ["failed", "completed"]
        assert metadata["attempts"][0]["error_code"] == "answer_json_invalid"
        assert metadata["elapsed_ms"] >= 0
        assert metadata["token_usage"] == {"input_tokens": 200, "output_tokens": 100}
        assert metadata["estimated_cost_usd"] == pytest.approx(0.0004)
        updated = review(client, original)
        assert updated["runtime"]["model_run"] == metadata
        eid = updated["evaluation"]["evaluation_id"]
        downloaded = client.get(f"/api/evaluation-cards/{eid}/audit").json()
        assert downloaded["runtime"]["model_run"] == metadata


def test_partial_model_answer_human_final_does_not_rewrite_model_audit(source):
    partial = answers()
    partial["criteria"][0].update(
        value=None, applicability="undetermined", decision_status="unresolved",
        reason="Искусственный неразрешённый ответ для проверки интеграции.",
        na_reason=None, review_reasons=["Требуется ручная проверка."],
    )
    evaluator, _ = engine(source, [partial])
    with TestClient(create_model_app(evaluator)) as client:
        original = card(client, job(client, "partial"))
        assert original["runtime"]["model_run"]["status"] == "partial"
        assert original["evaluation"]["original_result"] is None
        updated = review(client, original, criterion="01", value=True)
        assert updated["final_status"] == "completed"
        assert updated["evaluation"]["status"] == "partial"
        assert updated["evaluation"]["original_result"] is None
        assert updated["evaluation"]["final_result"]["total"] == "85.38"
        assert updated["evaluation"]["model_result"] == original["evaluation"]["model_result"]
        assert updated["runtime"]["model_run"] == original["runtime"]["model_run"]


def test_failed_run_diagnostics_and_retry_keep_completed_review(source):
    evaluator, provider = engine(source, [answers, ModelError("provider_auth"), answers])
    with TestClient(create_model_app(evaluator)) as client:
        successful = card(client, job(client, "first"))
        reviewed = review(client, successful)
        failed = job(client, "failure")
        assert failed["status"] == "failed" and failed["items"][0]["evaluation_id"] is None
        runs = client.get(
            "/api/evaluation-runs", params={"group_id": GROUP, "conversation_id": CID},
        ).json()
        assert runs[0]["status"] == "failed" and runs[0]["error_code"] == "provider_auth"
        failed_eid = runs[0]["evaluation_id"]
        assert client.get(f"/api/evaluation-runs/{failed_eid}/evidence").status_code == 409
        assert client.get(f"/api/evaluation-cards/{failed_eid}").status_code == 404
        retry = client.post(
            f"/api/evaluation-jobs/{failed['job_id']}/retry",
            headers={"Idempotency-Key": "retry"},
        )
        assert retry.status_code == 202, retry.text
        repeated = settled(client, retry.json()["job_id"])
        assert repeated["parent_job_id"] == failed["job_id"]
        assert repeated["status"] == "completed"
        again = client.post(
            f"/api/evaluation-jobs/{failed['job_id']}/retry",
            headers={"Idempotency-Key": "retry"},
        )
        assert again.json() == retry.json() and len(provider.requests) == 3
        eid = reviewed["evaluation"]["evaluation_id"]
        assert client.get("/api/evaluation-cards/" + eid).json() == reviewed
        assert client.get("/api/evaluation-runs/" + failed_eid).json()["status"] == "failed"


def test_changed_and_new_rule_reaches_model_and_each_card_keeps_original_version(source):
    def newer(payload):
        response = answers(payload)
        response["criteria"][0]["value"] = False
        extra = copy.deepcopy(response["criteria"][0])
        extra.update(criterion_id="09", value=False, reason="Новый искусственный критерий.")
        response["criteria"].append(extra)
        return response

    evaluator, provider = engine(source, [answers, newer])
    with TestClient(create_model_app(evaluator)) as client:
        original = card(client, job(client, "version-1"))
        saved_rule = copy.deepcopy(original["checklist"])
        draft = client.get("/api/checklists/freedom-chat/draft").json()
        draft["content"]["criteria"][0]["instruction"] = "Изменённая учебная инструкция версии 2."
        extra = copy.deepcopy(draft["content"]["criteria"][0])
        extra.update(id="09", title="Новый учебный критерий", weight=5)
        draft["content"]["criteria"].append(extra)
        changed = client.patch(
            "/api/checklists/freedom-chat/draft",
            json={"expected_revision": draft["revision"], "content": draft["content"]},
        )
        assert changed.status_code == 200, changed.text
        published = client.post(
            "/api/checklists/freedom-chat/versions",
            json={"expected_revision": changed.json()["revision"]},
        )
        assert published.status_code == 200, published.text
        version = published.json()["checklist_version_id"]
        assert version == "freedom-chat@2"
        new_card = card(client, job(client, "version-2", version))
        assert len(provider.requests) == 2
        assert provider.requests[0]["checklist"] == saved_rule
        assert provider.requests[1]["checklist"]["criteria"][0]["instruction"] == (
            "Изменённая учебная инструкция версии 2."
        )
        assert len(new_card["final_answers"]) == 9 and new_card["checklist"] == published.json()
        new_eid = new_card["evaluation"]["evaluation_id"]
        original_eid = original["evaluation"]["evaluation_id"]
        assert client.get(f"/api/evaluation-runs/{new_eid}").json()["input_fingerprint"] != (
            client.get(f"/api/evaluation-runs/{original_eid}").json()["input_fingerprint"]
        )
        assert new_card["evaluation"]["generation"]["mode"] == "demo"
        updated = review(client, original)
        assert updated["checklist"] == saved_rule
        assert updated["evaluation"]["final_result"]["total"] == "93.08"
        reviewed_custom = review(client, new_card, criterion="09", value=True)
        request = [{key: answer[key] for key in (
            "criterion_id", "value", "applicability", "decision_status"
        )} for answer in reviewed_custom["final_answers"]]
        shared = client.post(
            f"/api/checklist-versions/{version}/calculate", json={"answers": request}
        )
        assert shared.status_code == 200
        assert reviewed_custom["evaluation"]["final_result"] == shared.json()["result"]
        assert reviewed_custom["evaluation"]["original_result"] == (
            new_card["evaluation"]["original_result"]
        )


def test_hand_authored_demo_has_no_model_run_and_wrong_identity_is_not_attached(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "demo.sqlite3"))
    seed()
    source = RunStore()
    eid = "demo-" + CID
    with TestClient(create_model_app(None, "model_disabled")) as client:
        before = client.get("/api/evaluation-cards/" + eid).json()
        assert before["runtime"]["model_run"] is None
        source.begin({
            "evaluation_id": eid, "group_id": "ANOTHER-GROUP", "conversation_id": CID,
            "checklist_version_id": "freedom-chat@1", "status": "completed",
        })
        after = client.get("/api/evaluation-cards/" + eid).json()
        assert after["runtime"]["model_run"] is None
        assert after == before
    # Old databases without the P2 table remain usable with the normal P3 wrapper.
    with closing(team_data.connect()) as db, db:
        db.execute("DROP TABLE cqa_model_runs")
    with TestClient(create_model_app(None, "model_disabled")) as client:
        assert client.get("/api/evaluation-cards/" + eid).json() == before


@pytest.mark.parametrize(
    ("cid", "language", "channel", "version"),
    [
        (CID, "RU", "agent chat", "freedom-chat@1"),
        ("synthetic-kz-001", "KZ", "agent chat", "freedom-chat@1"),
        ("synthetic-en-001", "EN", "agent chat", "freedom-chat@1"),
        ("synthetic-call-001", "RU", "voice call", "freedom-call@1"),
    ],
)
def test_supported_language_and_call_use_exact_source_ids_without_acoustic_claims(
    tmp_path, monkeypatch, cid, language, channel, version,
):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "languages.sqlite3"))
    seed()
    evaluator, provider = engine(RunStore(), [answers])
    with TestClient(create_model_app(evaluator)) as client:
        source = client.get(f"/api/groups/{GROUP}/conversations/{cid}").json()
        evaluated = card(client, job(client, "language", version, cid))
        supplied = provider.requests[0]["conversation"]
        assert (supplied["language"], supplied["channel"]) == (language, channel)
        assert supplied["transcript"] == source["transcript"]
        assert "audio_url" not in supplied
        assert supplied["transcript"][1]["timestamp"] == supplied["transcript"][2]["timestamp"]
        assert supplied["transcript"][1]["message_id"] != supplied["transcript"][2]["message_id"]
        ids = {message["message_id"] for message in supplied["transcript"]}
        for answer in evaluated["final_answers"]:
            assert "audio" not in answer["analysis_scope"]
            for ref in answer["evidence"]:
                assert (ref["group_id"], ref["conversation_id"]) == (GROUP, cid)
                assert ref["message_id"] in ids
        assert evaluated["evaluation"]["checklist_version_id"] == version
        assert evaluated["evaluation"]["generation"]["mode"] == "demo"
        assert evaluated["runtime"]["model_run"]["audio_analysed"] is False


def test_disabled_restart_recovers_abandoned_model_audit_and_queue_without_calls(
    source, monkeypatch,
):
    def no_request(*_args, **_kwargs):
        pytest.fail("Recovery must not call an external model")

    monkeypatch.setattr(OpenAIResponses, "generate", no_request)
    with TestClient(create_model_app(None, "model_disabled")):
        pass  # Initialize the ordinary queue tables before simulating a crash.
    source.begin({
        "evaluation_id": "abandoned-run", "group_id": GROUP, "conversation_id": CID,
        "checklist_version_id": "freedom-chat@1", "status": "running",
        "generation_mode": "demo", "error_code": None,
        "started_at": "2026-01-01T00:00:00+00:00", "finished_at": None,
        "attempts": [], "elapsed_ms": None, "token_usage": None,
        "estimated_cost_usd": None,
    })
    with closing(team_data.connect()) as db, db:
        db.execute(
            """INSERT INTO cqa_batch_jobs VALUES(?,NULL,?,?,?,?,?,?)""",
            ("abandoned-job", GROUP, "freedom-chat@1", "{}",
             "2026-01-01T00:00:00+00:00", "abandoned-key", "synthetic-crash"),
        )
        db.execute(
            """INSERT INTO cqa_batch_items(job_id,conversation_id,status,started_at)
               VALUES(?,?,'running',?)""",
            ("abandoned-job", CID, "2026-01-01T00:00:00+00:00"),
        )
    with TestClient(create_model_app(None, "model_disabled")) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/model-status").json()["ready"] is False
        metadata = client.get("/api/evaluation-runs/abandoned-run").json()
        assert metadata["status"] == "interrupted"
        assert metadata["error_code"] == "server_restart"
        assert metadata["finished_at"] is None and metadata["elapsed_ms"] is None
        assert metadata["token_usage"] is None and metadata["estimated_cost_usd"] is None
        assert metadata["attempts"] == []
        queue = client.get("/api/evaluation-jobs/abandoned-job").json()
        assert queue["status"] == "interrupted"
        assert queue["items"][0]["error_code"] == "server_restart"
        assert queue["items"][0]["evaluation_id"] is None
        assert client.get(f"/api/evaluations?group_id={GROUP}").json() == []
    assert RunStore().get("abandoned-run")["metadata"] == metadata


def test_model_factory_keeps_health_available_when_storage_is_offline(tmp_path, monkeypatch):
    # A regular file cannot be the database's parent on Windows or Unix.
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("temporary storage blocker", encoding="utf-8")
    monkeypatch.setenv("CQA_DB_PATH", str(blocked_parent / "cqa.sqlite3"))
    monkeypatch.delenv("CQA_MODEL_ENABLED", raising=False)
    with TestClient(create_configured_app()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/model-status").json()["ready"] is False
        response = client.get(
            "/api/evaluation-runs", params={"group_id": GROUP, "conversation_id": CID},
        )
        assert response.status_code == 503
        assert str(tmp_path) not in response.text
