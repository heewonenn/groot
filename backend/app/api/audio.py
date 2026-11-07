"""Media delivery and content-verified transcript links for the team-v1 card."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..services.audio import load_recording
from ..team_data import CONVERSATIONS, DataRoute, get_conversation

router = APIRouter(tags=["audio"], route_class=DataRoute)
AUDIO_PATH = CONVERSATIONS + "/{conversation_id}/audio"


class Anchor(BaseModel):
    message_id: str
    start_seconds: float
    end_seconds: float
    word_offset: int
    consecutive_words: int


class AnchorList(BaseModel):
    group_id: str
    conversation_id: str
    status: Literal["verified_fragments", "unverified"]
    duration_seconds: float | None
    anchors: list[Anchor]


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    if header is None or not header.startswith("bytes=") or "," in header:
        return None  # Unknown units and multipart requests may receive the full resource.
    match = re.fullmatch(r"bytes=(\d{0,19})-(\d{0,19})", header)
    if match:
        left, right = match.groups()
        if left:
            start = int(left)
            end = min(int(right), size - 1) if right else size - 1
            if start <= end < size:
                return start, end
        elif right and int(right) > 0:
            return max(0, size - int(right)), size - 1
    raise HTTPException(416, "Недопустимый диапазон.", headers={"Content-Range": f"bytes */{size}"})


@router.head(AUDIO_PATH, include_in_schema=False)
@router.get(AUDIO_PATH, response_class=Response)
def serve_audio(group_id: str, conversation_id: str, request: Request) -> Response:
    conversation = get_conversation(group_id, conversation_id)
    recording = load_recording(conversation["audio_url"])
    size = len(recording.data)
    etag = f'"{recording.sha256}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "ETag": etag,
    }
    span = None
    if request.headers.get("If-Range", etag) == etag:
        span = byte_range(request.headers.get("Range"), size)
    start, end = span if span is not None else (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if span is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    data = b"" if request.method == "HEAD" else recording.data[start : end + 1]
    return Response(
        data,
        status_code=206 if span is not None else 200,
        media_type=recording.mime_type,
        headers=headers,
    )


@lru_cache(maxsize=1)
def proof_registry() -> dict:
    path = Path(__file__).resolve().parents[1] / "resources" / "audio_proof.json"
    return json.loads(path.read_text(encoding="utf-8"))


@router.get(AUDIO_PATH + "-anchors", response_model=AnchorList)
def get_anchors(group_id: str, conversation_id: str) -> dict:
    conversation = get_conversation(group_id, conversation_id)
    audio = load_recording(conversation["audio_url"])
    turns = conversation["transcript"]
    transcript = [{k: turn[k] for k in ("timestamp", "speaker", "text")} for turn in turns]
    encoded = json.dumps(transcript, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    response = {
        "group_id": group_id,
        "conversation_id": conversation_id,
        "status": "unverified",
        "duration_seconds": None,
        "anchors": [],
    }
    record = proof_registry().get(conversation["conversation_id"])
    if record and record["audio_sha256"] == audio.sha256 and record["transcript_sha256"] == digest:
        response["status"] = "verified_fragments"
        response["duration_seconds"] = record["duration_seconds"]
        for anchor in record["anchors"]:
            turn = turns[anchor["turn_index"]]
            response["anchors"].append(
                {
                    "message_id": turn["message_id"],
                    **{
                        k: anchor[k]
                        for k in (
                            "start_seconds",
                            "end_seconds",
                            "word_offset",
                            "consecutive_words",
                        )
                    },
                }
            )
    return response
