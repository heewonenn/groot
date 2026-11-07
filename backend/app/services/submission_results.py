"""Build the organizers' JSON from terminal, persisted evaluation attempts."""

import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal

from backend.app.services.submission_validation import OFFICIAL_CRITERION_IDS
from backend.app.team_data import connect

BASE_CHECKLIST = {"agent chat": "freedom-chat@1", "voice call": "freedom-call@1"}
FAILURES = {
    "evaluation_timeout": "Модель не успела вернуть ответ.",
    "evaluation_failed": "Не удалось получить и проверить ответ модели.",
    "server_restart": "Обработка диалога прервана при перезапуске сервера.",
}
# Only short explanations leave the private DB. Catch common identifiers without logging them.
PRIVATE_TEXT = re.compile(
    r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:\d[ -]?){12,19}\b"
    r"|(?<!\w)\+\d[\d\s()\-]{8,}\d|\b(?:sig|access_token|api_key)=[^\s&]+",
    re.IGNORECASE,
)


def _error_row(cid: str, reason: str) -> dict:
    return {
        "conversation_id": cid,
        "status": "error",
        "criteria": [],
        "score": None,
        "error": reason,
    }


def _real_model(generation: dict) -> str:
    origin = str(generation.get("origin", "")).lower()
    name = generation.get("model_id")
    if (
        generation.get("mode") not in {"live", "cache"}
        or not origin
        or generation.get("is_ground_truth") is not False
        or any(word in origin for word in ("demo", "fixture", "hand_authored", "ground_truth"))
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise ValueError("Для сдачи требуется сохранённая оценка фактически использованной модели.")
    return name.strip()


def _verified_model(db: sqlite3.Connection, card: dict, settings: str) -> str:
    gen = card["generation"]
    model = _real_model(gen)
    if gen != card["model_result"]["generation"]:
        raise ValueError("Происхождение ответа модели и карточки различается.")
    if gen["mode"] == "live":
        return model
    # team-v1 has no cache source ID. Require an equivalent original inference in storage.
    matches = db.execute(
        """SELECT i.card FROM cqa_batch_items i JOIN cqa_batch_jobs j ON i.job_id=j.id
           WHERE j.group_id=? AND j.checklist_version_id=? AND j.settings=?
           AND i.conversation_id=? AND i.status IN ('completed','partial')""",
        (card["group_id"], card["checklist_version_id"], settings, card["conversation_id"]),
    )
    for row in matches:
        other = json.loads(row["card"])
        original = other["generation"]
        if (
            original["mode"] == "live"
            and original == other["model_result"]["generation"]
            and original.get("model_id") == model
            and other["model_result"]["criteria"] == card["model_result"]["criteria"]
        ):
            try:
                _real_model(original)
            except ValueError:
                continue
            return model
    raise ValueError("Не найден исходный вызов модели для закешированной оценки.")


def _evidence(answer: dict, conversation: dict, positions: dict[str, int]) -> list[int]:
    numbers = set()
    for ref in answer["evidence"]:
        if any(ref[k] != conversation[k] for k in ("group_id", "conversation_id")):
            raise ValueError("Доказательство относится к другому диалогу.")
        try:
            numbers.add(positions[ref["message_id"]])
            if span := ref.get("span"):
                start = positions[span["start_message_id"]]
                end = positions[span["end_message_id"]]
                if start > end:
                    raise ValueError("Перепутаны границы фрагмента доказательства.")
                numbers.update(range(start, end + 1))
        except KeyError:
            raise ValueError("Не найдена реплика, указанная в доказательстве.") from None
    return sorted(numbers)


def _success(card: dict, conversation: dict) -> dict:
    cid = conversation["conversation_id"]
    answers = {a["criterion_id"]: a for a in card["model_result"]["criteria"]}
    if (
        len(card["model_result"]["criteria"]) != 8
        or answers.keys() != OFFICIAL_CRITERION_IDS.keys()
    ):
        raise ValueError("Для успешной оценки нужны исходные восемь критериев.")
    positions = {m["message_id"]: i for i, m in enumerate(conversation["transcript"], 1)}
    criteria = []
    for key, code in OFFICIAL_CRITERION_IDS.items():
        answer = answers[key]
        rationale = answer["reason"].strip()
        if answer["value"] is None and (na := (answer.get("na_reason") or "").strip()):
            if na not in rationale:
                rationale = f"{rationale} {na}".strip()
        if PRIVATE_TEXT.search(rationale):
            raise ValueError(f"Проверьте приватные данные в обосновании {cid}, {code}.")
        criteria.append(
            {
                "criterion_id": code,
                "value": answer["value"],
                "rationale": rationale,
                "evidence_turns": _evidence(answer, conversation, positions),
            }
        )
    original = card["original_result"]
    return {
        "conversation_id": cid,
        "status": "ok",
        "criteria": criteria,
        "score": {
            "final_score": float(Decimal(original["total"])),
            "critical_flag": original["gross_error"],
        },
    }


def _latest_attempts(db: sqlite3.Connection, root: str, group: str) -> tuple[sqlite3.Row, dict]:
    job = db.execute("SELECT * FROM cqa_batch_jobs WHERE id=?", (root,)).fetchone()
    if job is None or job["parent_id"] is not None or job["group_id"] != group:
        raise ValueError("Выберите исходное задание из нужной группы, до повторных попыток.")
    attempts: dict[str, sqlite3.Row] = {}
    seen: set[str] = set()
    current = job
    while current is not None:
        if current["id"] in seen:
            raise ValueError("Обнаружен цикл в истории повторных попыток.")
        seen.add(current["id"])
        if any(current[k] != job[k] for k in ("group_id", "settings", "checklist_version_id")):
            raise ValueError("Повторное задание использует другую конфигурацию.")
        for item in db.execute("SELECT * FROM cqa_batch_items WHERE job_id=?", (current["id"],)):
            cid = item["conversation_id"]
            if current["id"] != root and (
                cid not in attempts or attempts[cid]["status"] == "completed"
            ):
                raise ValueError("Повторная попытка не соответствует родительскому заданию.")
            if item["status"] in {"queued", "running"}:
                raise ValueError("Оценивание ещё не завершено.")
            if cid in attempts and not item["started_at"] and item["status"] == "interrupted":
                # An unstarted retry cannot supersede a real, already recorded attempt.
                continue
            attempts[cid] = item
        current = db.execute(
            "SELECT * FROM cqa_batch_jobs WHERE parent_id=?", (current["id"],)
        ).fetchone()
    return job, attempts


def build_submission(
    *,
    group_id: str,
    conversations: list[dict],
    job_ids: list[str],
    team_name: str,
    model_mode: str,
) -> dict:
    """Read one queue snapshot. Validate the complete split before writing the file."""
    if model_mode not in {"local", "external_api", "hybrid"} or not team_name.strip():
        raise ValueError("Укажите название команды и способ запуска модели.")
    if not job_ids or len(set(job_ids)) != len(job_ids):
        raise ValueError("Нужен список неповторяющихся исходных заданий.")
    by_id = {c["conversation_id"]: c for c in conversations}
    if len(by_id) != len(conversations) or any(c["group_id"] != group_id for c in conversations):
        raise ValueError("Неверный список исходных диалогов.")
    results = {}
    models: set[str] = set()
    with closing(connect()) as db, db:
        db.execute("BEGIN")
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cqa_batch_jobs'"
        ).fetchone():
            raise ValueError("В базе ещё нет результатов пакетной оценки.")
        for root in job_ids:
            job, items = _latest_attempts(db, root, group_id)
            if results.keys() & items.keys():
                raise ValueError("Независимые задания содержат один и тот же диалог.")
            batch_models: set[str] = set()
            for cid, item in items.items():
                if (
                    cid not in by_id
                    or BASE_CHECKLIST.get(by_id[cid]["channel"]) != (job["checklist_version_id"])
                ):
                    raise ValueError("Задание не соответствует исходному набору и чек-листу.")
                state = item["status"]
                if not item["started_at"]:
                    raise ValueError("Есть диалог, оценивание которого ни разу не запускалось.")
                if state in {"failed", "interrupted"}:
                    results[cid] = _error_row(
                        cid,
                        FAILURES.get(
                            item["error_code"],
                            "Не удалось завершить оценку диалога.",
                        ),
                    )
                    continue
                if state not in {"completed", "partial"} or not item["card"]:
                    raise ValueError("Диалог не имеет завершённой попытки оценки.")
                card = json.loads(item["card"])
                if (
                    card["conversation_id"] != cid
                    or card["group_id"] != group_id
                    or card["checklist_version_id"] != job["checklist_version_id"]
                    or card["status"] != state
                    or card["evaluation_id"] != item["evaluation_id"]
                ):
                    raise ValueError("Сохранённая карточка не соответствует заданию.")
                batch_models.add(_verified_model(db, card, job["settings"]))
                if state == "partial":
                    results[cid] = _error_row(cid, "Не удалось оценить все восемь критериев.")
                else:
                    results[cid] = _success(card, by_id[cid])
            if not batch_models:
                attempted_model = json.loads(job["settings"]).get("model")
                if not attempted_model or not items:
                    raise ValueError("Неизвестна модель, использованная в неуспешном задании.")
                batch_models.add(attempted_model)
            models.update(batch_models)
    if results.keys() != by_id.keys():
        raise ValueError("В выбранных заданиях оценены не все исходные conversation_id.")
    return {
        "schema_version": "1.0",
        "team_name": team_name.strip(),
        "model": {"mode": model_mode, "name": ", ".join(sorted(models))},
        "results": [results[cid] for cid in by_id],
    }
