"""Validation metrics (app/metrics.py) and gold examples (app/gold_examples.py)."""

import pytest

from app import gold_examples, metrics

FIELDS = [
    {"key": "ai", "label": "AI", "type": "boolean"},
    {"key": "sex", "label": "Sex", "type": "choice", "options": ["M", "F"]},
    {"key": "note", "label": "Note", "type": "text"},
]


def d(ai=True, sex="M", note="deep lab cut", conf=0.9):
    return {"ai": ai, "sex": sex, "note": note, "confidence_score": conf}


def test_score_binary_item_choice_and_text():
    gold = {1: d(), 2: d(ai=False, conf=0.1), 3: d(), 4: d(ai=False, sex="F", conf=0.1)}
    preds = {
        1: d(),
        2: d(ai=True, conf=0.8),
        3: d(ai=False, conf=0.2),
        4: d(ai=False, sex="F", conf=0.1),
    }
    s = metrics.score(FIELDS, preds, gold)
    assert s["n"] == 4
    rel = s[
        "relevance"
    ]  # relevance: 1 true positive, 1 false positive, 1 false negative, 1 true negative
    assert (rel["tp"], rel["fp"], rel["fn"], rel["tn"]) == (1, 1, 1, 1)
    assert rel["recall"] == pytest.approx(0.5) and rel["precision"] == pytest.approx(0.5)
    assert s["items"]["ai"]["recall"] == pytest.approx(0.5)
    assert s["items"]["sex"]["accuracy"] == 1.0
    assert s["items"]["note"]["f1"] == 1.0  # text: word overlap, not strict equality
    assert s["accuracy"] == pytest.approx((0.5 + 1.0) / 2)  # average of the boolean / choice items


def test_score_excludes_examples_and_unmatched_articles():
    gold = {1: d(), 2: d(), 3: d()}
    preds = {1: d(), 2: d(ai=False, conf=0.1)}  # 3 not predicted
    s = metrics.score(FIELDS, preds, gold, exclude={1})
    assert s["n"] == 1  # only article 2 counts (1 is a prompt example, 3 has no prediction)


def test_text_matching_is_not_strict_and_normalised():
    s = metrics.score(
        FIELDS,
        {1: d(sex=" m ", note="DeepLabCut was used")},
        {1: d(sex="M", note="deeplabcut used")},
    )
    assert s["items"]["sex"]["accuracy"] == 1.0  # case and spaces ignored
    assert 0 < s["items"]["note"]["f1"] < 1


def test_primary_value_follows_the_item_flagged_primary():
    gold = {1: d(), 2: d(ai=False, conf=0.1)}
    preds = {1: d(), 2: d(ai=True, conf=0.8)}
    s = metrics.score(FIELDS, preds, gold)
    assert metrics.primary_value(s, FIELDS)[:2] == ("relevance", "recall")  # default
    flagged = [{**FIELDS[0], "primary": True, "metric": "precision"}, *FIELDS[1:]]
    assert metrics.primary_value(s, flagged) == ("ai", "precision", pytest.approx(0.5))


def test_agreement_between_variants_and_disagreements():
    v = {
        "A": {1: d(), 2: d(ai=False, conf=0.1)},
        "B": {1: d(), 2: d(ai=False, conf=0.1)},
        "C": {1: d(), 2: d(ai=True, conf=0.9)},  # C deviates on article 2
    }
    ag = metrics.agreement(v, FIELDS)
    assert ag["A"] == ag["B"] == 1.0 and ag["C"] < 1.0
    assert metrics.agreement({"A": v["A"]}, FIELDS) == {}
    assert metrics.disagrees([d(), d()], FIELDS) is False
    assert metrics.disagrees([d(), d(sex="F")], FIELDS) is True
    assert metrics.disagrees([d(), None], FIELDS) is False


def test_pick_examples_balanced_and_reproducible():
    gold = {i: d(ai=True) for i in range(1, 7)} | {i: d(ai=False, conf=0.1) for i in range(7, 13)}
    a = gold_examples.pick_examples(gold, 4, seed=3)
    assert a == gold_examples.pick_examples(gold, 4, seed=3)
    assert sum(metrics.relevant(gold[i]) for i in a) == 2  # half relevant, half not
    assert (
        gold_examples.pick_examples(gold, 0) == []
        and len(gold_examples.pick_examples(gold, 99)) == 12
    )
