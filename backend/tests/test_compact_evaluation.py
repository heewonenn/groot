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


def test_unverified_product_answer_stays_partial_and_timing_keeps_both_anchors(sample):
    """An arbitrary rule ID can remain unresolved without losing other checked answers."""
    turns, rules, short = sample
    rules.criteria[3].id = "product-accuracy"
    short["product-accuracy"] = short.pop("04")
    short["product-accuracy"].update(
        choice="review",
        reason="Клиент подтвердил результат, но официального источника для инструкции нет.",
    )
    short["06"].update(
        timing=True,
        reason="Между вопросом клиента и ответом оператора прошло 20 секунд.",
        evidence=[{"kind": "support", "first": 5, "last": 6}],
    )
    provider = Scripted([short])
    engine = ModelEvaluator(provider, RunStore())
    result = engine("grounding", turns, rules.model_dump(mode="json"), {"response_layout": LAYOUT})
    card = build_card("grounding", turns, rules, result)
    assert card["status"] == "partial"
    assert card["original_result"] is None and card["final_result"] is None
    accuracy = next(a for a in result["criteria"] if a["criterion_id"] == "product-accuracy")
    assert accuracy["decision_status"] == "unresolved"
    assert accuracy["applicability"] == "undetermined" and accuracy["value"] is None
    assert accuracy["na_reason"] is None and accuracy["review_reasons"]
    timing = next(a for a in result["criteria"] if a["criterion_id"] == "06")
    assert timing["analysis_scope"] == ["transcript", "timestamps"]
    assert timing["evidence"][0]["span"] == {
        "start_message_id": turns["transcript"][4]["message_id"],
        "end_message_id": turns["transcript"][5]["message_id"],
    }
    prompt = engine.store.get("grounding")["metadata"]["prompt_version"]
    assert prompt.startswith("cqa-evaluator-2.")


def test_compact_official_sources_survive_decoding_and_unknown_sources_fail(sample):
    from backend.app.evaluation.compact import CompactAnswers
    from backend.app.evaluation.contract import canonical, prepare, validate_result

    turns, rules, short = sample
    fragment = {"source_version_id": "synthetic-guide@1", "fragment_id": "navigation",
                "text": "In this synthetic service, download the statement from Statements."}
    payload, _ = prepare(turns, rules.model_dump(mode="json"), [fragment])
    compact = CompactAnswers(payload, rules)
    for answer in short.values():
        answer["sources"] = []
    short["04"]["sources"] = [1]
    decoded = compact.decode(canonical(short))
    accuracy = next(a for a in decoded["criteria"] if a["criterion_id"] == "04")
    assert accuracy["knowledge_references"] == [
        {"source_version_id": "synthetic-guide@1", "fragment_id": "navigation"}
    ]
    assert accuracy["analysis_scope"] == ["transcript", "knowledge"]
    fixture = example("server-response.synthetic.json")["model_result"]
    result = {**fixture, "criteria": decoded["criteria"]}
    assert validate_result(result, payload, rules)["status"] == "completed"
    short["04"]["sources"] = [2]
    with pytest.raises(ModelError, match="answer_schema_invalid"):
        compact.decode(canonical(short))
