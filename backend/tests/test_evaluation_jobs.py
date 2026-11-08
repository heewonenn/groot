"""Public synthetic records exercise the team-v1 evidence/scoring/queue boundary."""

import json
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.app import team_data
from backend.app.api.evaluation_jobs import JobRunner, router
from backend.app.services.evaluation_jobs import store_review

CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"
MID = "synthetic-contract-001"


def fixture(name: str) -> dict:
    return json.loads((CONTRACTS / "examples" / name).read_bytes())


def adapter(eid: str, conv: dict, checklist: dict, settings: dict) -> dict:
    assert "audio_url" not in conv
    assert len(checklist["criteria"]) == 8
    result = fixture("server-response.synthetic.json")["model_result"]
    result.update(
        evaluation_id=eid, group_id=conv["group_id"], conversation_id=conv["conversation_id"]
    )
    for answer in result["criteria"]:
        for ref in answer["evidence"]:
            ref.update(group_id=conv["group_id"], conversation_id=conv["conversation_id"])
            ref["message_id"] = ref["message_id"].replace(MID, conv["conversation_id"])
            if ref["span"]:
                ref["span"] = {
                    k: v.replace(MID, conv["conversation_id"]) for k, v in ref["span"].items()
                }
    return result


@pytest.fixture
def setup_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "cqa.db"))
    record = fixture("conversation.synthetic.json")
    record["audio_url"] = None
    group = record["group_id"]
    # Input records originally contain raw messages, not previously normalized IDs.
    for msg in record["transcript"]:
        msg.pop("message_id")
    rows = [
        team_data.SourceConversation.model_validate({**record, "conversation_id": cid})
        for cid in (MID, "other")
    ]
    team_data.store_group(group, rows)
    runner = JobRunner(adapter)
    runner.start()
    app = FastAPI()
    app.state.jobs = runner
    app.include_router(router)
    try:
        with TestClient(app) as client:
            yield runner, client
    finally:
        runner.stop()


def create(client, ids: list[str], key: str):
    return client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": key},
        json={
            "group_id": "SYNTHETIC",
            "conversation_ids": ids,
            "checklist_version_id": "freedom-chat@1",
        },
    )


def settled(client, job_id: str) -> dict:
    for _ in range(200):
        row = client.get("/api/evaluation-jobs/" + job_id).json()
        if row["status"] not in ("queued", "running"):
            return row
        time.sleep(0.015)
    pytest.fail("Job did not finish")


def test_card_uses_real_scoring_and_exact_team_schema(setup_jobs):
    runner, client = setup_jobs
    receipt = create(client, [MID], "run")
    assert receipt.status_code == 202
    job = settled(client, receipt.json()["job_id"])
    card = client.get("/api/evaluations/" + job["items"][0]["evaluation_id"]).json()
    Draft202012Validator(json.loads((CONTRACTS / "cqa-team-v1.schema.json").read_bytes())).validate(
        card
    )
    assert card["original_result"]["total"] == "85.38"
    assert card["final_result"] == card["original_result"]
    assert card["review_history"] == []
    assert card["group_id"] == "SYNTHETIC" and card["conversation_id"] == MID
    assert card["model_result"]["evaluation_id"] == card["evaluation_id"]
    assert create(client, [MID], "run").json() == receipt.json()
    assert create(client, ["other"], "run").status_code == 409
    exported = client.get("/api/evaluation-jobs/" + job["job_id"] + "/export")
    export_card = exported.json()["items"][0]["evaluation"]
    assert "reason" not in export_card["model_result"]["criteria"][0]
    assert (
        export_card["model_result"]["criteria"][0]["evidence"]
        == card["model_result"]["criteria"][0]["evidence"]
    )
    runner.evaluator = None
    assert create(client, [MID], "offline").status_code == 503
    assert create(client, [MID], "run").json() == receipt.json()


@pytest.mark.parametrize("issue", ["group", "span", "binding", "scale", "missing", "repeat", "na"])
def test_rejects_invalid_result(setup_jobs, issue):
    runner, client = setup_jobs

    def invalid(*args):
        result = adapter(*args)
        first = result["criteria"][0]
        if issue == "group":
            first["evidence"][0]["group_id"] = "ANOTHER"
        elif issue == "span":
            first["evidence"][0]["span"] = {
                "start_message_id": MID + ":m0005",
                "end_message_id": MID + ":m0002",
            }
        elif issue == "binding":
            result["checklist_version_id"] = "freedom-chat@2"
        elif issue == "scale":
            result["criteria"][5]["value"] = True
        elif issue == "missing":
            first["evidence"] = []
        elif issue == "repeat":
            result["criteria"][1] = first
        else:
            result["criteria"][1]["na_reason"] = " "
        return result

    runner.evaluator = invalid
    job = settled(client, create(client, [MID], issue).json()["job_id"])
    assert job["status"] == "failed"
    assert job["items"][0]["evaluation_id"] is None


