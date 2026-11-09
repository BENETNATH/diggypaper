"""Spec 2026-11-09-triage-seuils-fonds-champs §2 — triage paramétré par projet +
persistance de field_confidences.

R1: préréglages seulement, inégalités strictes (0.85 exact → à revoir),
``null`` = famille désactivée. Politique None (projet sans politique) =
comportement actuel 0.85/0.15.
"""

import pytest

from app.db.models import PredictionStatus
from app.schema import normalize_triage_policy
from app.tasks.inference import _field_confidences, _triage


class TestTriagePresets:
    def test_default_policy_keeps_current_behaviour(self):
        assert _triage(0.9) == PredictionStatus.AUTO_ACCEPTED
        assert _triage(0.85) == PredictionStatus.NEEDS_HUMAN_REVIEW  # stricte
        assert _triage(0.15) == PredictionStatus.NEEDS_HUMAN_REVIEW  # stricte
        assert _triage(0.1) == PredictionStatus.AUTO_REJECTED
        assert _triage(None) == PredictionStatus.NEEDS_HUMAN_REVIEW
        assert _triage("n/a") == PredictionStatus.NEEDS_HUMAN_REVIEW

    def test_accept_preset_boundaries(self):
        for accept, at, above in (
            (0.7, 0.7, 0.75),
            (0.85, 0.85, 0.9),
            (0.9, 0.9, 0.95),
        ):
            policy = {"accept": accept, "reject": None}
            assert _triage(at, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW
            assert _triage(above, policy) == PredictionStatus.AUTO_ACCEPTED

    def test_accept_null_never_auto_accepted(self):
        policy = {"accept": None, "reject": None}
        assert _triage(0.99, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW
        assert _triage(1.0, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW

    def test_reject_preset_boundaries(self):
        for reject, at, below in (
            (0.1, 0.1, 0.05),
            (0.15, 0.15, 0.1),
            (0.25, 0.25, 0.2),
        ):
            policy = {"accept": None, "reject": reject}
            assert _triage(at, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW
            assert _triage(below, policy) == PredictionStatus.AUTO_REJECTED

    def test_reject_null_never_auto_rejected(self):
        policy = {"accept": None, "reject": None}
        assert _triage(0.0, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW

    def test_families_independent(self):
        policy = {"accept": 0.7, "reject": 0.25}
        assert _triage(0.9, policy) == PredictionStatus.AUTO_ACCEPTED
        assert _triage(0.1, policy) == PredictionStatus.AUTO_REJECTED
        assert _triage(0.4, policy) == PredictionStatus.NEEDS_HUMAN_REVIEW


class TestNormalizeTriagePolicy:
    def test_none_is_default_policy(self):
        assert normalize_triage_policy(None) is None

    def test_full_policy(self):
        assert normalize_triage_policy({"accept": 0.7, "reject": 0.25}) == {
            "accept": 0.7,
            "reject": 0.25,
        }

    def test_null_families_disabled(self):
        assert normalize_triage_policy({"accept": None, "reject": None}) == {
            "accept": None,
            "reject": None,
        }

    def test_missing_key_is_disabled_family(self):
        assert normalize_triage_policy({"accept": 0.9}) == {"accept": 0.9, "reject": None}

    @pytest.mark.parametrize(
        "bad",
        [
            {"accept": 0.5},
            {"accept": 0.15},
            {"reject": 0.5},
            {"reject": 0.9},
            {"accept": "0.85"},
            {"reject": [0.15]},
            "0.85",
        ],
    )
    def test_out_of_preset_rejected(self, bad):
        with pytest.raises(ValueError):
            normalize_triage_policy(bad)


class TestFieldConfidences:
    def test_extracts_and_clamps(self):
        prediction = {
            "field_confidence": {"f1": 0.42, "f2": 1.7, "f3": -0.2},
        }
        assert _field_confidences(prediction) == {"f1": 0.42, "f2": 1.0, "f3": 0.0}

    def test_invalid_values_dropped(self):
        assert _field_confidences({"field_confidence": {"f1": 0.4, "f2": "high", "f3": [0.5]}}) == {
            "f1": 0.4
        }

    def test_no_usable_confidence_is_none(self):
        assert _field_confidences({"field_confidence": {"f1": "high"}}) is None
        assert _field_confidences({"field_confidence": {}}) is None
        assert _field_confidences({"f1": "x"}) is None
        assert _field_confidences(None) is None
