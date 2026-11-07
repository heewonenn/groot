"""Participant 3 review transactions against public synthetic team fixtures."""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.app import team_data
from backend.app.api.checklists import get_repository
from backend.app.api.evaluation_jobs import JobRunner
from backend.app.api.evaluation_jobs import router as jobs_router
from backend.app.api.participant3 import router as review_router
from backend.app.services.participant3 import initialize

CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"
MID = "synthetic-contract-001"
GROUP = "SYNTHETIC"


def fixture(name):
    return json.loads((CONTRACTS / "examples" / name).read_bytes())


def adapter(eid, conv, checklist, settings):
    result = fixture("server-response.synthetic.json")["model_result"]
    result.update(
        evaluation_id=eid,
        group_id=conv["group_id"],
        conversation_id=conv["conversation_id"],
    )
    return result


@pytest.fixture
def review(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "review.db"))
    record = fixture("conversation.synthetic.json")
    for msg in record["transcript"]:
        msg.pop("message_id")
    team_data.store_group(GROUP, [team_data.SourceConversation.model_validate(record)])
    get_repository()
    runner = JobRunner(adapter)
    runner.start()
    initialize()
    app = FastAPI()
    app.state.jobs = runner
    app.include_router(jobs_router)
    app.include_router(review_router)
    app.include_router(team_data.router)
    try:
        with TestClient(app) as client:
            yield client, runner
    finally:
        runner.stop()


def evaluation(review, key="job"):
    client, _ = review
    receipt = client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": key},
        json={
            "group_id": GROUP,
            "conversation_ids": [MID],
            "checklist_version_id": "freedom-chat@1",
        },
    )
    assert receipt.status_code == 202, receipt.text
    job_id = receipt.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/evaluation-jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.015)
    else:
        pytest.fail("Synthetic evaluation did not finish")
    eid = job["items"][0]["evaluation_id"]
    assert eid, job
    response = client.get(f"/api/evaluation-cards/{eid}")
    assert response.status_code == 200, response.text
    return response.json()


