"""Additive review API boundaries; the shared team-v1 card stays unchanged."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Text = Annotated[str, Field(min_length=1, max_length=12000)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1_000_000, le=1_000_000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Span(Contract):
    start_message_id: Identifier
    end_message_id: Identifier


class Evidence(Contract):
    group_id: Identifier
    conversation_id: Identifier
    message_id: Identifier
    kind: Literal["support", "absence", "context"] = "context"
    span: Span | None = None


class OpenAppeal(Contract):
    criterion_id: Identifier
    comment: Text


class ReviewRequest(Contract):
    action: Literal["confirm", "change"]
    value: StrictBool | Number | None
    applicability: Literal["applicable", "not_applicable"]
    reason: Text
    na_reason: Text | None = None
    evidence: list[Evidence] = Field(max_length=1000)
    expected_revision: int = Field(ge=0, strict=True)


class SavedDecision(Contract):
    action: Literal["confirm", "change"]
    value: StrictBool | Number | None
    applicability: Literal["applicable", "not_applicable"]
    reason: str
    na_reason: str | None
    evidence: list[Evidence]
    created_at: str
    previous_value: StrictBool | Number | None
    previous_applicability: Literal["applicable", "not_applicable", "undetermined"]
    previous_decision_status: Literal["evaluated", "unresolved"]


class Appeal(Contract):
    appeal_id: str
    evaluation_id: str
    criterion_id: str
    comment: str
    status: Literal["open", "resolved"]
    created_at: str
    decision: SavedDecision | None
