import os
import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import team_data as app
from backend.app.main import app as application


def chat(conversation_id, greeting="Здравствуйте!"):
    return {
        "conversation_id": conversation_id,
        "channel": "agent chat",
        "language": "RU",
        "audio_url": None,
        "transcript": [
            {"timestamp": "00:00", "speaker": "bot", "text": "Соединяю."},
            {"timestamp": "00:00", "speaker": "system", "text": "Оператор подключён"},
            {"timestamp": "00:00", "speaker": "operator", "text": greeting},
            {"timestamp": "00:02", "speaker": "client", "text": "Спасибо."},
        ],
    }


def envelope(group_id, records):
    return {
        "isSuccess": True,
        "error": "",
        "data": {
            "dataset_group_id": group_id,
            "total_records": len(records),
            "records": records,
        },
    }


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"CQA_DB_PATH": str(Path(temp) / "cqa.db")}))
        self.upstream = self.enterContext(patch.object(app, "fetch_group"))
        self.client = self.enterContext(TestClient(application))

    def import_group(self, group_id, records):
        self.upstream.return_value = envelope(group_id, records)
        return self.client.post(f"/api/groups/{group_id}/import")

    def test_reimport_preserves_evidence_and_order(self):
        record = chat("c1")
        path = "/api/groups/DEV/conversations/c1"
        for inserted, unchanged in [(1, 0), (0, 1)]:
            response = self.import_group("DEV", [record])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json(),
                {
                    "group_id": "DEV",
                    "inserted": inserted,
                    "unchanged": unchanged,
                    "total": 1,
                },
            )
            turns = self.client.get(path).json()["transcript"]
            self.assertEqual(len(turns), len(record["transcript"]))
            for index, (original, stored) in enumerate(
                zip(record["transcript"], turns, strict=True), start=1
            ):
                message_id = f"c1:m{index:04d}"
                self.assertEqual(stored["message_id"], message_id)
                self.assertEqual({key: stored[key] for key in original}, original)
                evidence = self.client.get(f"{path}/messages/{message_id}")
                self.assertEqual(evidence.status_code, 200)
                self.assertEqual(evidence.json()["message_id"], message_id)
        self.assertEqual(self.client.get(f"{path}/messages/c1:missing").status_code, 404)

    def test_group_ids_isolate_conversations_and_evidence(self):
        path = "/api/groups/{}/conversations/c1/messages/c1:m0003"
        for group, text in [("DEV", "Первый текст"), ("CONTROL", "Второй текст")]:
            self.assertEqual(self.import_group(group, [chat("c1", text)]).status_code, 200)
        self.assertEqual(self.client.get(path.format("DEV")).json()["text"], "Первый текст")
        self.assertEqual(self.client.get(path.format("CONTROL")).json()["text"], "Второй текст")

    def test_call_keeps_original_time_when_recording_url_changes(self):
        call = chat("call1")
        call.update(channel="voice call", audio_url="https://example.invalid/audio/first")
        stamp = "2025-09-08T13:37:01.250+05:00"
        call["transcript"] = [{"speaker": "operator", "timestamp": stamp, "text": "Добрый день."}]
        self.assertEqual(self.import_group("DEV", [chat("chat1"), call]).status_code, 200)
        call["audio_url"] = "https://example.invalid/audio/new"
        self.assertEqual(self.import_group("DEV", [chat("chat1"), call]).status_code, 200)
        path = "/api/groups/DEV/conversations"
        only_calls = self.client.get(path, params={"channel": "voice call", "language": "RU"})
        self.assertEqual([row["conversation_id"] for row in only_calls.json()], ["call1"])
        item = self.client.get(f"{path}/call1").json()
        self.assertEqual(item["audio_url"], call["audio_url"])
        self.assertEqual(item["transcript"][0]["timestamp"], stamp)
        self.assertEqual(item["transcript"][0]["message_id"], "call1:m0001")

    def test_conflict_rolls_back_entire_group(self):
        self.assertEqual(self.import_group("DEV", [chat("existing")]).status_code, 200)
        response = self.import_group("DEV", [chat("new"), chat("existing", "Текст изменён")])
        self.assertEqual(response.status_code, 409)
        base = "/api/groups/DEV/conversations"
        self.assertEqual(self.client.get(f"{base}/new").status_code, 404)
        evidence = self.client.get(f"{base}/existing/messages/existing:m0003")
        self.assertEqual(evidence.json()["text"], "Здравствуйте!")

    def test_invalid_snapshots_leave_existing_data_intact(self):
        self.assertEqual(self.import_group("DEV", [chat("existing")]).status_code, 200)
        bad_count = envelope("DEV", [chat("existing"), chat("new")])
        bad_count["data"]["total_records"] = 3
        for name, invalid in [
            ("upstream-error", {"isSuccess": False, "error": "offline"}),
            ("mismatched-group", envelope("OTHER", [chat("existing"), chat("new")])),
            ("mismatched-count", bad_count),
            ("duplicate", envelope("DEV", [chat("existing"), chat("new"), chat("new")])),
        ]:
            with self.subTest(name=name):
                self.upstream.return_value = invalid
                response = self.client.post("/api/groups/DEV/import")
                self.assertEqual(response.status_code, 502, response.text)
                records = self.client.get("/api/groups/DEV/conversations").json()
                self.assertEqual([r["conversation_id"] for r in records], ["existing"])

    def test_checklists_and_dialogues_share_the_database(self):
        version = self.client.get("/api/checklist-versions/freedom-chat@1")
        self.assertEqual(version.status_code, 200)
        self.assertEqual(self.import_group("DEV", [chat("c1")]).status_code, 200)
        self.assertEqual(
            self.client.get("/api/checklist-versions/freedom-chat@1").json(), version.json()
        )
        values = {
            "01": True,
            "02": None,
            "03": None,
            "04": 15,
            "05": True,
            "06": 7,
            "07": True,
            "08": False,
        }
        result = self.client.post(
            "/api/checklist-versions/freedom-chat@1/calculate",
            json={
                "answers": [
                    {
                        "criterion_id": key,
                        "value": value,
                        "applicability": "not_applicable" if value is None else "applicable",
                    }
                    for key, value in values.items()
                ]
            },
        )
        self.assertEqual(result.json()["result"]["total"], "85.38")

    def test_removed_record_rejects_entire_snapshot(self):
        self.assertEqual(self.import_group("DEV", [chat("c1"), chat("c2")]).status_code, 200)
        self.assertEqual(self.import_group("DEV", [chat("c1")]).status_code, 409)
        stored = self.client.get("/api/groups/DEV/conversations").json()
        self.assertEqual(len(stored), 2)

    def test_storage_failure_does_not_expose_internal_paths(self):
        with patch.object(app, "connect", side_effect=sqlite3.OperationalError("private path")):
            result = self.client.get("/api/groups/DEV/conversations")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("private path", result.text)

    def test_source_query_preserves_signature_bytes(self):
        url = "https://example.invalid/invoke?sig=synthetic%2Bvalue&group_id=DEV&sv=1.0"
        with patch.dict(os.environ, {"CQA_SOURCE_URL": url}):
            self.assertEqual(
                app.source_url("CONTROL 10"),
                "https://example.invalid/invoke?sig=synthetic%2Bvalue&sv=1.0&group_id=CONTROL%2010",
            )

    def test_health_when_queue_storage_is_unavailable(self):
        with patch("backend.app.main.JobRunner.start", side_effect=sqlite3.OperationalError):
            with TestClient(application) as client:
                self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
                self.assertFalse(client.get("/api/capabilities").json()["model_available"])

    def test_bounded_snapshot_and_private_field_errors(self):
        with patch.dict(os.environ, {"CQA_IMPORT_MAX_BYTES": "4"}):
            self.assertEqual(app.read_snapshot(BytesIO(b"{}")), {})
            with self.assertRaises(app.HTTPException) as error:
                app.read_snapshot(BytesIO(b'{"very":"long"}'))
            self.assertEqual(error.exception.status_code, 413)
        for field, invalid in [("text", " "), ("timestamp", "private-invalid-stamp")]:
            with self.subTest(field=field):
                broken = chat("unknown")
                broken["transcript"][2][field] = invalid
                result = self.import_group("DEV", [chat("valid"), broken])
                self.assertEqual(result.status_code, 502)
                self.assertIn(f"records.1.transcript.2.{field}", result.json()["detail"])
                self.assertNotIn("private-invalid-stamp", result.text)
                self.assertEqual(self.client.get("/api/groups/DEV/conversations").status_code, 404)


if __name__ == "__main__":
    unittest.main()
