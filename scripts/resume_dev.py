"""Retry unfinished DEV conversations with the team's reserved OpenRouter requests."""

import argparse
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from backend.app import submission
from backend.app.evaluation.openrouter_local import load_profile, profile_path
from backend.app.services.submission_results import build_submission
from scripts.check_dev_delivery import DEFAULT_ROOTS, RESULT, audit, inputs

BRANCH = "codex/participant-2"
GEMINI = "gemini-3.8-flash"
DOTS = "dots-studio/dots-3-note-preview:free"
# The engine has three attempts overall. Gemini always receives the first one.
MAX_DOTS_PER_JOB = 2


def remaining() -> int:
    route, _ = load_profile(profile_path(), 12000)
    if route.model != DOTS:
        raise ValueError("The local OpenRouter profile must select the team's free Dots route")
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.get(
            "https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + route.api_key}
        )
        response.raise_for_status()
        count = response.json()["data"]["free_model_daily_requests"]["remaining"]
    if type(count) is not int or count < 0:
        raise ValueError("Unrecognized OpenRouter quota")
    return count


def latest(client: httpx.Client, job: str, cid: str) -> dict:
    seen = set()
    while job not in seen:
        seen.add(job)
        state = submission.get(client, f"/evaluation-jobs/{job}")
        if (
            state["group_id"] != "DEV"
            or state["checklist_version_id"] not in submission.VERSIONS.values()
            or [r["conversation_id"] for r in state["items"]] != [cid]
        ):
            raise ValueError("Expected a single original DEV conversation per root job")
        if not state["retry_job_id"]:
            return state
        job = state["retry_job_id"]
    raise ValueError("Cycle in job history")


def wait_batch(client: httpx.Client, jobs: dict) -> dict:
    end = time.monotonic() + 1200
    pending = dict(jobs)
    finished = {}
    while pending:
        for cid, job in list(pending.items()):
            item = submission.get(client, f"/evaluation-jobs/{job}")["items"][0]
            if item["status"] not in {"queued", "running"}:
                finished[cid] = item
                del pending[cid]
        if pending:
            if time.monotonic() >= end:
                raise TimeoutError("Backend still owns the pending jobs. Check their status.")
            time.sleep(4)
    return finished


def charged(client: httpx.Client, item: dict) -> int:
    if item.get("evaluation_id"):
        run = submission.get(client, "/evaluation-runs/" + item["evaluation_id"])
        attempts = run["attempts"]
        if attempts and attempts[0]["provider"] == "gemini":
            return sum(a["provider"] == "openrouter" for a in attempts)
    return MAX_DOTS_PER_JOB


def checkpoint(roots: dict, source: list[dict]) -> dict:
    old = submission.read_file(RESULT)
    new = build_submission(
        group_id="DEV",
        conversations=source,
        job_ids=list(roots.values()),
        team_name=submission.TEAM,
        model_mode="external_api",
    )
    rows = {r["conversation_id"]: r for r in new["results"]}
    for saved in old["results"]:
        if saved["status"] == "ok" and saved != rows.get(saved["conversation_id"]):
            raise ValueError("An existing official score changed: " + saved["conversation_id"])
    audit(new, roots, source)
    if new != old:
        submission.save_file(RESULT, new, source, "dev")
    return new


