"""Import and score the organizers' ten control chats on Google Gemma 4."""
import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from backend.app import submission
from backend.app.evaluation.gemini import GEMMA_4_31B
from scripts.check_dev_delivery import PRIVATE


def run_control(client: httpx.Client, group: str, destination: Path, minutes: float) -> int:
    deadline = time.monotonic() + minutes * 60
    if not group.strip() or group == "DEV":
        raise ValueError("Укажите точный идентификатор контрольной группы.")
    status = submission.get(client, "/api/model-status")
    routes = [status, status.get("quota_fallback") or {}, status.get("fallback") or {}]
    if not status.get("ready") or not any(
        r.get("provider") == "gemini" and r.get("model") == GEMMA_4_31B for r in routes
    ):
        raise ValueError("Gemma 4 31B не подключена на сервере.")
    submission.post(client, f"/api/groups/{quote(group, safe='')}/import")
    source = [
        submission.get_conversation(group, row["conversation_id"])
        for row in submission.list_conversations(group)
    ]
    submission.validate_dataset(source, "prod")

    def first(conversation: dict) -> str:
        left = (deadline - time.monotonic()) / 60
        if left <= 0:
            raise TimeoutError("Истекло время ожидания. Повторите команду с тем же group_id.")
        roots = submission.run_jobs(
            client, [conversation], group, GEMMA_4_31B, "control-gemma4-v1", left, compact=True,
        )
        return roots[0]

    def export(roots: list[str]) -> dict:
        doc = submission.build_submission(
            group_id=group, conversations=source, job_ids=roots,
            team_name=submission.TEAM, model_mode="external_api",
        )
        for row in doc["results"]:
            if any(PRIVATE.search(c["rationale"]) for c in row["criteria"]):
                raise ValueError(f"Проверьте приватные данные в {row['conversation_id']}.")
        submission.save_file(destination, doc, source, "prod")
        return doc

    def retry(root: str) -> None:
        state = submission.get(client, "/api/evaluation-jobs/" + root)
        if state["retry_job_id"]:
            submission.wait_jobs(client, [state["retry_job_id"]], deadline)
            return
        if state["items"][0]["status"] in {"failed", "interrupted"} and time.monotonic() < deadline:
            job = submission.post(
                client, f"/api/evaluation-jobs/{root}/retry", params={"gemma_only": "true"},
                headers={"Idempotency-Key": "control-gemma4-retry-" + root},
            )
            submission.wait_jobs(client, [job["job_id"]], deadline)

    # Two live chats share the Google token quota. Each keeps its own resumable root.
    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = list(pool.map(first, source))
        doc = export(roots)
        if any(r["status"] == "error" for r in doc["results"]):
            list(pool.map(retry, roots))
            doc = export(roots)
    failures = [r["conversation_id"] for r in doc["results"] if r["status"] == "error"]
    print(f"Готово {len(source) - len(failures)} из {len(source)}. Файл: {destination}")
    if failures:
        print("Требуют разбора: " + ", ".join(failures))
    return len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--wait-minutes", type=float, default=45)
    args = parser.parse_args()
    if not 0 < args.wait_minutes <= 60:
        parser.error("--wait-minutes должен быть больше нуля и не больше 60")
    try:
        destination = submission.ROOT / "results/prod/results.json"
        with httpx.Client(base_url="http://127.0.0.1:8765", timeout=60, trust_env=False) as client:
            failures = run_control(client, args.group_id, destination, args.wait_minutes)
            return int(bool(failures))
    except httpx.HTTPStatusError as exc:
        print(f"Локальный API вернул HTTP {exc.response.status_code}. "
              "Проверьте группу, источник и задания.", file=sys.stderr)
    except httpx.RequestError:
        print("Проверьте соединение с локальным API на порту 8765.", file=sys.stderr)
    except HTTPException as exc:
        print(f"Хранилище вернуло HTTP {exc.status_code}: {exc.detail}", file=sys.stderr)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Выгрузка контроля: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
