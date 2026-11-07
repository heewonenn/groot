"""Serve the real app with a disposable DB and an explicitly synthetic conversation."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.app.main import app
from backend.app.team_data import SourceConversation, store_group


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    conversation = json.loads(
        (root / "docs/contracts/examples/conversation.synthetic.json").read_bytes()
    )
    with TemporaryDirectory(prefix="cqa-e2e-") as temporary:
        os.environ["CQA_DB_PATH"] = str(Path(temporary) / "cqa.sqlite3")
        source = SourceConversation.model_validate(
            {
                "conversation_id": conversation["conversation_id"],
                "channel": conversation["channel"],
                "language": conversation["language"],
                "audio_url": None,
                "transcript": [
                    {k: m[k] for k in ("speaker", "text", "timestamp")}
                    for m in conversation["transcript"]
                ],
            }
        )
        store_group(conversation["group_id"], [source])
        uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
