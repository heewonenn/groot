"""An explicit demo must stay synthetic and preserve saved reviews after reseeding."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.app.main import create_app
from backend.app.participant3_demo import seed

GROUP = "SYNTHETIC"
CID = "synthetic-contract-001"
EID = "demo-" + CID
SCHEMA = Path(__file__).resolve().parents[2] / "docs/contracts/cqa-team-v1.schema.json"


def test_demo_is_explicit_and_never_claims_model_or_ground_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "demo.sqlite3"))
    with TestClient(create_app()) as client:
        assert client.get("/api/evaluations").json() == []
        assert client.get(f"/api/groups/{GROUP}/conversations").status_code == 404
        assert client.get("/api/capabilities").json()["model_available"] is False
        assert len(seed()) == 5
        summaries = client.get(f"/api/groups/{GROUP}/conversations").json()
        assert {row["language"] for row in summaries} == {"RU", "KZ", "EN"}
        assert {row["channel"] for row in summaries} == {"agent chat", "voice call"}
        validator = Draft202012Validator(json.loads(SCHEMA.read_bytes()))
        for row in summaries:
            eid = "demo-" + row["conversation_id"]
            canonical = client.get(f"/api/evaluations/{eid}").json()
            validator.validate(canonical)
            assert canonical["generation"]["mode"] == "demo"
            assert canonical["generation"]["is_ground_truth"] is False
            assert canonical["generation"]["use_for_model_quality"] is False
            assert row["has_audio"] is False
        partial = client.get("/api/evaluation-cards/demo-synthetic-partial-001").json()
        assert partial["final_status"] == "partial"
        assert partial["evaluation"]["original_result"] is None
        assert partial["evaluation"]["final_result"] is None


def test_reseed_and_server_restart_preserve_review_and_original(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "demo.sqlite3"))
    seed()
    with TestClient(create_app()) as client:
        original = client.get(f"/api/evaluation-cards/{EID}").json()
        appeal = client.post(
            f"/api/evaluations/{EID}/appeals",
            json={"criterion_id": "04", "comment": "Проверка сохранения после перезапуска."},
        ).json()
        response = client.post(
            f"/api/appeals/{appeal['appeal_id']}/decision",
            headers={"Idempotency-Key": "demo-persistence"},
            json={
                "action": "change", "value": 20, "applicability": "applicable",
                "reason": "Учебный пересмотр по сохранённой реплике.", "na_reason": None,
                "expected_revision": 0,
                "evidence": [{
                    "group_id": GROUP, "conversation_id": CID,
                    "message_id": CID + ":m0006", "kind": "support", "span": None,
                }],
            },
        )
        assert response.status_code == 200, response.text
        reviewed = response.json()
        assert reviewed["evaluation"]["original_result"] == (
            original["evaluation"]["original_result"]
        )
        assert reviewed["evaluation"]["original_result"]["total"] == "85.38"
        assert reviewed["evaluation"]["final_result"]["total"] == "93.08"
    seed()
    with TestClient(create_app()) as client:
        assert client.get(f"/api/evaluation-cards/{EID}").json() == reviewed
        assert len(client.get(f"/api/evaluations?group_id={GROUP}").json()) == 5
        assert client.get("/api/capabilities").json()["model_available"] is False
