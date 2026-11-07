import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.checklists import get_repository
from backend.app.main import app
from backend.app.repositories.checklists import ChecklistRepository

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path):
    repository = ChecklistRepository(tmp_path / "checklists.sqlite3")
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as http:
            yield http, repository
    finally:
        app.dependency_overrides.pop(get_repository, None)


def test_version_lifecycle_persistence_and_old_version_calculation(client):
    http, repository = client
    seed = http.get("/api/checklist-versions/freedom-chat@1").json()
    draft = http.get("/api/checklists/freedom-chat/draft").json()
    draft["content"]["criteria"][0]["instruction"] = "Оператор поздоровался; имя не требуется."
    draft["content"]["criteria"][5]["levels"][2].update(
        value=8, description="Изменённая ступень", normalized_value="0.8"
    )
    saved = http.patch("/api/checklists/freedom-chat/draft", json={
        "expected_revision": draft["revision"], "content": draft["content"],
    })
    assert saved.status_code == 200
    published = http.post("/api/checklists/freedom-chat/versions", json={
        "expected_revision": saved.json()["revision"],
    })
    assert published.status_code == 200
    assert published.json()["checklist_version_id"] == "freedom-chat@2"
    assert http.get("/api/checklist-versions/freedom-chat@1").json() == seed
    assert len(http.get("/api/checklists/freedom-chat/versions").json()) == 2
    reopened = ChecklistRepository(repository.path)
    assert reopened.get_version("freedom-chat@1").model_dump(mode="json") == seed
    assert reopened.get_version("freedom-chat@2").criteria[5].levels[2].value == 8
    values = {"01": True, "02": None, "03": None, "04": 15, "05": True,
              "06": 7, "07": True, "08": False}
    payload = {"answers": [
        {"criterion_id": key, "value": value,
         "applicability": "not_applicable" if value is None else "applicable"}
        for key, value in values.items()
    ]}
    assert http.post("/api/checklist-versions/freedom-chat@1/calculate", json=payload).json()[
        "result"
    ]["total"] == "85.38"
    assert http.post(
        "/api/checklist-versions/freedom-chat@2/calculate", json=payload
    ).json()["error"]["code"] == "invalid_scale"


def test_copy_create_publish_and_conflict(client):
    http, _ = client
    created = http.post("/api/checklists", json={"title": "Мой чек-лист"})
    assert created.status_code == 201
    empty = created.json()
    assert http.post(f"/api/checklists/{empty['checklist_id']}/versions", json={
        "expected_revision": empty["revision"],
    }).status_code == 422
    copy = http.post("/api/checklists", json={
        "title": "Копия для проверки", "copy_from_version_id": "freedom-call@1",
    }).json()
    assert copy["derived_from_version_id"] == "freedom-call@1"
    assert copy["content"]["channels"] == ["voice call"]
    copy["content"]["title"] = "Обновлённый заголовок"
    path = f"/api/checklists/{copy['checklist_id']}"
    body = {"expected_revision": copy["revision"], "content": copy["content"]}
    assert http.patch(path + "/draft", json=body).status_code == 200
    conflict = http.patch(path + "/draft", json=body)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "draft_conflict"
    revision = http.get(path + "/draft").json()["revision"]
    version = http.post(path + "/versions", json={"expected_revision": revision}).json()
    current = http.get(path + "/draft").json()
    repeated = http.post(path + "/versions", json={"expected_revision": current["revision"]})
    assert repeated.json() == version
    assert len(http.get(path + "/versions").json()) == 1


def test_seed_schema_and_validation_errors(client):
    from jsonschema import Draft202012Validator

    http, _ = client
    validator = Draft202012Validator(json.loads(
        (ROOT / "docs/contracts/cqa-team-v1.schema.json").read_text(encoding="utf-8")
    ))
    assert len(http.get("/api/checklists").json()) == 2
    for version in ("freedom-chat@1", "freedom-call@1"):
        validator.validate(http.get(f"/api/checklist-versions/{version}").json())
    assert http.get("/api/checklist-versions/missing").status_code == 404
    bad = http.post("/api/checklists", json={"title": "   "})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
    assert "input" not in bad.text and "traceback" not in bad.text.lower()


def test_storage_failure_is_sanitized(client):
    http, _ = client

    def unavailable():
        raise sqlite3.OperationalError("private path and credential must not escape")

    app.dependency_overrides[get_repository] = unavailable
    result = http.get("/api/checklists")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "storage_unavailable"
    assert "private path" not in result.text


@pytest.mark.parametrize("ratio", ["0.0000001", "0.0000000000000000000000000001"])
def test_small_scale_ratios_keep_the_published_decimal_contract(client, ratio):
    from jsonschema import Draft202012Validator

    http, repository = client
    validator = Draft202012Validator(json.loads(
        (ROOT / "docs/contracts/cqa-team-v1.schema.json").read_text(encoding="utf-8")
    ))
    draft = http.get("/api/checklists/freedom-chat/draft").json()
    draft["content"]["criteria"][3]["levels"][1]["normalized_value"] = ratio
    saved = http.patch("/api/checklists/freedom-chat/draft", json={
        "expected_revision": draft["revision"], "content": draft["content"],
    })
    assert saved.status_code == 200
    assert saved.json()["content"]["criteria"][3]["levels"][1]["normalized_value"] == ratio
    published = http.post("/api/checklists/freedom-chat/versions", json={
        "expected_revision": saved.json()["revision"],
    })
    assert published.status_code == 200
    version_id = published.json()["checklist_version_id"]
    versions = [
        published.json(),
        http.get(f"/api/checklist-versions/{version_id}").json(),
        http.get("/api/checklists/freedom-chat/versions").json()[0],
    ]
    reopened = ChecklistRepository(repository.path).get_version(version_id)
    assert reopened.criteria[3].levels[1].normalized_value == Decimal(ratio)
    versions.append(json.loads(reopened.model_dump_json()))
    for version in versions:
        validator.validate(version)
        assert version["criteria"][3]["levels"][1]["normalized_value"] == ratio
    original = http.get("/api/checklist-versions/freedom-chat@1").json()
    assert original["criteria"][3]["levels"][1]["normalized_value"] == "0.25"


def test_draft_read_stays_consistent_when_another_connection_publishes(client, monkeypatch):
    http, reader = client
    connection = sqlite3.connect(reader.path)
    try:
        # Let the writer commit while the reader's snapshot is still open.
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()
    writer = ChecklistRepository(reader.path)
    before = http.get("/api/checklists/freedom-chat/draft").json()
    changed = writer.get_draft("freedom-chat").content
    changed.criteria[0].instruction = "Новое правило, опубликованное во время чтения."
    original_record = reader._record
    published = []

    def publish_after_first_read(connection, checklist_id):
        row = original_record(connection, checklist_id)
        saved = writer.update_draft(checklist_id, before["revision"], changed)
        published.append(writer.publish(checklist_id, saved.revision))
        return row

    with monkeypatch.context() as patch:
        patch.setattr(reader, "_record", publish_after_first_read)
        observed = http.get("/api/checklists/freedom-chat/draft")
    assert observed.status_code == 200
    assert published[0].checklist_version_id == "freedom-chat@2"
    assert observed.json() == before
    after = http.get("/api/checklists/freedom-chat/draft").json()
    assert after["base_version_id"] == "freedom-chat@2"
    assert after["revision"] == before["revision"] + 2
    assert after["content"]["criteria"][0]["instruction"] == changed.criteria[0].instruction
