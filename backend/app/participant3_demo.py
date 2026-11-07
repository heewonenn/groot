"""Explicit, persistent synthetic fixtures; never an automatic model adapter."""

import argparse
import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from backend.app.api.checklists import get_repository
from backend.app.services.evaluation_cards import build_card
from backend.app.services.evaluation_jobs import DDL, _json, _now
from backend.app.team_data import SourceConversation, connect, get_conversation, store_group

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"
GROUP = "SYNTHETIC"
RECORDS = (
    ("synthetic-contract-001", "agent chat", "RU", False),
    ("synthetic-en-001", "agent chat", "EN", False),
    ("synthetic-kz-001", "agent chat", "KZ", False),
    ("synthetic-partial-001", "agent chat", "RU", True),
    ("synthetic-call-001", "voice call", "RU", False),
)
TRANSLATIONS = {
    "EN": [
        "How do I download a statement in this training service?",
        "Connecting you to an operator.",
        "The operator has joined.",
        "Hello, I am Anna. Open Statements and select Download.",
        "It worked. Can I choose a date range?",
        "Choose a range above Download. Contact us if you need help. Goodbye!",
    ],
    "KZ": [
        "Оқу сервисінде анықтаманы қалай жүктеймін?",
        "Сұрағыңызды операторға жіберемін.",
        "Оператор қосылды.",
        "Сәлеметсіз бе, мен Аннамын. Анықтамалар бөлімінен Жүктеу түймесін басыңыз.",
        "Болды. Кезеңді таңдауға бола ма?",
        "Жүктеу түймесінің үстінен кезеңді таңдаңыз. Сұрақтар болса, хабарласыңыз. Сау болыңыз!",
    ],
}


def seed() -> list[str]:
    """Add synthetic records without resetting saved appeals or existing cards."""
    source = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
    fixture = json.loads((EXAMPLES / "server-response.synthetic.json").read_bytes())
    records = []
    for cid, channel, language, _ in RECORDS:
        transcript = [
            {key: message[key] for key in ("speaker", "text", "timestamp")}
            for message in source["transcript"]
        ]
        for index, message in enumerate(transcript):
            if language in TRANSLATIONS:
                message["text"] = TRANSLATIONS[language][index]
            if channel == "voice call":
                seconds = (0, 1, 1, 10, 20, 40)[index]
                message["timestamp"] = f"2026-01-01T00:00:{seconds:02d}Z"
        records.append(SourceConversation(
            conversation_id=cid, channel=channel, language=language,
            transcript=transcript, audio_url=None,
        ))
    store_group(GROUP, records)
    # Load/seed immutable rule versions before taking our SQLite write lock.
    versions = {
        channel: get_repository().get_version(version)
        for channel, version in (
            ("agent chat", "freedom-chat@1"), ("voice call", "freedom-call@1")
        )
    }
    cards = []
    for cid, channel, _, partial in RECORDS:
        version = versions[channel]
        eid = "demo-" + cid
        model = deepcopy(fixture["model_result"])
        model.update(
            evaluation_id=eid, conversation_id=cid, group_id=GROUP,
            checklist_id=version.checklist_id,
            checklist_version_id=version.checklist_version_id,
        )
        for answer in model["criteria"]:
            for ref in answer["evidence"]:
                ref["conversation_id"] = cid
                ref["message_id"] = ref["message_id"].replace(source["conversation_id"], cid)
                if ref["span"]:
                    ref["span"] = {
                        key: value.replace(source["conversation_id"], cid)
                        for key, value in ref["span"].items()
                    }
        if partial:
            model["criteria"][0].update(
                value=None, applicability="undetermined", decision_status="unresolved",
                reason="Учебный частичный ответ: оценщик не смог разрешить критерий.",
                na_reason=None,
            )
        cards.append((version, build_card(eid, get_conversation(GROUP, cid), version, model)))
    with closing(connect()) as db, db:
        db.executescript(DDL)
        db.execute("BEGIN IMMEDIATE")
        for version, card in cards:
            job = "demo-" + version.checklist_version_id
            now = _now()
            db.execute(
                """INSERT OR IGNORE INTO cqa_batch_jobs
                   (id,parent_id,group_id,checklist_version_id,settings,created_at,
                    request_key,request_hash) VALUES(?,NULL,?,?,?,?,?,?)""",
                (job, GROUP, version.checklist_version_id,
                 _json({"provider": "synthetic-demo", "prompt_version": "contract-v1"}),
                 now, job, "explicit-synthetic-fixture"),
            )
            db.execute(
                """INSERT OR IGNORE INTO cqa_batch_items
                   (job_id,conversation_id,status,evaluation_id,card,started_at,finished_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (job, card["conversation_id"], card["status"], card["evaluation_id"],
                 _json(card), now, now),
            )
    return [card["evaluation_id"] for _, card in cards]


def main() -> None:
    argparse.ArgumentParser(
        description="Добавить искусственные примеры для карточки и апелляций (не AI и не GT)."
    ).parse_args()
    identifiers = seed()
    print(f"Готово: {len(identifiers)} синтетических карточек. Существующие решения сохранены.")
    print(
        "Откройте http://127.0.0.1:5173/analysis?group_id=SYNTHETIC"
        "&conversation_id=synthetic-contract-001"
    )


if __name__ == "__main__":
    main()
