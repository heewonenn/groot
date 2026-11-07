"""Serve organizer audio through a bounded, same-origin endpoint."""

import hashlib
import http.client
import ipaddress
import os
import socket
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import SplitResult, urljoin, urlsplit

from fastapi import HTTPException

DEFAULT_HOSTS = {
    "ec88f21f1accecf38e20095a9fe175.a5.environment.api.powerplatform.com",
    "freedomholdingcorporation.crm11.dynamics.com",
}
MAX_AUDIO = 32 * 1024 * 1024
MAX_HTML = 128 * 1024
CACHE_BYTES = 64 * 1024 * 1024
CACHE_TTL = 15 * 60
DEADLINE_SECONDS = 35
MAX_HOPS = 5
AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/mp4",
    "audio/webm",
}


class AudioSourceError(Exception):
    """A safe message without an upstream URL, transcript or source payload."""


class PlayerPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_audio = False
        self.src = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "audio":
            self.in_audio = True
        if self.in_audio and tag in {"audio", "source"} and not self.src:
            self.src = dict(attrs).get("src")

    def handle_endtag(self, tag: str) -> None:
        if tag == "audio":
            self.in_audio = False


def checked_parts(url: str) -> SplitResult:
    extra = os.environ.get("CQA_AUDIO_ALLOWED_HOSTS", "")
    allowed = DEFAULT_HOSTS | {name.strip().lower() for name in extra.split(",") if name.strip()}
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname not in allowed
            or parts.username is not None
            or parts.password is not None
            or parts.port not in {None, 443}
            or parts.fragment
            or "\\" in url
            or any(ord(c) < 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError
    except ValueError, TypeError:
        raise AudioSourceError("Источник записи не разрешён.") from None
    return parts


def public_addresses(host: str) -> list[str]:
    results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(info[4][0] for info in results))
    ips = [ipaddress.ip_address(address) for address in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):
        raise AudioSourceError("Недопустимый сетевой адрес источника записи.")
    return addresses


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    return min(15.0, left)


class PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host: str, addresses: list[str], deadline: float) -> None:
        super().__init__(host, timeout=remaining(deadline), context=ssl.create_default_context())
        self.addresses = addresses
        self.deadline = deadline

    def connect(self) -> None:
        # Connect to exactly a checked numeric address; keep the original DNS
        # name for SNI and certificate verification. No second DNS resolution.
        for index, address in enumerate(self.addresses):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            raw = socket.socket(family, socket.SOCK_STREAM)
            try:
                raw.settimeout(remaining(self.deadline))
                raw.connect((address, 443))
                raw.settimeout(remaining(self.deadline))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                raw.close()
                if time.monotonic() >= self.deadline or (
                    isinstance(error, TimeoutError) and index == len(self.addresses) - 1
                ):
                    raise TimeoutError from None
        raise AudioSourceError("Не удалось подключиться к источнику записи.")


def read_url(parts: SplitResult, deadline: float) -> tuple[str, str | None, bytes | None]:
    connection = PinnedTLS(parts.hostname, public_addresses(parts.hostname), deadline)
    try:
        path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        connection.request("GET", path, headers={"Accept-Encoding": "identity"})
        wire = connection.sock
        wire.settimeout(remaining(deadline))
        with connection.getresponse() as resp:
            if resp.status in {301, 302, 303, 307, 308}:
                return "redirect", resp.getheader("Location"), None
            if resp.status != 200:
                raise AudioSourceError("Источник не вернул запись.")
            mime = resp.headers.get_content_type()
            if mime not in AUDIO_TYPES | {"text/html", "application/octet-stream"}:
                raise AudioSourceError("Источник вернул неподдерживаемый тип записи.")
            cap = MAX_HTML if mime == "text/html" else MAX_AUDIO
            length = resp.getheader("Content-Length")
            if length and (not length.isdigit() or int(length) > cap):
                raise AudioSourceError("Размер ответа источника превышает лимит.")
            data = bytearray()
            while True:
                wire.settimeout(remaining(deadline))
                block = resp.read1(min(65536, cap + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > cap:
                    raise AudioSourceError("Размер ответа источника превышает лимит.")
            if not data or (length and int(length) != len(data)):
                raise AudioSourceError("Источник вернул неполную запись.")
            return mime, None, bytes(data)
    finally:
        connection.close()


@dataclass(frozen=True)
class Recording:
    data: bytes
    mime_type: str
    sha256: str


def resolve_recording(url: str) -> Recording:
    deadline = time.monotonic() + DEADLINE_SECONDS
    for _ in range(MAX_HOPS):
        parts = checked_parts(url)
        kind, redirect, body = read_url(parts, deadline)
        if kind == "redirect":
            if not redirect:
                raise AudioSourceError("Некорректное перенаправление записи.")
            url = urljoin(url, redirect)
        elif kind == "text/html":
            parser = PlayerPage()
            parser.feed(body.decode("utf-8-sig", errors="replace"))
            if not parser.src:
                raise AudioSourceError("На странице источника не обнаружена запись.")
            url = urljoin(url, parser.src)
        else:
            if kind == "application/octet-stream":
                if body.startswith(b"ID3") or (
                    len(body) >= 2 and body[0] == 255 and body[1] & 224 == 224
                ):
                    kind = "audio/mpeg"
                else:
                    raise AudioSourceError("Тип записи не распознан.")
            return Recording(body, kind, hashlib.sha256(body).hexdigest())
    raise AudioSourceError("Слишком много переходов до записи.")


_cache: OrderedDict[bytes, tuple[float, Recording]] = OrderedDict()
_lock = threading.Lock()
_download = threading.Lock()


def cached_recording(key: bytes) -> Recording | None:
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return cached[1]
    return None


def load_recording(url: str | None) -> Recording:
    if not url:
        raise HTTPException(404, "У этой версии нет записи.")
    key = hashlib.sha256(url.encode("utf-8")).digest()
    audio = cached_recording(key)
    if audio:
        return audio
    if not _download.acquire(timeout=DEADLINE_SECONDS):
        raise HTTPException(503, "Источник занят. Попробуйте ещё раз.")
    try:
        audio = cached_recording(key)
        if audio:
            return audio
        try:
            audio = resolve_recording(url)
        except TimeoutError:
            raise HTTPException(504, "Источник записи не ответил вовремя.") from None
        except AudioSourceError as error:
            raise HTTPException(502, str(error)) from None
        except OSError, ValueError, http.client.HTTPException:
            raise HTTPException(502, "Не удалось получить запись от источника.") from None
        with _lock:
            _cache[key] = time.monotonic(), audio
            _cache.move_to_end(key)
            while (
                sum(len(entry.data) for _, entry in _cache.values()) > CACHE_BYTES
                or len(_cache) > 32
            ):
                _cache.popitem(last=False)
        return audio
    finally:
        _download.release()
