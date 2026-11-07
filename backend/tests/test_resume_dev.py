import sqlite3
from contextlib import closing

import httpx
import pytest

from scripts import resume_dev


@pytest.mark.parametrize(("remaining", "attempts", "expected"), [(30, 1, 0), (50, 2, 20)])
def test_dev_preserves_reserves_and_limits_restarts(monkeypatch, remaining, attempts, expected):
    # Simulate a stale remote counter, including a second invocation the same UTC day.
    monkeypatch.setattr(resume_dev, "remaining", lambda: remaining)
    roots = {f"example-{i}": f"root-{i}" for i in range(35)}
    completed = set()

    def checkpoint(roots, _source):
        return {
            "results": [
                {"conversation_id": cid, "status": "ok" if cid in completed else "error"}
                for cid in roots
            ]
        }

    def respond(request):
        path = request.url.path
        if path.endswith("/model-status"):
            data = {
                "ready": True,
                "model": resume_dev.GEMINI,
                "fallback": {"provider": "openrouter", "model": resume_dev.DOTS},
            }
        elif "/evaluation-runs/" in path:
            data = {"attempts": [{"provider": "gemini"}] + [{"provider": "openrouter"}] * attempts}
        elif path.endswith("/retry"):
            job = path.split("/")[-2]
            data = {"job_id": job.replace("root-", "child-")}
        else:
            job = path.split("/")[-1]
            cid = "example-" + job.split("-")[-1]
            if job.startswith("child-"):
                completed.add(cid)
            data = {
                "job_id": job,
                "group_id": "DEV",
                "checklist_version_id": "freedom-chat@1",
                "retry_job_id": None,
                "items": [
                    {
                        "conversation_id": cid,
                        "evaluation_id": cid,
                        "status": "completed" if cid in completed else "failed",
                        "error_code": None,
                    }
                ],
            }
        return httpx.Response(200, json=data)

    monkeypatch.setattr(resume_dev, "checkpoint", checkpoint)
    with (
        httpx.Client(base_url="http://test/api", transport=httpx.MockTransport(respond)) as client,
        closing(sqlite3.connect(":memory:")) as ledger,
    ):
        for _ in range(2):
            resume_dev.recover(client, roots, [], 30, 20, ledger)
        assert ledger.execute("SELECT spent FROM usage").fetchone()[0] == expected
    assert len(completed) * attempts == expected
