"""Validate the organizers' JSON 1.0 submission against the original dataset."""

from collections import Counter
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.schemas.checklists import ChecklistVersion, ScoringAnswer
from backend.app.services.scoring import ChecklistError, calculate

OFFICIAL_CRITERION_IDS = {
    "01": "contact", "02": "discovery", "03": "identification", "04": "consultation",
    "05": "empathy", "06": "dialogue", "07": "closure", "08": "critical",
}
_SPLITS = {"dev": {"agent chat": 60, "voice call": 5}, "prod": {"agent chat": 10}}
Text = Annotated[str, Field(pattern=r"\S")]


class _Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class _Model(_Contract):
    mode: Literal["local", "external_api", "hybrid"]
    name: Text


class _Criterion(_Contract):
    criterion_id: str
    value: bool | int | float | None
    rationale: Text
    evidence_turns: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]


class _Score(_Contract):
    final_score: Annotated[int | float, Field(ge=0, le=100)]
    critical_flag: bool


class _Ok(_Contract):
    conversation_id: Text
    status: Literal["ok"]
    criteria: Annotated[list[_Criterion], Field(min_length=8, max_length=8)]
    score: _Score


class _Failed(_Contract):
    conversation_id: Text
    status: Literal["error"]
    criteria: Annotated[list[_Criterion], Field(max_length=0)]
    score: None
    error: Text


class _Submission(_Contract):
    schema_version: Literal["1.0"]
    team_name: Text
    model: _Model
    results: list[Annotated[_Ok | _Failed, Field(discriminator="status")]]


@lru_cache(maxsize=2)
def _official_checklist(channel: str) -> ChecklistVersion:
    filename = "call-v1.json" if channel == "voice call" else "chat-v1.json"
    path = Path(__file__).resolve().parents[1] / "resources" / "checklists" / filename
    return ChecklistVersion.model_validate_json(path.read_bytes())


def validate_dataset(conversations: list[dict], split: str) -> dict[str, tuple[str, int]]:
    """Check the complete source set and index its channel and number of original turns by ID."""
    if split not in _SPLITS:
        raise ValueError("Набор должен быть dev или prod.")
    index: dict[str, tuple[str, int]] = {}
    for record in conversations:
        if not isinstance(record, dict):
            raise ValueError("Некорректная запись исходного набора.")
        cid = record.get("conversation_id")
        channel = record.get("channel")
        turns = record.get("transcript")
        if not isinstance(cid, str) or not cid.strip() or cid in index:
            raise ValueError("Пустой или повторяющийся conversation_id в исходном наборе.")
        if channel not in ("agent chat", "voice call") or not isinstance(turns, list) or not turns:
            raise ValueError("Некорректный канал или транскрипт в исходном наборе.")
        index[cid] = (channel, len(turns))
    if Counter(channel for channel, _ in index.values()) != _SPLITS[split]:
        raise ValueError(f"Состав исходного набора не соответствует {split}.")
    return index


def validate_submission(payload: dict, conversations: list[dict], split: str) -> None:
    """Raise ValueError for invalid shape, membership, evidence indices, answers or score."""
    sources = validate_dataset(conversations, split)
    try:
        document = _Submission.model_validate(payload)
    except ValidationError as exc:
        # Payloads may contain private text. Only include the row index and Pydantic error code.
        issue = exc.errors(include_input=False, include_context=False, include_url=False)[0]
        loc = issue["loc"]
        where = "файл"
        if len(loc) > 1 and loc[0] == "results" and isinstance(loc[1], int):
            where = f"results[{loc[1]}]"
        raise ValueError(f"Ошибка структуры JSON ({where}, {issue['type']}).") from None
    ids = [entry.conversation_id for entry in document.results]
    if len(ids) != len(sources) or set(ids) != sources.keys():
        raise ValueError("Идентификаторы results не совпадают с полным исходным набором.")
    for i, entry in enumerate(document.results):
        if isinstance(entry, _Failed):
            continue
        channel, turn_count = sources[entry.conversation_id]
        criteria = {item.criterion_id: item for item in entry.criteria}
        if set(criteria) != set(OFFICIAL_CRITERION_IDS.values()):
            raise ValueError(f"results[{i}]: требуются восемь разных официальных criterion_id.")
        answers = []
        for internal, external in OFFICIAL_CRITERION_IDS.items():
            criterion = criteria[external]
            if any(turn > turn_count for turn in criterion.evidence_turns):
                raise ValueError(f"results[{i}], {external}: реплика за пределами транскрипта.")
            try:
                answer = ScoringAnswer(
                    criterion_id=internal, value=criterion.value,
                    applicability="not_applicable" if criterion.value is None else "applicable",
                )
            except ValidationError:
                raise ValueError(f"results[{i}], {external}: некорректное значение.") from None
            answers.append(answer)
        try:
            calculated = calculate(_official_checklist(channel), answers)
        except ChecklistError as exc:
            raise ValueError(f"results[{i}]: {exc.message}") from None
        if calculated.total is None or Decimal(str(entry.score.final_score)) != Decimal(
            calculated.total
        ):
            raise ValueError(f"results[{i}]: final_score не совпадает с расчётом чек-листа.")
        if entry.score.critical_flag != calculated.gross_error:
            raise ValueError(f"results[{i}]: critical_flag не совпадает со значением critical.")
