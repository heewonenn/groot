"""Connect a Windows demo backend to OpenRouter via its official browser sign-in."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx

from backend.app.api.checklists import get_repository
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.gemini import GEMINI_MODEL, GeminiGenerateContent
from backend.app.evaluation.openrouter import OpenRouterChat
from backend.app.evaluation.openrouter_local import load_profile, profile_path, save_profile
from backend.app.evaluation.provider import ModelError
from backend.app.evaluation.store import RunStore
from backend.app.services.scoring import ChecklistError
from backend.app.team_data import ROOT

CANDIDATES = (
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-31b-it:free",
    "recursive/flint-v1.0:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "liquid/lfm-3-1.7b:free",
)


def sign_in() -> str:
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    callback_path = "/callback/" + secrets.token_urlsafe(18)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    codes = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The incoming URL contains a single-use authorization code.

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path != callback_path:
                self.send_error(404)
                return
            if "error" in query:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization cancelled. You may close this tab.")
                codes.append("")
                return
            if not secrets.compare_digest(query.get("state", [""])[0].encode(), state.encode()):
                self.send_error(403)
                return
            code = query.get("code", [""])[0]
            if not code or len(code) > 4096:
                self.send_error(400)
                return
            codes.append(code)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write("Вход подтверждён. CQA проверяет резерв на учебном чате.".encode())

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        auth_url = "https://openrouter.ai/auth?" + urlencode({
            "callback_url": f"http://localhost:{server.server_port}{callback_path}",
            "state": state, "code_challenge_method": "S256",
            "code_challenge": challenge.rstrip(b"=").decode(), "key_label": "WE ARE GROOT CQA",
        })
        print("OpenRouter sign-in:", auth_url, flush=True)
        webbrowser.open(auth_url)
        until = time.monotonic() + 600
        while not codes and time.monotonic() < until:
            server.handle_request()
    if not codes:
        raise RuntimeError("OpenRouter sign-in timed out")
    if not codes[0]:
        raise SystemExit("OpenRouter sign-in cancelled.")
    reply = httpx.post("https://openrouter.ai/api/v1/auth/keys", json={
        "code": codes[0], "code_verifier": verifier, "code_challenge_method": "S256",
    }, timeout=20)
    reply.raise_for_status()
    key = reply.json().get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("OpenRouter did not issue a key")
    return key


def free_routes():
    response = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
    response.raise_for_status()
    models = {m["id"]: m for m in response.json()["data"]}
    for name in CANDIDATES:
        item = models.get(name)
        if not item:
            continue
        structured = "structured_outputs" in item["supported_parameters"]
        if not structured and "response_format" not in item["supported_parameters"]:
            continue
        if (all(item["pricing"].get(unit) == "0" for unit in ("prompt", "completion"))
                and item["pricing"].get("request", "0") == "0"):
            max_output = min(12000, item["top_provider"].get("max_completion_tokens") or 12000)
            reasoning = "reasoning" in item["supported_parameters"]
            # Dots passed the complete dialogue check in JSON object mode.
            json_object = not structured or name == "dots-studio/dots-3-note-preview:free"
            yield name, item["context_length"], max_output, json_object, reasoning


def check_switch(provider: OpenRouterChat, context: int) -> None:
    # Exercise the actual evaluator and wire schema. Only the primary's 429 is simulated.
    primary = GeminiGenerateContent(GEMINI_MODEL, "synthetic", transport=httpx.MockTransport(
        lambda _: httpx.Response(429),
    ))
    store = RunStore()
    evaluator = ModelEvaluator(primary, store, fallback_provider=provider,
                               fallback_context_token_limit=context, request_timeout=110,
                               total_timeout=240)
    rules = get_repository().get_version("freedom-chat@1").model_dump(mode="json")
    sample = ROOT / "docs/contracts/examples/conversation.synthetic.json"
    identifier = str(uuid4())
    result = evaluator(identifier, json.loads(sample.read_bytes()), rules, {})
    run = store.get(identifier)["metadata"]
    if (run["attempts"][0]["error_code"] != "provider_rate_limit"
            or result["generation"]["origin"] != "openrouter"
            or not any(a["decision_status"] == "evaluated" and a["evidence"]
                       for a in result["criteria"])):
        raise ModelError("synthetic_fallback_not_completed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconnect", action="store_true", help="authorize a replacement key")
    parser.add_argument("--model", choices=CANDIDATES, help="probe only this free model")
    parser.add_argument("--json-object", action="store_true",
                        help="request JSON mode with the full schema in the prompt")
    args = parser.parse_args()
    destination = profile_path()
    pending = destination.with_suffix(".pending.dpapi")
    routes = list(free_routes())
    if args.model:
        routes = [route for route in routes if route[0] == args.model]
    if not routes:
        raise SystemExit("No supported free model available in the current catalog.")
    key = os.getenv("OPENROUTER_API_KEY")
    if not key and not args.reconnect:
        for existing in (pending, destination):
            try:
                key = load_profile(existing, 12000)[0].api_key
            except (OSError, ValueError, KeyError, TypeError):
                continue
            break
    if not key:
        key = sign_in()
    # Preserve the authorized key through temporary provider outages. The backend only
    # reads the separate active profile created after the end-to-end probe succeeds.
    save_profile(pending, OpenRouterChat(routes[0][0], key), routes[0][1])
    old_db = os.getenv("CQA_DB_PATH")
    with TemporaryDirectory() as temporary:
        os.environ["CQA_DB_PATH"] = str(Path(temporary) / "probe.sqlite3")
        try:
            for model, context, max_output, json_object, reasoning in routes:
                print("Checking synthetic fallback:", model, flush=True)
                route = OpenRouterChat(
                    model, key, max_output, json_object=json_object or args.json_object,
                    low_reasoning=reasoning,
                )
                try:
                    check_switch(route, context)
                except ModelError as error:
                    print("Probe result:", error.code, flush=True)
                    cause = error.__context__
                    if error.code == "answer_rules_invalid" and cause is not None:
                        if isinstance(cause, ChecklistError):
                            print("Rule validation:", cause.code, flush=True)
                        elif str(cause) in {
                            "Missing, unknown or duplicate criterion",
                            "Evidence outside conversation", "Invalid evidence range",
                            "Invalid unresolved answer", "Missing evidence",
                            "Missing N/A justification", "No explanation",
                        }:
                            print("Rule validation:", str(cause), flush=True)
                    if error.code == "provider_auth":
                        pending.unlink(missing_ok=True)
                        break
                    continue
                save_profile(destination, route, context)
                pending.unlink(missing_ok=True)
                print("Verified free fallback:", model, flush=True)
                return
        finally:
            if old_db is None:
                os.environ.pop("CQA_DB_PATH", None)
            else:
                os.environ["CQA_DB_PATH"] = old_db
    raise SystemExit("No free route passed. The previous configuration was preserved.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError) as exc:
        # Never surface exchange bodies, request headers or OAuth callback URLs via exceptions.
        raise SystemExit(f"OpenRouter connection failed ({type(exc).__name__}).") from None
