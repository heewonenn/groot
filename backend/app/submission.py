"""Prepare or verify the official hackathon results from the team's actual evaluations."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx
from fastapi import HTTPException

from backend.app.services.submission_results import build_submission
from backend.app.services.submission_validation import validate_dataset, validate_submission
from backend.app.team_data import ROOT, connect, get_conversation, list_conversations

TEAM = "WE ARE GROOT"
VERSIONS = {"agent chat": "freedom-chat@1", "voice call": "freedom-call@1"}


def read_file(path: Path) -> dict:
    def unique_keys(pairs: list) -> dict:
        document = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("В JSON повторяется имя поля.")
            document[key] = value
        return document

    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)


def check_document(document: dict, conversations: list[dict], split: str) -> None:
    validate_submission(document, conversations, split)
    if document["team_name"] != TEAM:
        raise ValueError(f"Ожидается название команды {TEAM}.")


def save_file(path: Path, document: dict, conversations: list[dict], split: str) -> None:
    check_document(document, conversations, split)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=".submission-",
            suffix=".tmp",
            encoding="utf-8",
            newline="\n",
            delete=False,
        ) as stream:
            staged = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        check_document(read_file(staged), conversations, split)
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def post(client: httpx.Client, path: str, **kwargs) -> dict:
    response = client.post(path, **kwargs)
    response.raise_for_status()
    return response.json()


def get(client: httpx.Client, path: str) -> dict:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def run_jobs(
    client: httpx.Client,
    conversations: list[dict],
    group: str,
    model: str,
    run_key: str,
    minutes: float,
    *,
    compact: bool = False,
) -> list[str]:
    if not get(client, "/api/capabilities").get("model_available"):
        raise ValueError("Оценщик не подключён. Экспорт доступен для ранее завершённых заданий.")
    deadline = time.monotonic() + minutes * 60
    roots = []
    settings = {"model": model}
    if compact:
        settings["response_layout"] = "turns-v1"
    for channel, version in VERSIONS.items():
        ids = sorted(row["conversation_id"] for row in conversations if row["channel"] == channel)
        if not ids:
            continue
        body = {
            "group_id": group,
            "conversation_ids": ids,
            "checklist_version_id": version,
            "model_config": settings,
        }
        encoded = json.dumps([run_key, body], sort_keys=True, ensure_ascii=False)
        key = "submission-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        job = post(client, "/api/evaluation-jobs", json=body, headers={"Idempotency-Key": key})
        roots.append(job["job_id"])
        print(f"{version} · {len(ids)} · job_id={job['job_id']}", flush=True)
    pending = set(roots)
    while pending:
        for job_id in sorted(pending):
            state = get(client, f"/api/evaluation-jobs/{job_id}")
            if state["retry_job_id"]:
                pending.remove(job_id)
                pending.add(state["retry_job_id"])
            elif state["status"] not in {"queued", "running"}:
                pending.remove(job_id)
                print(f"{job_id} · {state['status']} · {state['counts']}", flush=True)
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Истекло время ожидания. Задания сохранены; повторите команду.")
            time.sleep(min(2, remaining))
    return roots


def job_status(group: str) -> None:
    with closing(connect()) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cqa_batch_jobs'"
        ).fetchone():
            rows = db.execute(
                """SELECT j.id, j.parent_id, i.status, count(*) n FROM cqa_batch_jobs j
                   JOIN cqa_batch_items i ON i.job_id=j.id WHERE j.group_id=?
                   GROUP BY j.id,i.status ORDER BY j.created_at,i.status""",
                (group,),
            ).fetchall()
        else:
            rows = []
    if not rows:
        print("Сохранённых заданий нет.")
    for row in rows:
        print(f"{row['id']} · parent={row['parent_id'] or '-'} · {row['status']}={row['n']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "run", "export", "check"):
        command = commands.add_parser(name)
        command.add_argument("split", choices=["dev", "prod"])
        command.add_argument("--group-id", help="Exact organizer group ID (DEV by default for dev)")
        if name in {"run", "export"}:
            command.add_argument(
                "--mode", required=True, choices=["local", "external_api", "hybrid"]
            )
        if name == "export":
            command.add_argument(
                "--job",
                dest="jobs",
                required=True,
                action="append",
                help="Root job ID. Repeat for other channels.",
            )
        if name == "run":
            command.add_argument("--compact", action="store_true",
                                 help="Use checklist choices and 1-based transcript evidence")
            command.add_argument(
                "--model", required=True, help="Actual configured model identifier"
            )
            command.add_argument("--run-key", default="official-v1")
            command.add_argument("--wait-minutes", type=float, default=45)
    args = parser.parse_args()
    group = args.group_id or ("DEV" if args.split == "dev" else "")
    if not group:
        parser.error("Для prod укажите выданный организаторами --group-id.")
    if args.command == "run" and not 0 < args.wait_minutes <= 1440:
        parser.error("--wait-minutes должен быть в диапазоне (0, 1440].")
    path = ROOT / "results" / args.split / "results.json"
    try:
        rows = list_conversations(group)
        if args.command == "status":
            print(f"{group} · диалогов: {len(rows)} · файл: {path.is_file()}")
            job_status(group)
            return 0
        conversations = [get_conversation(group, row["conversation_id"]) for row in rows]
        validate_dataset(conversations, args.split)
        if args.command == "check":
            document = read_file(path)
            check_document(document, conversations, args.split)
        else:
            if args.command == "run":
                with httpx.Client(base_url="http://127.0.0.1:8765", timeout=30) as client:
                    roots = run_jobs(
                        client, conversations, group, args.model, args.run_key, args.wait_minutes,
                        compact=args.compact,
                    )
            else:
                roots = args.jobs
            document = build_submission(
                group_id=group,
                conversations=conversations,
                job_ids=roots,
                team_name=TEAM,
                model_mode=args.mode,
            )
            save_file(path, document, conversations, args.split)
        ok = sum(row["status"] == "ok" for row in document["results"])
        print(f"{path} · ok={ok}, error={len(rows) - ok} · проверено")
    except HTTPException as exc:
        print(f"Ошибка HTTP {exc.status_code}: {exc.detail}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(
            f"API вернул HTTP {exc.response.status_code}. Проверьте группу и настройки модели.",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError:
        print(
            "Сервер на 127.0.0.1:8765 недоступен. Сохранённые задания не удалены.", file=sys.stderr
        )
        return 1
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Выгрузка не завершена: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