def appeal(client, eid, criterion="04", comment="Прошу проверить ответ."):
    response = client.post(
        f"/api/evaluations/{eid}/appeals",
        json={"criterion_id": criterion, "comment": comment},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def ref(number=6, **kwargs):
    return {
        "group_id": GROUP,
        "conversation_id": MID,
        "message_id": f"{MID}:m{number:04d}",
        "kind": "support",
        "span": None,
        **kwargs,
    }


def decision(value=20, revision=0, **kwargs):
    return {
        "action": "change",
        "value": value,
        "applicability": "applicable",
        "reason": "Проверено по исходной реплике.",
        "na_reason": None,
        "evidence": [ref()],
        "expected_revision": revision,
        **kwargs,
    }


def decide(client, a, payload=None, key="decision"):
    return client.post(
        f"/api/appeals/{a['appeal_id']}/decision",
        headers={"Idempotency-Key": key},
        json=payload or decision(),
    )


def test_completed_card_review_immutable_original_and_native_contract(review):
    client, _ = review
    original = evaluation(review)
    eid = original["evaluation"]["evaluation_id"]
    assert original["revision"] == 0
    assert original["final_status"] == "completed"
    assert original["evaluation"]["original_result"]["total"] == "85.38"
    a = appeal(client, eid)
    response = decide(client, a)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"] == 1
    assert updated["evaluation"]["original_result"] == original["evaluation"]["original_result"]
    assert updated["evaluation"]["model_result"] == original["evaluation"]["model_result"]
    assert updated["evaluation"]["final_result"]["total"] == "93.08"
    answer = next(x for x in updated["final_answers"] if x["criterion_id"] == "04")
    assert answer["value"] == 20
    assert answer["reason"] == decision()["reason"]
    assert updated["appeals"][0]["comment"] == a["comment"]
    assert updated["appeals"][0]["decision"]["previous_value"] == 15
    canonical = client.get(f"/api/evaluations/{eid}").json()
    schema = json.loads((CONTRACTS / "cqa-team-v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(canonical)
    assert canonical == updated["evaluation"]
    assert client.get(f"/api/evaluation-cards/{eid}").json() == updated
    summaries = client.get(f"/api/evaluations?group_id={GROUP}&conversation_id={MID}").json()
    assert len(summaries) == 1
    assert summaries[0]["original_total"] == "85.38"
    assert summaries[0]["final_total"] == "93.08"
    assert summaries[0]["revision"] == 1
    audit = client.get(f"/api/evaluation-cards/{eid}/audit")
    assert audit.status_code == 200
    assert "attachment" in audit.headers["content-disposition"]
    assert audit.json()["evaluation"] == canonical
    assert "transcript" not in audit.json()
    assert "audio_url" not in audit.text


def test_idempotent_replay_before_revision_and_payload_conflict(review):
    client, _ = review
    card = evaluation(review)
    eid = card["evaluation"]["evaluation_id"]
    a = appeal(client, eid)
    first = decide(client, a)
    assert first.status_code == 200
    # Replay still succeeds even though the submitted revision is now stale.
    assert decide(client, a).json() == first.json()
    assert decide(client, a, decision(reason="Иное решение." )).status_code == 409
    assert decide(client, a, key="second-key").status_code == 409
    other = appeal(client, eid, "07")
    assert decide(client, other, decision(value=False), key="other").status_code == 409
    now = client.get(f"/api/evaluation-cards/{eid}").json()
    assert now["revision"] == 1
    open_appeal = next(x for x in now["appeals"] if x["appeal_id"] == other["appeal_id"])
    assert open_appeal["status"] == "open"


def test_duplicate_open_appeal_and_required_comments(review):
    client, _ = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    a = appeal(client, eid)
    assert appeal(client, eid, comment="Повторное нажатие.")["appeal_id"] == a["appeal_id"]
    response = client.post(
        f"/api/evaluations/{eid}/appeals", json={"criterion_id": "04", "comment": "   "}
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/evaluations/{eid}/appeals", json={"criterion_id": "unknown", "comment": "Проверить"}
    )
    assert response.status_code in (404, 422)
    assert len(client.get("/api/appeals?status=open").json()) == 1
    assert client.get("/api/appeals?status=resolved").json() == []


@pytest.mark.parametrize(
    ("criterion", "payload"),
    [
        ("01", decision(value=1)),
        ("04", decision(value=True)),
        ("04", decision(value=17)),
        ("04", decision(value="20")),
        ("04", decision(reason="   ")),
        ("04", decision(evidence=[])),
        ("04", decision(evidence=[ref(group_id="WRONG")])),
        ("04", decision(evidence=[ref(conversation_id="WRONG")])),
        ("04", decision(evidence=[ref(99)])),
        ("04", decision(evidence=[ref(2, span={
            "start_message_id": MID + ":m0005", "end_message_id": MID + ":m0002"
        })])),
        ("04", decision(evidence=[ref(2, span={
            "start_message_id": MID + ":m0002", "end_message_id": MID + ":m0099"
        })])),
        ("01", decision(value=None, applicability="not_applicable", na_reason="Не нужен.")),
        ("07", decision(value=None, applicability="not_applicable", na_reason="   ")),
        ("07", decision(value=True, applicability="not_applicable", na_reason="Не нужен.")),
        ("04", decision(expected_revision=True)),
        ("04", decision(action="confirm")),
    ],
)
def test_invalid_decision_leaves_open_appeal_and_card_untouched(review, criterion, payload):
    client, _ = review
    card = evaluation(review)
    eid = card["evaluation"]["evaluation_id"]
    a = appeal(client, eid, criterion)
    before = client.get(f"/api/evaluation-cards/{eid}").json()
    response = decide(client, a, payload)
    assert response.status_code == 422, response.text
    after = client.get(f"/api/evaluation-cards/{eid}").json()
    assert after == before


def test_na_recalculates_and_flag_does_not_penalize_numeric_score(review):
    client, _ = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    a = appeal(client, eid, "07")
    changed = decide(
        client, a,
        decision(
            value=None, applicability="not_applicable", na_reason="Ситуация не требует завершения."
        ),
    )
    assert changed.status_code == 200, changed.text
    first = changed.json()
    assert first["evaluation"]["final_result"]["applicable_weight"] == "60.00"
    assert first["evaluation"]["final_result"]["total"] == "84.17"
    flag = appeal(client, eid, "08")
    response = decide(client, flag, decision(value=True, revision=1), key="flag")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["evaluation"]["final_result"]["gross_error"] is True
    assert card["evaluation"]["final_result"]["total"] == "84.17"
    assert card["evaluation"]["original_result"]["gross_error"] is False


def test_evidence_ranges_keep_equal_timestamp_messages_distinct(review):
    client, _ = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    evidence = [ref(2, span={
        "start_message_id": MID + ":m0002", "end_message_id": MID + ":m0003"
    })]
    a = appeal(client, eid)
    response = decide(client, a, decision(evidence=evidence))
    assert response.status_code == 200, response.text
    card = response.json()
    saved = card["appeals"][0]["decision"]["evidence"]
    assert saved == evidence
    assert card["evaluation"]["review_history"][0]["evidence"] == [
        {"group_id": GROUP, "conversation_id": MID, "message_id": MID + ":m0002"},
        {"group_id": GROUP, "conversation_id": MID, "message_id": MID + ":m0003"},
    ]
    first = client.get(f"/api/groups/{GROUP}/conversations/{MID}/messages/{MID}:m0002").json()
    second = client.get(f"/api/groups/{GROUP}/conversations/{MID}/messages/{MID}:m0003").json()
    assert first["timestamp"] == second["timestamp"]
    assert first["message_id"] != second["message_id"]


def test_partial_human_resolution_preserves_original_without_false_early_total(review):
    client, runner = review

    def unresolved(*args):
        result = adapter(*args)
        for answer in result["criteria"]:
            if answer["criterion_id"] in {"01", "05"}:
                answer.update(
                    decision_status="unresolved", applicability="undetermined", value=None
                )
        return result

    runner.evaluator = unresolved
    original = evaluation(review)
    eid = original["evaluation"]["evaluation_id"]
    assert original["final_status"] == "partial"
    a = appeal(client, eid, "01")
    assert decide(client, a, decision(action="confirm", value=None)).status_code == 422
    first = decide(client, a, decision(value=True), key="resolve-one")
    assert first.status_code == 200, first.text
    assert first.json()["final_status"] == "partial"
    assert first.json()["evaluation"]["final_result"] is None
    b = appeal(client, eid, "05")
    second = decide(client, b, decision(value=True, revision=1), key="resolve-two")
    assert second.status_code == 200, second.text
    card = second.json()
    assert card["final_status"] == "completed"
    assert card["evaluation"]["final_result"]["total"] == "85.38"
    assert card["evaluation"]["status"] == "partial"
    assert card["evaluation"]["model_result"] == original["evaluation"]["model_result"]
    assert card["evaluation"]["original_result"] is None
    assert all(answer["decision_status"] == "evaluated" for answer in card["final_answers"])
    with closing(team_data.connect()) as db:
        item = db.execute(
            "SELECT status FROM cqa_batch_items WHERE evaluation_id=?", (eid,)
        ).fetchone()
    assert item["status"] == "partial"
    schema = json.loads((CONTRACTS / "cqa-team-v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(card["evaluation"])


def test_review_uses_original_version_after_new_checklist_version(review):
    client, _ = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    repository = get_repository()
    draft = repository.get_draft("freedom-chat")
    content = copy.deepcopy(draft.content)
    content.criteria[3].weight = 200
    changed = repository.update_draft("freedom-chat", draft.revision, content)
    published = repository.publish("freedom-chat", changed.revision)
    assert published.checklist_version_id == "freedom-chat@2"
    a = appeal(client, eid)
    response = decide(client, a)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["checklist"]["checklist_version_id"] == "freedom-chat@1"
    assert card["evaluation"]["final_result"]["total"] == "93.08"


def test_concurrent_stale_reviews_only_one_commits_and_state_survives_runner_restart(review):
    client, runner = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    a, b = appeal(client, eid, "04"), appeal(client, eid, "07")
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(decide, client, a, decision(), "race-a")
        future_b = pool.submit(decide, client, b, decision(value=False), "race-b")
        responses = [future_a.result(), future_b.result()]
    assert sorted(response.status_code for response in responses) == [200, 409]
    before = client.get(f"/api/evaluation-cards/{eid}").json()
    assert before["revision"] == 1
    assert sum(row["status"] == "resolved" for row in before["appeals"]) == 1
    runner.stop()
    runner.start()
    initialize()
    assert client.get(f"/api/evaluation-cards/{eid}").json() == before


def test_unknown_card_and_appeal_are_404(review):
    client, _ = review
    assert client.get("/api/evaluation-cards/unknown").status_code == 404
    assert client.get("/api/evaluation-cards/unknown/audit").status_code == 404
    assert decide(client, {"appeal_id": "unknown"}).status_code == 404


def test_confirmation_and_saved_receipt_replay_after_later_revision(review):
    client, _ = review
    eid = evaluation(review)["evaluation"]["evaluation_id"]
    a = appeal(client, eid, "01")
    body = decision(action="confirm", value=True)
    first = decide(client, a, body, key="confirmed")
    assert first.status_code == 200, first.text
    assert first.json()["evaluation"]["final_result"]["total"] == "85.38"
    assert first.json()["evaluation"]["review_history"][0]["decision"] == "confirmed"
    b = appeal(client, eid, "04")
    changed = decide(client, b, decision(revision=1), key="changed")
    assert changed.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    # A transport retry returns the saved transaction response, not a fresh later card.
    replay = decide(client, a, body, key="confirmed")
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert client.get(f"/api/evaluation-cards/{eid}").json()["revision"] == 2
