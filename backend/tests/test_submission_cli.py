"""An isolated rehearsal of the control run; no organizer records leave this test."""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import submission, team_data
from backend.app.evaluation.gemini import GEMMA_4_31B
from backend.app.evaluation.provider import ModelError
from backend.app.main import create_app
from backend.app.team_data import SourceConversation, store_group
from scripts.run_control import run_control

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"


def simulated_adapter(evaluation_id, conversation, rules, settings):
    if conversation["conversation_id"].endswith("9"):
        raise ModelError("provider_rate_limit")
    return synthetic_result(evaluation_id, conversation, rules, settings)


def synthetic_result(evaluation_id, conversation, rules, settings):
    cid = conversation["conversation_id"]
    stub = json.loads((EXAMPLES / "server-response.synthetic.json").read_bytes())["model_result"]
    old_id = stub["conversation_id"]
    stub = json.loads(json.dumps(stub).replace(old_id, cid))
    stub.update(evaluation_id=evaluation_id, group_id="TEST-CONTROL")
    stub["generation"].update(
        mode="live", model_id=settings["model"], origin="pytest_only", use_for_model_quality=False
    )
    for answer in stub["criteria"]:
        for ref in answer["evidence"]:
            ref["group_id"] = "TEST-CONTROL"
    return stub


def test_prepare_export_and_check_control(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(submission, "ROOT", tmp_path)
    example = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
    source = [
        SourceConversation.model_validate(
            {
                "conversation_id": f"pytest-{i}",
                "language": "RU",
                "channel": "agent chat",
                "audio_url": None,
                "transcript": [
                    {k: msg[k] for k in ("timestamp", "speaker", "text")}
                    for msg in example["transcript"]
                ],
            }
        )
        for i in range(10)
    ]
    store_group("TEST-CONTROL", source)
    with TestClient(create_app(evaluator=simulated_adapter)) as client:
        conversations = [
            submission.get_conversation("TEST-CONTROL", item.conversation_id) for item in source
        ]
        roots = submission.run_jobs(client, conversations, "TEST-CONTROL", "pytest-only", "run1", 1)
    destination = tmp_path / "results/prod/results.json"
    args = ["cli", "export", "prod", "--group-id", "TEST-CONTROL", "--mode", "local", "--job"]
    monkeypatch.setattr(sys, "argv", [*args, roots[0]])
    assert submission.main() == 0
    saved = destination.read_bytes()
    payload = json.loads(saved)
    assert payload["team_name"] == "WE ARE GROOT"
    assert sum(r["status"] == "ok" for r in payload["results"]) == 9
    assert payload["results"][0]["score"]["final_score"] == 85.38
    assert payload["results"][-1]["status"] == "error"
    assert "HTTP 429" in payload["results"][-1]["error"]
    assert payload["results"][-1]["score"] is None
    monkeypatch.setattr(sys, "argv", ["cli", "check", "prod", "--group-id", "TEST-CONTROL"])
    assert submission.main() == 0
    monkeypatch.setattr(sys, "argv", [*args, "absent"])
    assert submission.main() == 1
    assert destination.read_bytes() == saved


def test_duplicate_key_rejected(tmp_path):
    invalid = tmp_path / "bad.json"
    invalid.write_text('{"test": false, "test": true}', encoding="utf-8")
    with pytest.raises(ValueError):
        submission.read_file(invalid)


def test_control_import_retry_and_resume_on_ten_synthetic_chats(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "control.sqlite3"))
    example = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
    transcript = [
        {k: m[k] for k in ("timestamp", "speaker", "text")} for m in example["transcript"]
    ]
    records = [{"conversation_id": f"control-{i}", "channel": "agent chat", "language": "RU",
                "audio_url": None, "transcript": transcript} for i in range(10)]
    monkeypatch.setattr(team_data, "fetch_group", lambda group: {
        "isSuccess": True, "data": {"dataset_group_id": group, "records": records,
                                    "total_records": len(records)},
    })
    calls = Counter()

    def adapter(eid, conversation, rules, settings):
        cid = conversation["conversation_id"]
        calls[cid] += 1
        assert settings["model"] == GEMMA_4_31B
        if cid == "control-0" and calls[cid] == 1:
            raise ModelError("provider_rate_limit")
        if cid == "control-0":
            assert settings["gemma_only"] is True
        return synthetic_result(eid, conversation, rules, settings)

    app = create_app(evaluator=adapter)

    @app.get("/api/model-status")
    def status():
        return {"ready": True, "provider": "gemini", "model": GEMMA_4_31B}

    dest = tmp_path / "results" / "prod" / "results.json"
    with TestClient(app) as client:
        with pytest.raises(ValueError):
            run_control(client, "DEV", dest, 1)
        assert not dest.exists()
        assert run_control(client, "TEST-CONTROL", dest, 2) == 0
        saved = dest.read_bytes()
        assert run_control(client, "TEST-CONTROL", dest, 2) == 0
        assert saved == dest.read_bytes()
    assert sum(calls.values()) == 11
    assert all(row["status"] == "ok" for row in json.loads(saved)["results"])
