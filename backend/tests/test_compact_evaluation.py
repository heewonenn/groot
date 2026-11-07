import copy

import pytest

from backend.app.api.checklists import get_repository
from backend.app.evaluation.compact import LAYOUT
from backend.app.evaluation.engine import ModelEvaluator
from backend.app.evaluation.provider import ModelError
from backend.app.evaluation.store import RunStore
from backend.app.services.evaluation_cards import build_card
from backend.tests.test_model_evaluation import Scripted, example


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_DB_PATH", str(tmp_path / "batch.sqlite3"))
    rules = get_repository().get_version("freedom-chat@1")
    turns = example("conversation.synthetic.json")
    return turns, rules, compact_answers(turns)


def compact_answers(turns):
    fixture = example("server-response.synthetic.json")["model_result"]
    short = {}
    for a in fixture["criteria"]:
        value = a["value"]
        ref = a["evidence"][0]
        turn = next(
            i for i, m in enumerate(turns["transcript"], 1) if m["message_id"] == ref["message_id"]
        )
        choice = (
            "NA"
            if value is None
            else ("yes" if value else "no")
            if type(value) is bool
            else str(float(value))
        )
        proof = {"kind": ref["kind"], "first": turn, "last": turn}
        short[a["criterion_id"]] = {
            "choice": choice,
            "reason": a["reason"],
            "evidence": [proof],
            "timing": False,
        }
    return short


def test_compact_layout_preserves_numerical_values_and_source_turns(sample):
    turns, rules, short = sample
    primary = Scripted([ModelError("provider_rate_limit")])
    backup = Scripted([short])
    engine = ModelEvaluator(primary, RunStore(), fallback_provider=backup)
    result = engine("short", turns, rules.model_dump(mode="json"), {"response_layout": LAYOUT})
    card = build_card("short", turns, rules, result)
    assert card["original_result"]["total"] == "85.38"
    assert result["criteria"][0]["evidence"][0]["message_id"].endswith(":m0004")
    assert backup.requests[0][1]["conversation"]["transcript"][3]["turn"] == 4
    assert engine.store.get("short")["metadata"]["attempts"][1]["fallback"]


@pytest.mark.parametrize(
    "change",
    [
        {"evidence": [{"kind": "support", "first": 0, "last": 1}]},
        {"evidence": [{"kind": "absence", "first": 4, "last": 2}]},
        {"evidence": [{"kind": "context", "first": 2, "last": 999}]},
        {"choice": "NA"},
        {"choice": "1"},
        {"choice": True},
    ],
)
def test_bad_compact_answers_do_not_become_scores(sample, change):
    turns, rules, short = sample
    bad = copy.deepcopy(short)
    bad["01"].update(change)
    provider = Scripted([bad, bad])
    with pytest.raises(ModelError, match="answer_"):
        ModelEvaluator(provider, RunStore())(
            "bad",
            turns,
            rules.model_dump(mode="json"),
            {"response_layout": LAYOUT},
        )
    assert RunStore().get("bad")["result"] is None
