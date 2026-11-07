"""HTTP export integration uses synthetic records, never actual model measurements."""

import json
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.checklists import get_repository
from backend.app.api.submission_downloads import router
from backend.app.services.evaluation_cards import build_card
from backend.app.services.evaluation_jobs import DDL, store_review
from backend.app.services.submission_validation import validate_submission
from backend.app.team_data import (
    SourceConversation,
    connect,
    get_conversation,
    list_conversations,
    store_group,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"
GROUP = "HTTP-EXPORT-TEST"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "http-export.sqlite3"))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def seed(split="prod", mode="live", *, errors=False):
    example = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
    source = []
    for i in range(65 if split == "dev" else 10):
        source.append(SourceConversation.model_validate({
            "conversation_id": f"http-export-{i:03d}",
            "channel": "voice call" if i >= 60 else "agent chat",
            "language": ("RU", "KZ", "EN")[i % 3],
            "audio_url": None,
            "transcript": [
                {key: message[key] for key in ("speaker", "timestamp", "text")}
                for message in example["transcript"]
            ],
        }))
    store_group(GROUP, source)
    rules = {
        channel: get_repository().get_version(version)
        for channel, version in (
            ("agent chat", "freedom-chat@1"), ("voice call", "freedom-call@1")
        )
    }
    with closing(connect()) as db, db:
        db.executescript(DDL)
        for channel, version in rules.items():
            members = [item for item in source if item.channel == channel]
            if not members:
                continue
            job = "calls" if channel == "voice call" else "chats"
            db.execute(
                "INSERT INTO cqa_batch_jobs VALUES (?,?,?,?,?,?,?,?)",
                (job, None, GROUP, version.checklist_version_id,
                 '{"model":"http-test-model"}', "2026-11-07", job, job),
            )
            for index, item in enumerate(members):
                cid = item.conversation_id
                conversation = get_conversation(GROUP, cid)
                raw = json.loads((EXAMPLES / "server-response.synthetic.json").read_bytes())
                result = json.loads(json.dumps(raw["model_result"]).replace(
                    example["conversation_id"], cid
                ))
                eid = "evaluation-" + cid
                result.update(
                    evaluation_id=eid, group_id=GROUP,
                    checklist_id=version.checklist_id,
                    checklist_version_id=version.checklist_version_id,
                )
                result["generation"].update(
                    mode=mode, model_id="http-test-model", origin="pytest_only",
                    use_for_model_quality=False,
                )
                for answer in result["criteria"]:
                    answer["reason"] = "Проверка HTTP-контракта " + answer["criterion_id"]
                    for ref in answer["evidence"]:
                        ref["group_id"] = GROUP
                result["criteria"][3]["evidence"][0]["span"] = {
                    "start_message_id": cid + ":m0002",
                    "end_message_id": cid + ":m0004",
                }
                status = "completed"
                error = None
                if errors and index == 1:
                    unresolved = result["criteria"][0]
                    unresolved.update(
                        value=None, applicability="undetermined", decision_status="unresolved"
                    )
                    status = "partial"
                card = build_card(eid, conversation, version, result)
                if errors and index == 0:
                    status, error, eid, card = "failed", "evaluation_timeout", None, None
                db.execute(
                    "INSERT INTO cqa_batch_items VALUES (?,?,?,?,?,?,?,?)",
                    (job, cid, status, eid, json.dumps(card) if card else None,
                     error, "start", "end"),
                )
    return ["chats", "calls"] if split == "dev" else ["chats"]


def request_body(split="prod", jobs=None):
    return {
        "group_id": GROUP, "split": split,
        "job_ids": jobs if jobs is not None else ["chats"],
        "team_name": "WE ARE GROOT", "model_mode": "local",
    }


@pytest.mark.parametrize("split", ["prod", "dev"])
def test_download_valid_complete_dataset_including_errors(client, split):
    jobs = seed(split, errors=True)
    response = client.post("/api/submissions/prepare", json=request_body(split, jobs))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == 'attachment; filename="results.json"'
    assert response.headers["cache-control"] == "no-store"
    assert response.content.endswith(b"\n")
    payload = response.json()
    conversations = [
        get_conversation(GROUP, row["conversation_id"]) for row in list_conversations(GROUP)
    ]
    validate_submission(payload, conversations, split)
    assert len(payload["results"]) == (65 if split == "dev" else 10)
    assert payload["model"] == {"mode": "local", "name": "http-test-model"}
    first_rows = {row["conversation_id"]: row for row in payload["results"]}
    assert first_rows["http-export-000"]["status"] == "error"
    assert first_rows["http-export-001"]["status"] == "error"
    success = first_rows["http-export-002"]
    assert success["criteria"][3]["evidence_turns"] == [2, 3, 4, 6]
    assert success["score"] == {"final_score": 85.38, "critical_flag": False}
    assert "audio_url" not in response.text and "transcript" not in response.text
    assert not list(Path(get_repository().path).parent.glob("results.json"))


