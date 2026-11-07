import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import team_data
from backend.app.api import audio as api
from backend.app.services import audio as service


class AudioTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"CQA_DB_PATH": str(Path(folder) / "data.db")}))
        app = FastAPI()
        app.include_router(team_data.router)
        app.include_router(api.router)
        self.client = self.enterContext(TestClient(app))
        self.record = self.insert("TEST", "Synthetic hello", "https://source.invalid/audio")
        self.path = "/api/groups/TEST/conversations/c/audio"
        self.audio = service.Recording(b"0123456789", "audio/mpeg", "abc")

    def insert(self, group, text, url):
        record = team_data.SourceConversation(
            conversation_id="c",
            channel="voice call",
            language="EN",
            audio_url=url,
            transcript=[team_data.SourceMessage(timestamp="00:00", speaker="operator", text=text)],
        )
        team_data.store_group(group, [record])
        return record

    def test_single_byte_ranges_head_and_invalid_range(self):
        with patch.object(api, "load_recording", return_value=self.audio):
            for header, expected in [
                ("bytes=0-0", b"0"),
                ("bytes=4-", b"456789"),
                ("bytes=-3", b"789"),
                ("bytes=7-19", b"789"),
            ]:
                response = self.client.get(self.path, headers={"Range": header})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            response = self.client.head(self.path)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.headers["Content-Length"], "10")
            response = self.client.get(self.path, headers={"Range": "bytes=12-20"})
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.headers["Content-Range"], "bytes */10")
            response = self.client.get(
                self.path, headers={"Range": "bytes=4-", "If-Range": '"old"'}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, self.audio.data)

    def test_wrapper_and_redirect_destinations_must_be_allowed(self):
        host = "https://freedomholdingcorporation.crm11.dynamics.com/record"
        private = "http://127.0.0.1/forbidden"
        for outcome in [
            ("text/html", None, f'<audio><source src="{private}"></audio>'.encode()),
            ("redirect", private, None),
        ]:
            with patch.object(service, "read_url", return_value=outcome) as fetch:
                with self.assertRaises(service.AudioSourceError):
                    service.resolve_recording(host)
                self.assertEqual(fetch.call_count, 1)
        dns = [(None, None, None, None, ("127.0.0.1", 443))]
        with patch.object(service.socket, "getaddrinfo", return_value=dns):
            with self.assertRaises(service.AudioSourceError):
                service.public_addresses("example.test")

    def test_verified_message_id_and_independent_transcript_and_media_hashes(self):
        self.insert("OTHER", "Different group content", self.record.audio_url)
        bare = [message.model_dump() for message in self.record.transcript]
        digest = hashlib.sha256(json.dumps(bare, sort_keys=True, separators=(",", ":")).encode())
        record = {
            "audio_sha256": "abc",
            "transcript_sha256": digest.hexdigest(),
            "duration_seconds": 2,
            "anchors": [
                {
                    "turn_index": 0,
                    "start_seconds": 0.2,
                    "end_seconds": 0.7,
                    "word_offset": 0,
                    "consecutive_words": 2,
                }
            ],
        }
        with (
            patch.object(api, "load_recording", return_value=self.audio),
            patch.object(api, "proof_registry", return_value={"c": record}),
        ):
            response = self.client.get(self.path + "-anchors").json()
            self.assertEqual((response["group_id"], response["conversation_id"]), ("TEST", "c"))
            message_id = response["anchors"][0]["message_id"]
            self.assertEqual(message_id, "c:m0001")
            evidence = self.client.get(f"/api/groups/TEST/conversations/c/messages/{message_id}")
            self.assertEqual(evidence.json()["speaker"], "operator")
            changed = self.client.get("/api/groups/OTHER/conversations/c/audio-anchors").json()
            self.assertEqual(changed["anchors"], [])
            record["audio_sha256"] = "changed"
            self.assertEqual(self.client.get(self.path + "-anchors").json()["status"], "unverified")

    def test_missing_call_and_source_failure(self):
        self.assertEqual(self.client.get("/api/groups/NONE/conversations/c/audio").status_code, 404)
        result = self.client.get(self.path)
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("source.invalid", result.text)
        self.insert("EMPTY", "No source", None)
        self.assertEqual(
            self.client.get("/api/groups/EMPTY/conversations/c/audio").status_code, 404
        )