def test_retry_only_failed_and_preserve_interruption(setup_jobs):
    runner, client = setup_jobs

    def sometimes_fails(eid, conv, rules, settings):
        if conv["conversation_id"] == "other":
            raise TimeoutError("PRIVATE URL")
        return adapter(eid, conv, rules, settings)

    runner.evaluator = sometimes_fails
    job = settled(client, create(client, [MID, "other"], "first").json()["job_id"])
    assert job["status"] == "partial"
    assert job["items"][1]["error_code"] == "evaluation_timeout"
    orig_eid = job["items"][0]["evaluation_id"]
    runner.stop()
    with closing(team_data.connect()) as db, db:
        db.execute("UPDATE cqa_batch_items SET status='running' WHERE conversation_id='other'")
    runner.start()
    assert settled(client, job["job_id"])["status"] == "interrupted"
    runner.evaluator = adapter
    url = "/api/evaluation-jobs/" + job["job_id"] + "/retry"
    receipt = client.post(url, headers={"Idempotency-Key": "retry"})
    retry = settled(client, receipt.json()["job_id"])
    assert client.post(url, headers={"Idempotency-Key": "retry"}).json() == receipt.json()
    assert client.post(url, headers={"Idempotency-Key": "another"}).status_code == 409
    assert retry["status"] == "completed"
    assert [r["conversation_id"] for r in retry["items"]] == ["other"]
    assert client.get("/api/evaluations/" + orig_eid).status_code == 200


def test_gemma_only_retry_pins_one_attempt_and_preserves_root_settings(setup_jobs):
    runner, client = setup_jobs

    def evaluate(eid, conv, rules, settings):
        if not settings.get("gemma_only"):
            raise TimeoutError()
        return adapter(eid, conv, rules, settings)

    runner.evaluator = evaluate
    root = settled(client, create(client, [MID], "gemma-root").json()["job_id"])
    assert root["status"] == "failed"
    url = "/api/evaluation-jobs/" + root["job_id"] + "/retry"
    headers = {"Idempotency-Key": "pinned-retry"}
    child = client.post(url, params={"gemma_only": "true"}, headers=headers).json()
    saved = settled(client, child["job_id"])
    assert saved["status"] == "completed"
    assert saved["model_config"] == root["model_config"]
    assert client.post(url, params={"gemma_only": "true"}, headers=headers).json() == child
    assert client.post(url, headers=headers).status_code == 409


def test_unresolved_no_false_score(setup_jobs):
    runner, client = setup_jobs

    def unresolved(*args):
        result = adapter(*args)
        result["criteria"][0].update(
            decision_status="unresolved", applicability="undetermined", value=None
        )
        return result

    runner.evaluator = unresolved
    job = settled(client, create(client, [MID], "partial").json()["job_id"])
    assert job["status"] == "partial"
    card = client.get("/api/evaluations/" + job["items"][0]["evaluation_id"]).json()
    assert (
        card["status"] == "partial"
        and card["original_result"] is None
        and card["final_result"] is None
    )


def test_store_review_uses_real_original_rules_and_caller_transaction(setup_jobs):
    _, client = setup_jobs
    job = settled(client, create(client, [MID], "review").json()["job_id"])
    eid = job["items"][0]["evaluation_id"]
    path = "/api/evaluations/" + eid
    original = client.get(path).json()
    decision = fixture("server-response.synthetic.json")["review_history"][0]
    with closing(team_data.connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="Evidence"):
            store_review(
                db,
                eid,
                {**decision, "evidence": [{**decision["evidence"][0], "group_id": "WRONG"}]},
            )
        reviewed = store_review(db, eid, decision)
        assert reviewed["original_result"] == original["original_result"]
        assert reviewed["original_result"]["total"] == "85.38"
        assert reviewed["final_result"]["total"] == "93.08"
        db.rollback()
    assert client.get(path).json() == original
    with closing(team_data.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        reviewed = store_review(db, eid, decision)
        assert store_review(db, eid, decision) == reviewed
        with pytest.raises(HTTPException) as conflict:
            store_review(db, eid, {**decision, "appeal_id": "another"})
        assert conflict.value.status_code == 409
    assert client.get(path).json() == reviewed
    Draft202012Validator(json.loads((CONTRACTS / "cqa-team-v1.schema.json").read_bytes())).validate(
        reviewed
    )
    exported = client.get("/api/evaluation-jobs/" + job["job_id"] + "/export").json()["items"][0][
        "evaluation"
    ]
    assert [exported[k]["total"] for k in ("original_result", "final_result")] == ["85.38", "93.08"]
    assert exported["review_history"][0]["evidence"] == decision["evidence"]