def recover(
    client: httpx.Client,
    roots: dict,
    source: list[dict],
    reserve: int,
    budget: int,
    ledger: sqlite3.Connection,
) -> None:
    status = submission.get(client, "/model-status")
    if (
        not status["ready"]
        or status["model"] != GEMINI
        or status["fallback"] != {"provider": "openrouter", "model": DOTS}
    ):
        raise ValueError("Expected configured Gemini 3.8 Flash and free Dots fallback")
    doc = checkpoint(roots, source)
    todo = [r["conversation_id"] for r in doc["results"] if r["status"] == "error"]
    tries = Counter()
    rate_probes = 0
    width = 1
    day = datetime.now(UTC).date().isoformat()
    ledger.execute("CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, spent INTEGER NOT NULL)")
    ledger.execute("INSERT OR IGNORE INTO usage VALUES(?,0)", (day,))
    ledger.commit()
    while todo and datetime.now(UTC).date().isoformat() == day:
        left = remaining()
        # Reserve before any POST. An interruption leaves the worst-case debit in place.
        with ledger:
            ledger.execute("BEGIN IMMEDIATE")
            used = ledger.execute("SELECT spent FROM usage WHERE day=?", (day,)).fetchone()[0]
            capacity = min(len(todo), width, min(budget - used, left - reserve) // MAX_DOTS_PER_JOB)
            if capacity <= 0:
                print(
                    f"DEV stopped. OpenRouter remaining={left}, DEV remaining={budget - used}"
                )
                break
            held = capacity * MAX_DOTS_PER_JOB
            ledger.execute("UPDATE usage SET spent=spent+? WHERE day=?", (held, day))
        chunk, todo = todo[:capacity], todo[capacity:]
        jobs = {}
        for cid in chunk:
            state = latest(client, roots[cid], cid)
            if state["items"][0]["status"] == "completed":
                continue
            jid = state["job_id"]
            if state["items"][0]["status"] not in {"queued", "running"}:
                child = submission.post(
                    client,
                    f"/evaluation-jobs/{jid}/retry",
                    headers={"Idempotency-Key": "dev-recover-" + jid},
                )
                jid = child["job_id"]
            jobs[cid] = jid
        outcomes = wait_batch(client, jobs)
        spent = sum(charged(client, row) for row in outcomes.values())
        with ledger:
            ledger.execute("UPDATE usage SET spent=spent+? WHERE day=?", (spent - held, day))
        for cid, item in outcomes.items():
            tries[cid] += 1
            if item["status"] != "completed" and tries[cid] < 2:
                todo.append(cid)
        checkpoint(roots, source)
        if any(item["status"] == "completed" for item in outcomes.values()):
            width, rate_probes = 3, 0
        elif outcomes and all(r["error_code"] == "provider_rate_limit" for r in outcomes.values()):
            width = 1
            rate_probes += 1
            if rate_probes >= 2:
                break
            time.sleep(60)


def wait_for_reset() -> bool:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC) + timedelta(seconds=5)
    print("Waiting for", reset.astimezone().isoformat(), flush=True)
    while (seconds := (reset - datetime.now(UTC)).total_seconds()) > 0:
        time.sleep(min(45, seconds))
    return datetime.now(UTC) < reset + timedelta(hours=1)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=submission.ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def publish() -> None:
    path = "results/dev/results.json"
    if not git("status", "--porcelain", "--", path):
        return
    remote = git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()
    if (
        git("branch", "--show-current") != BRANCH
        or not remote
        or git("rev-parse", "HEAD") != remote[0]
    ):
        raise ValueError("Check the branch and its unpublished commits before pushing DEV")
    git("commit", "--only", "-m", "Update verified DEV evaluations after quota reset", "--", path)
    git("push", "origin", f"{git('rev-parse', 'HEAD')}:refs/heads/{BRANCH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=Path, default=DEFAULT_ROOTS)
    parser.add_argument("--control-reserve", type=int, default=20)
    parser.add_argument("--jury-reserve", type=int, default=10)
    parser.add_argument("--max-dev-requests", type=int, default=20)
    parser.add_argument("--wait-for-reset", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if min(args.control_reserve, args.jury_reserve, args.max_dev_requests) < 0:
        parser.error("Request budgets must be nonnegative")
    roots, source = inputs(args.roots)
    folder = profile_path().parent
    # Separate databases keep the process lock away from both the API and short budget writes.
    with closing(sqlite3.connect(folder / "dev-recovery-lock.sqlite3", timeout=0)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("Could not acquire the local DEV runner lock") from exc
        left = remaining()
        print(
            f"OpenRouter remaining={left}, control={args.control_reserve}, "
            f"jury={args.jury_reserve}, DEV cap={args.max_dev_requests}",
            flush=True,
        )
        if args.wait_for_reset and left == 0 and not wait_for_reset():
            return
        with (
            httpx.Client(
                base_url="http://127.0.0.1:8765/api", timeout=20, trust_env=False
            ) as client,
            closing(sqlite3.connect(folder / "dev-recovery-usage.sqlite3")) as ledger,
        ):
            recover(
                client,
                roots,
                source,
                args.control_reserve + args.jury_reserve,
                args.max_dev_requests,
                ledger,
            )
        if args.publish:
            publish()


if __name__ == "__main__":
    main()
