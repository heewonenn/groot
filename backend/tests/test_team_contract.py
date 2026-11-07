"""Shared ZIP-compatible examples stay valid for models, cards and checklist APIs."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs/contracts"


def test_public_contract_and_evidence_identity():
    schema = json.loads((CONTRACTS / "cqa-team-v1.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    conversation = json.loads((CONTRACTS / "examples/conversation.synthetic.json").read_bytes())
    response = json.loads((CONTRACTS / "examples/server-response.synthetic.json").read_bytes())
    for document in (conversation, response, response["model_result"]):
        validator.validate(document)
    for path in (CONTRACTS / "checklists").glob("*.json"):
        validator.validate(json.loads(path.read_bytes()))
    messages = conversation["transcript"]
    assert conversation["message_count"] == len(messages)
    positions = {m["message_id"]: i for i, m in enumerate(messages, 1)}
    for mid, position in positions.items():
        assert mid == f"{conversation['conversation_id']}:m{position:04d}"
    for answer in response["model_result"]["criteria"]:
        for evidence in answer["evidence"]:
            assert evidence["group_id"] == conversation["group_id"]
            assert evidence["conversation_id"] == conversation["conversation_id"]
            assert evidence["message_id"] in positions
            if span := evidence.get("span"):
                assert positions[span["start_message_id"]] <= positions[span["end_message_id"]]
    for decision in response["review_history"]:
        for evidence in decision["evidence"]:
            assert evidence["group_id"] == conversation["group_id"]
            assert evidence["conversation_id"] == conversation["conversation_id"]
            assert evidence["message_id"] in positions
