"""Read-only app-server inspection; output only non-secret model/config/skill facts."""
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

root = Path(__file__).resolve().parents[1]
proc = subprocess.Popen(
    [shutil.which("codex"), "app-server", "--stdio"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", cwd=root,
)
messages = queue.Queue()


def reader():
    for line in proc.stdout:
        try:
            messages.put(json.loads(line))
        except json.JSONDecodeError:
            pass


threading.Thread(target=reader, daemon=True).start()
counter = 0


def call(method, params):
    global counter
    counter += 1
    proc.stdin.write(json.dumps({"id": counter, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    while True:
        msg = messages.get(timeout=45)
        if msg.get("id") == counter:
            if "error" in msg:
                raise RuntimeError(msg["error"])
            return msg["result"]


try:
    call("initialize", {"clientInfo": {"name": "hackathon-audit", "version": "1.0"},
                        "capabilities": {"experimentalApi": True}})
    proc.stdin.write('{"method":"initialized"}\n')
    proc.stdin.flush()
    models = call("model/list", {"includeHidden": False, "limit": 100})
    skills = call("skills/list", {"cwds": [str(root)], "forceReload": True})
    config = call("config/read", {"cwd": str(root), "includeLayers": False})
    figma_status = call(
        "mcpServerStatus/list", {"serverName": "figma", "detail": "toolsAndAuthOnly"}
    )
    selected = config.get("config", {})
    local_skills = []
    for group in skills.get("data", []):
        for skill in group.get("skills", []):
            if str(root).lower() in str(skill.get("path", "")).lower():
                local_skills.append({k: skill.get(k) for k in ("name", "path", "enabled")})
    figma_summary = [{"name": s.get("name"), "authStatus": s.get("authStatus"),
                      "toolCount": len(s.get("tools", {}))} for s in figma_status.get("data", [])]
    result = {"models": models, "projectSkills": local_skills, "figmaStatus": figma_summary,
              "skillErrors": [g.get("errors", []) for g in skills.get("data", [])],
              "effectiveConfig": {k: selected.get(k) for k in
                                  ("model", "model_reasoning_effort", "agents")}}
    out = root / "docs" / "codex-audit.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
finally:
    proc.terminate()
    proc.wait(timeout=10)
