"""Watch criteria match engine — pure functions, tested out of the database.

Spec: specs/2026-11-08-criteres-veille.md §Moteur de match.
"""

from __future__ import annotations

from app.tasks.watch import (
    criterion_key,
    criterion_met,
    free_question_fields,
    watch_matches,
)

PASS_FIELDS = [
    {"key": "pertinent", "type": "boolean", "label": "Pertinent"},
    {"key": "design", "type": "choice", "label": "Design", "options": ["In vivo", "In vitro"]},
    {"key": "watch_q_1", "type": "boolean", "label": "Q?"},
]


def test_criterion_key_field_and_free():
    assert criterion_key({"kind": "field", "field_key": "pertinent"}, 3) == "pertinent"
    assert criterion_key({"kind": "free", "question": "Q1"}, 0) == "watch_q_0"
    assert criterion_key({"kind": "free", "question": "Q2"}, 2) == "watch_q_2"


def test_criterion_boolean_expected():
    crit = {"kind": "field", "field_key": "pertinent", "expected": True}
    assert criterion_met({"pertinent": True}, crit, "pertinent", {"pertinent"}) is True
    assert criterion_met({"pertinent": False}, crit, "pertinent", {"pertinent"}) is False
    # non-bool answer (a string "yes") never satisfies a boolean criterion
    assert criterion_met({"pertinent": "yes"}, crit, "pertinent", {"pertinent"}) is False


def test_criterion_choice_case_insensitive():
    crit = {"kind": "field", "field_key": "design", "expected": "in vivo"}
    assert criterion_met({"design": "In Vivo"}, crit, "design", {"design"}) is True
    assert criterion_met({"design": "In vitro"}, crit, "design", {"design"}) is False
    assert criterion_met({"design": "Unknown"}, crit, "design", {"design"}) is False


def test_criterion_confidence_bounds():
    crit = {
        "kind": "field",
        "field_key": "pertinent",
        "expected": True,
        "conf_min": 0.7,
        "conf_max": 1.0,
    }
    assert (
        criterion_met(
            {"pertinent": True, "confidence_score": 0.75}, crit, "pertinent", {"pertinent"}
        )
        is True
    )
    assert (
        criterion_met(
            {"pertinent": True, "confidence_score": 0.69}, crit, "pertinent", {"pertinent"}
        )
        is False
    )
    # bounds set + confidence absent -> criterion NOT satisfied
    assert criterion_met({"pertinent": True}, crit, "pertinent", {"pertinent"}) is False
    # no bounds + confidence absent + right answer -> satisfied
    plain = {"kind": "field", "field_key": "pertinent", "expected": True}
    assert criterion_met({"pertinent": True}, plain, "pertinent", {"pertinent"}) is True
    # single-side bound
    lower = {"kind": "field", "field_key": "pertinent", "expected": True, "conf_min": 0.5}
    assert (
        criterion_met(
            {"pertinent": True, "confidence_score": 0.9}, lower, "pertinent", {"pertinent"}
        )
        is True
    )
    upper = {"kind": "field", "field_key": "pertinent", "expected": True, "conf_max": 0.9}
    assert (
        criterion_met(
            {"pertinent": True, "confidence_score": 0.95}, upper, "pertinent", {"pertinent"}
        )
        is False
    )


def test_criterion_free_question():
    crit = {"kind": "free", "question": "Est-ce une étude in vivo ?", "expected": True}
    assert criterion_met({"watch_q_0": True}, crit, "watch_q_0", {"watch_q_0"}) is True
    assert criterion_met({"watch_q_0": False}, crit, "watch_q_0", {"watch_q_0"}) is False


def test_criterion_snapshot_guard():
    # field_key not in the pass snapshot -> never satisfied, never an error
    crit = {"kind": "field", "field_key": "gone", "expected": True}
    assert criterion_met({"gone": True}, crit, "gone", {"pertinent"}) is False
    assert criterion_met(None, crit, "gone", {"pertinent"}) is False


def test_criterion_no_decision():
    crit = {"kind": "field", "field_key": "pertinent", "expected": True}
    assert criterion_met(None, crit, "pertinent", {"pertinent"}) is False
    assert criterion_met({}, crit, "pertinent", {"pertinent"}) is False


def test_watch_matches_and():
    criteria = [
        {"kind": "field", "field_key": "pertinent", "expected": True},
        {"kind": "free", "question": "Q?", "expected": True},
    ]
    decision = {"pertinent": True, "watch_q_1": True}
    assert watch_matches(criteria, "AND", decision, PASS_FIELDS, 0.5, None) is True
    assert (
        watch_matches(
            criteria, "AND", {"pertinent": True, "watch_q_1": False}, PASS_FIELDS, 0.5, None
        )
        is False
    )
    # combine defaults to AND
    assert watch_matches(criteria, None, decision, PASS_FIELDS, 0.5, None) is True


def test_watch_matches_or():
    criteria = [
        {"kind": "field", "field_key": "pertinent", "expected": True},
        {"kind": "free", "question": "Q?", "expected": True},
    ]
    assert (
        watch_matches(
            criteria, "OR", {"pertinent": False, "watch_q_1": True}, PASS_FIELDS, 0.5, None
        )
        is True
    )
    assert (
        watch_matches(
            criteria, "OR", {"pertinent": False, "watch_q_1": False}, PASS_FIELDS, 0.5, None
        )
        is False
    )
    assert watch_matches(criteria, "OR", None, PASS_FIELDS, 0.5, None) is False


def test_watch_matches_legacy_fallback():
    primary = {"key": "pertinent", "type": "boolean"}
    # criteria None -> the existing rule: primary positive OR conf >= threshold
    assert watch_matches(None, None, {"pertinent": True}, PASS_FIELDS, 0.5, primary) is True
    assert watch_matches(None, None, {"confidence_score": 0.8}, PASS_FIELDS, 0.5, primary) is True
    assert (
        watch_matches(
            None, None, {"pertinent": False, "confidence_score": 0.2}, PASS_FIELDS, 0.5, primary
        )
        is False
    )
    assert watch_matches(None, None, None, PASS_FIELDS, 0.5, primary) is False
    # empty criteria list -> same fallback
    assert watch_matches([], "AND", {"pertinent": True}, PASS_FIELDS, 0.5, primary) is True


def test_free_question_fields_order_and_index():
    criteria = [
        {"kind": "field", "field_key": "pertinent", "expected": True},
        {"kind": "free", "question": "Est-ce une étude in vivo ?", "expected": True},
        {"kind": "field", "field_key": "design", "expected": "In vivo"},
        {"kind": "free", "question": "Aucun conflit d'intérêt ?", "expected": False},
    ]
    fields = free_question_fields(criteria)
    assert fields == [
        {"key": "watch_q_1", "type": "boolean", "label": "Est-ce une étude in vivo ?"},
        {"key": "watch_q_3", "type": "boolean", "label": "Aucun conflit d'intérêt ?"},
    ]


def test_free_question_fields_none_and_empty():
    assert free_question_fields(None) == []
    assert free_question_fields([]) == []
    assert (
        free_question_fields([{"kind": "field", "field_key": "pertinent", "expected": True}]) == []
    )


def test_i18n_criterion_keys_present_in_all_languages():
    import json
    from pathlib import Path

    m = json.loads((Path(__file__).parent.parent / "app" / "i18n" / "messages.json").read_text())
    for lang in ("fr", "en", "de", "es"):
        for key in ("watch_crit_field", "watch_crit_free", "watch_yes", "watch_no"):
            assert key in m[lang], f"{lang} missing {key}"
