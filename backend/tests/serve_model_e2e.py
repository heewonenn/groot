"""Disposable real P2/P3 integration server with an explicitly synthetic provider.

Only this test entrypoint constructs the provider. It cannot call an external
service, and all of its generated cards are marked demo, never live or GT.
"""

import copy
import json
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.app.evaluation.contract import canonical
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.provider import ModelError, Reply
from backend.app.evaluation.store import RunStore
from backend.app.model_app import create_model_app
from backend.app.participant3_demo import seed
from backend.app.team_data import SourceConversation, store_group

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/contracts/examples"
CONVERSATIONS = (
    "model-first-001", "model-fail-once-001", "model-unknown-001",
    "model-version-001", "model-partial-001", "model-mobile-001",
    "model-concurrent-001",
)


class SyntheticBrowserProvider:
    """Controlled responses exercise the real evaluator without network calls."""

    name = "synthetic_browser"
    model = "synthetic-browser-model"
    live = False

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.lock = threading.Lock()

    def generate(self, instructions: str, payload: str, schema: dict, timeout: float) -> Reply:
        request = json.loads(payload)
        request = request.get("evaluation_input", request)
        conversation, rules = request["conversation"], request["checklist"]
        cid = conversation["conversation_id"]
        with self.lock:
            self.calls[cid] = self.calls.get(cid, 0) + 1
            attempt = self.calls[cid]
        time.sleep(0.15)
        if cid in {"model-fail-once-001", "model-concurrent-001"} and attempt == 1:
            raise ModelError("provider_auth")
        template = json.loads((EXAMPLES / "server-response.synthetic.json").read_bytes())
        originals = {a["criterion_id"]: a for a in template["model_result"]["criteria"]}
        messages = conversation["transcript"]
        operator = next(m for m in messages if m["speaker"] == "operator")
        answers = []
        for rule in rules["criteria"]:
            if rule["id"] in originals:
                answer = copy.deepcopy(originals[rule["id"]])
                for ref in answer["evidence"]:
                    ref.update(group_id=conversation["group_id"], conversation_id=cid)
                    ref["message_id"] = ref["message_id"].replace("synthetic-contract-001", cid)
                    if ref["span"]:
                        ref["span"] = {
                            key: value.replace("synthetic-contract-001", cid)
                            for key, value in ref["span"].items()
                        }
            else:
                value = rule["levels"][-1]["value"] if rule["answer_type"] == "scale" else True
                if rule["answer_type"] == "flag":
                    value = False
                answer = {
                    "criterion_id": rule["id"], "value": value,
                    "decision_status": "evaluated", "applicability": "applicable",
                    "reason": "Искусственный ответ для проверки передачи нового правила.",
                    "evidence": [{
                        "group_id": conversation["group_id"], "conversation_id": cid,
                        "message_id": operator["message_id"], "kind": "support", "span": None,
                    }],
                    "na_reason": None, "review_reasons": [], "analysis_scope": ["transcript"],
                    "knowledge_references": [],
                }
            # A visible marker verifies transport of an edited rule, not model semantics.
            if rule["id"] == "01" and "QA_FALSE" in rule["instruction"]:
                answer["value"] = False
                answer["reason"] = (
                    "Учебный маркер изменённого правила прочитан тестовым поставщиком."
                )
            if cid == "model-partial-001" and rule["id"] == "01":
                answer.update(
                    value=None, decision_status="unresolved", applicability="undetermined",
                    reason="Учебный неразрешённый ответ для ручного пересмотра.",
                    review_reasons=["Тестовый поставщик намеренно оставил ответ неразрешённым."],
                    na_reason=None,
                )
            answers.append(answer)
        usage = None if cid == "model-unknown-001" else {"input_tokens": 100, "output_tokens": 50}
        return Reply(canonical({"criteria": answers}), self.model, usage)


def main() -> None:
    if os.getenv("CQA_E2E_MODEL") != "1":
        raise SystemExit("This disposable test server requires CQA_E2E_MODEL=1.")
    with TemporaryDirectory(prefix="cqa-model-e2e-") as temporary:
        os.environ["CQA_DB_PATH"] = str(Path(temporary) / "cqa.sqlite3")
        seed()
        fixture = json.loads((EXAMPLES / "conversation.synthetic.json").read_bytes())
        sources = [SourceConversation.model_validate({
            "conversation_id": cid, "channel": fixture["channel"], "language": "RU",
            "audio_url": None,
            "transcript": [
                {key: message[key] for key in ("speaker", "text", "timestamp")}
                for message in fixture["transcript"]
            ],
        }) for cid in CONVERSATIONS]
        store_group("INTEGRATION", sources)
        evaluator = None if os.getenv("CQA_E2E_MODEL_DISABLED") == "1" else ModelEvaluator(
            SyntheticBrowserProvider(), RunStore(),
            input_usd_per_million=1, output_usd_per_million=2,
        )
        application = create_model_app(evaluator, "model_disabled" if evaluator is None else None)
        uvicorn.run(
            application, host="127.0.0.1", port=int(os.getenv("CQA_E2E_API_PORT", "8765")),
        )


if __name__ == "__main__":
    main()