def test_official_export_keeps_original_after_human_review(client):
    seed()
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT card FROM cqa_batch_items LIMIT 1").fetchone()
        card = json.loads(row["card"])
        original = card["original_result"]["total"]
        reviewed = store_review(db, card["evaluation_id"], {
            "appeal_id": "http-test-appeal", "criterion_id": "04",
            "checklist_version_id": card["checklist_version_id"],
            "comment": "Проверить полноту ответа.", "decision": "changed",
            "original_value": 15, "new_value": 20,
            "reason": "review information is not official AI output", "revision": 1,
            "evidence": [{
                "group_id": GROUP, "conversation_id": card["conversation_id"],
                "message_id": card["conversation_id"] + ":m0006",
            }],
        })
        assert reviewed["final_result"]["total"] != original
    response = client.post("/api/submissions/prepare", json=request_body())
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["score"]["final_score"] == float(original)
    assert "review information" not in response.text


@pytest.mark.parametrize("mode", ["demo", "cache"])
def test_rejects_demo_or_unverified_cache(client, mode):
    seed(mode=mode)
    response = client.post("/api/submissions/prepare", json=request_body())
    assert response.status_code == 422
    assert "results" not in response.json()


def test_list_jobs_filters_group_and_preserves_retry_information(client):
    seed()
    response = client.get("/api/evaluation-jobs", params={"group_id": GROUP})
    assert response.status_code == 200
    assert len(response.json()) == 1
    job = response.json()[0]
    assert job["job_id"] == "chats" and job["parent_job_id"] is None
    assert job["counts"]["completed"] == 10
    assert job["model_config"] == {"model": "http-test-model"}
    assert "card" not in str(job) and "transcript" not in str(job)
    assert client.get("/api/evaluation-jobs", params={"group_id": "missing"}).status_code == 404


def test_imported_group_without_job_tables_is_empty(client):
    store_group(GROUP, [])
    assert client.get("/api/evaluation-jobs", params={"group_id": GROUP}).json() == []


def test_storage_failure_returns_safe_503(client, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", "/dev/null/http-export.sqlite3")
    response = client.get("/api/evaluation-jobs", params={"group_id": GROUP})
    assert response.status_code == 503
    assert "/dev/null" not in response.text


@pytest.mark.parametrize(("field", "value"), [
    ("job_ids", []), ("job_ids", ["chats", "chats"]), ("job_ids", [" "]),
    ("job_ids", [7]), ("group_id", " "), ("group_id", 42),
    ("team_name", " "), ("team_name", "another team"),
    ("model_mode", "demo"), ("split", "test"),
])
def test_rejects_invalid_export_request(client, field, value):
    body = request_body()
    body[field] = value
    response = client.post("/api/submissions/prepare", json=body)
    assert response.status_code == 422


def test_rejects_missing_wrong_group_jobs_and_incomplete_split(client):
    seed()
    assert client.post(
        "/api/submissions/prepare", json=request_body(jobs=["missing"])
    ).status_code == 404
    with closing(connect()) as db, db:
        db.execute("UPDATE cqa_batch_jobs SET group_id='OTHER'")
    assert client.post("/api/submissions/prepare", json=request_body()).status_code == 404
    assert client.post(
        "/api/submissions/prepare", json=request_body(split="dev")
    ).status_code == 422
    body = request_body()
    body["group_id"] = "not-imported"
    assert client.post("/api/submissions/prepare", json=body).status_code == 404


def test_private_explanation_is_not_echoed_in_error(client):
    seed()
    with closing(connect()) as db, db:
        row = db.execute("SELECT evaluation_id,card FROM cqa_batch_items LIMIT 1").fetchone()
        card = json.loads(row["card"])
        card["model_result"]["criteria"][0]["reason"] = "sig=secret-synthetic-value"
        db.execute("UPDATE cqa_batch_items SET card=? WHERE evaluation_id=?",
                   (json.dumps(card), row["evaluation_id"]))
    response = client.post("/api/submissions/prepare", json=request_body())
    assert response.status_code == 422
    assert "secret-synthetic-value" not in response.text
