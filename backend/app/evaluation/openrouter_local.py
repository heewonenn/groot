"""User-scoped Windows storage for an explicitly connected free OpenRouter fallback."""

import ctypes
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from backend.app.evaluation.openrouter import OpenRouterChat
from backend.app.team_data import ROOT


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is required")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.addressof(buffer))
    result = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    transform = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Windows could not access the local OpenRouter profile")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(result.data, 0, result.size)
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(result.data))


def profile_path() -> Path:
    database = Path(os.getenv("CQA_DB_PATH") or ROOT / "data" / "cqa.sqlite3")
    return database.with_name("openrouter-fallback.dpapi")


def save_profile(path: Path, provider: OpenRouterChat, context: int) -> None:
    sealed = _dpapi(json.dumps({
        "model": provider.model, "context": context, "key": provider.api_key,
        "json_object": provider.json_object, "max_output": provider.max_output_tokens,
        "low_reasoning": provider.low_reasoning,
    }).encode(), encrypt=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(sealed)
    try:
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


def load_profile(path: Path, max_output: int) -> tuple[OpenRouterChat, int]:
    profile = json.loads(_dpapi(path.read_bytes(), encrypt=False))
    context = profile["context"]
    if type(context) is not int or not 1024 <= context <= 2_000_000:
        raise ValueError("invalid_openrouter_context")
    if not isinstance(profile["model"], str):
        raise ValueError("invalid_openrouter_model")
    if not isinstance(profile["key"], str) or not profile["key"]:
        raise ValueError("invalid_openrouter_key")
    mode = profile.get("json_object", False)
    low_reasoning = profile.get("low_reasoning", False)
    limit = profile.get("max_output", max_output)
    if (type(mode) is not bool or type(low_reasoning) is not bool or type(limit) is not int
            or not 512 <= limit <= 32000):
        raise ValueError("invalid_openrouter_settings")
    route = OpenRouterChat(
        profile["model"], profile["key"], min(max_output, limit),
        json_object=mode, low_reasoning=low_reasoning,
    )
    return route, context
