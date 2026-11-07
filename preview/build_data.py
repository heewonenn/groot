"""Build public, synthetic preview data with the real participant-1 calculator.

No scoring arithmetic is implemented in JavaScript. A finite lookup contains all
allowed values of the two original eight-criterion versions, including N/A and
the independent critical flag. Rebuild after changing those saved versions.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.api.checklists import get_repository  # noqa: E402
from backend.app.evaluation.contract import PROMPT_VERSION  # noqa: E402
from backend.app.schemas.checklists import ScoringAnswer  # noqa: E402
from backend.app.services.scoring import calculate  # noqa: E402
from backend.app.team_data import connect, get_conversation, list_conversations  # noqa: E402


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Pool:
    def __init__(self) -> None:
        self.items: list[dict | list] = []
        self.indices: dict[str, int] = {}

    def put(self, item: dict | list) -> int:
        key = compact(item)
        if key not in self.indices:
            self.indices[key] = len(self.items)
            self.items.append(item)
        return self.indices[key]


def build(output: Path) -> dict:
    # The production demo seed contains only hand-authored public synthetic text.
    from backend.app.participant3_demo import seed

    with TemporaryDirectory(prefix="cqa-static-build-") as temporary:
        previous_db = os.environ.get("CQA_DB_PATH")
        os.environ["CQA_DB_PATH"] = str(Path(temporary) / "cqa.sqlite3")
        try:
            seed()
            repository = get_repository()
            conversations = [
                get_conversation("SYNTHETIC", row["conversation_id"])
                for row in list_conversations("SYNTHETIC")
            ]
            with closing(connect()) as db:
                items = db.execute(
                    """SELECT i.card,i.started_at,i.finished_at,j.id AS job_id,j.settings
                       FROM cqa_batch_items i JOIN cqa_batch_jobs j ON j.id=i.job_id
                       WHERE j.group_id='SYNTHETIC' AND i.card IS NOT NULL
                       ORDER BY i.rowid"""
                ).fetchall()
            versions = {}
            cards = []
            for item in items:
                card = json.loads(item["card"])
                version = repository.get_version(card["checklist_version_id"])
                versions[version.checklist_version_id] = version
                cards.append({
                    "evaluation": card,
                    "checklist": version.model_dump(mode="json"),
                    "final_answers": card["model_result"]["criteria"],
                    "revision": 0,
                    "final_status": card["status"],
                    "appeals": [],
                    "runtime": {
                        "job_id": item["job_id"], "started_at": item["started_at"],
                        "finished_at": item["finished_at"],
                        "model_config": json.loads(item["settings"]),
                        "model_run": None,
                    },
                })
            rows, sections, results = Pool(), Pool(), Pool()
            lookup = {}
            for version_id, version in versions.items():
                choices = []
                for rule in version.criteria:
                    values = ([False, True] if rule.answer_type in {"boolean", "flag"}
                              else [level.value for level in rule.levels])
                    choices.append([*values, None] if rule.allow_na else values)
                table = {}
                for values in itertools.product(*choices):
                    answers = [
                        ScoringAnswer(
                            criterion_id=rule.id, value=value,
                            applicability="not_applicable" if value is None else "applicable",
                        ) for rule, value in zip(version.criteria, values, strict=True)
                    ]
                    result = calculate(version, answers).model_dump(mode="json")
                    record = [
                        [rows.put(row) for row in result["criteria"]],
                        [sections.put(section) for section in result["sections"]],
                        result["applicable_weight"], result["earned_points"],
                        result["total"], result["gross_error"],
                    ]
                    # Integers and corresponding finite floats are the same legal scale value.
                    key = compact([int(v) if type(v) is float and v.is_integer() else v
                                   for v in values])
                    table[key] = results.put(record)
                lookup[version_id] = table
            payload = {
                "version": 3,
                "notice": (
                    "Статический предпросмотр: синтетические данные, сохранение в этом браузере. "
                    "Результаты заранее рассчитаны настоящим модулем участника 1. "
                    "Модель не вызывается; это не результаты DEV/PROD или Ground Truth."
                ),
                "model_status": {
                    "configured": False,
                    "ready": False,
                    "reason": "static_preview",
                    "provider": None,
                    "model": None,
                    "prompt_version": PROMPT_VERSION,
                    "audio_analysed": False,
                    "knowledge_version": None,
                },
                "conversations": conversations, "cards": cards,
                "checklists": [v.model_dump(mode="json") for v in versions.values()],
                "checklist_summaries": [
                    summary.model_dump(mode="json") for summary in repository.list_checklists()
                    if summary.checklist_id in {v.checklist_id for v in versions.values()}
                ],
                "checklist_drafts": {
                    version.checklist_id:
                    repository.get_draft(version.checklist_id).model_dump(mode="json")
                    for version in versions.values()
                },
                "calculations": {
                    "rows": rows.items, "sections": sections.items,
                    "records": results.items, "by_version": lookup,
                },
            }
        finally:
            if previous_db is None:
                os.environ.pop("CQA_DB_PATH", None)
            else:
                os.environ["CQA_DB_PATH"] = previous_db
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(compact(payload), encoding="utf-8")
    return {"output": str(output), "bytes": output.stat().st_size,
            "cards": len(cards), "combinations_per_version": {k: len(v) for k, v in lookup.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "preview/public/preview-data.json")
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
