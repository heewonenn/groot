import json
from contextlib import closing
from pathlib import Path

import pytest

from backend.app.services.evaluation_jobs import DDL
from backend.app.services.submission_results import build_submission
from backend.app.team_data import connect

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"
CONV = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
CID = CONV["conversation_id"]
GID = CONV["group_id"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "export.sqlite3"))
    with closing(connect()) as connection:
        connection.executescript(DDL)
        yield connection


def card(mode="live", model="test-model"):
    result = json.loads((EXAMPLES / "server-response.synthetic.json").read_bytes())
    generation = dict(
        result["generation"],
        mode=mode,
        origin="local_provider",
        is_ground_truth=False,
        model_id=model,
    )
    result["generation"] = generation
    result["model_result"]["generation"] = generation.copy()
    for answer in result["model_result"]["criteria"]:
        answer["reason"] = "Explanation for " + answer["criterion_id"]
    return result


def add_job(db, job="root", parent=None, result=None, status="completed", started=True):
    db.execute(
        "INSERT INTO cqa_batch_jobs VALUES (?,?,?,?,?,?,?,?)",
        (job, parent, GID, "freedom-chat@1", '{"model":"test-model"}', "now", job, job),
    )
    db.execute(
        "INSERT INTO cqa_batch_items VALUES (?,?,?,?,?,?,?,?)",
        (
            job,
            CID,
            status,
            result["evaluation_id"] if result else None,
            json.dumps(result) if result else None,
            "evaluation_timeout" if not result else None,
            "start" if started else None,
            "end",
        ),
    )
    db.commit()


def export(job_ids=None, conversations=None):
    return build_submission(
        group_id=GID,
        conversations=conversations or [CONV],
        job_ids=job_ids or ["root"],
        team_name="Test",
        model_mode="local",
    )


def test_official_criteria_use_original_answer_and_whole_transcript(db):
    raw = card()
    ans = raw["model_result"]["criteria"][3]
    ans["evidence"][0]["span"] = {
        "start_message_id": CID + ":m0002",
        "end_message_id": CID + ":m0004",
    }
    add_job(db, result=raw)
    submission = export()
    assert submission["model"] == {"mode": "local", "name": "test-model"}
    row = submission["results"][0]
    assert row["score"] == {"final_score": 85.38, "critical_flag": False}
    assert row["criteria"][3] == {
        "criterion_id": "consultation",
        "value": 15,
        "rationale": "Explanation for 04",
        "evidence_turns": [2, 3, 4, 6],
    }
    assert row["criteria"][5]["value"] == 7
    assert row["criteria"][1]["value"] is None
    assert raw["model_result"]["criteria"][1]["na_reason"] in row["criteria"][1]["rationale"]
    assert "transcript" not in row and "review_history" not in row


def test_retry_is_used_and_duplicate_roots_not_arbitrarily_selected(db):
    add_job(db, status="failed")
    assert export()["results"][0] == {
        "conversation_id": CID,
        "status": "error",
        "criteria": [],
        "score": None,
        "error": "Модель не успела вернуть ответ.",
    }
    add_job(db, job="retry", parent="root", result=card())
    assert export()["results"][0]["status"] == "ok"
    add_job(db, job="independent", status="failed")
    with pytest.raises(ValueError, match="Независимые"):
        export(["root", "independent"])
    with pytest.raises(ValueError):
        export(["retry"])


@pytest.mark.parametrize("mode", ["demo", "cache"])
def test_reject_demo_or_unverified_cache(db, mode):
    add_job(db, result=card(mode))
    with pytest.raises(ValueError):
        export()


def test_cache_with_matching_live_run(db):
    add_job(db, result=card())
    replay = card("cache")
    replay["evaluation_id"] = replay["model_result"]["evaluation_id"] = "cache-eid"
    add_job(db, job="cached", result=replay)
    assert export(["cached"])["results"][0]["status"] == "ok"


@pytest.mark.parametrize("status", ["queued", "running", "interrupted"])
def test_unattempted_never_becomes_a_submission(db, status):
    add_job(db, status=status, started=False)
    with pytest.raises(ValueError):
        export()


def test_partial_and_missing_records(db):
    answer = card()
    answer.update(status="partial", original_result=None, final_result=None)
    add_job(db, result=answer, status="partial")
    assert export()["results"][0]["status"] == "error"
    with pytest.raises(ValueError, match="не все"):
        export(conversations=[CONV, {**CONV, "conversation_id": "missing"}])


def test_refuse_private_rationale_and_cross_dialogue_evidence(db):
    raw = card()
    answer = raw["model_result"]["criteria"][0]
    answer["reason"] = "Ссылка на запись содержит sig=private-synthetic-value"
    add_job(db, result=raw)
    with pytest.raises(ValueError, match="приватные") as error:
        export()
    assert "private-synthetic-value" not in str(error.value)
    answer["reason"] = "Проверено."
    answer["evidence"][0]["group_id"] = "OTHER"
    db.execute("UPDATE cqa_batch_items SET card=?", (json.dumps(raw),))
    db.commit()
    with pytest.raises(ValueError, match="другому диалогу"):
        export()
