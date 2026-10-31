import math

import pytest

from twin import scoring
from twin.profile import load_profile


@pytest.fixture(scope="module")
def principles():
    return load_profile().principles


def test_weights_sum_to_one(principles):
    assert math.isclose(sum(p.weight for p in principles), 1.0)


@pytest.mark.parametrize(("raw", "want"), [(-3, 0), (4.4, 4), (4.6, 5), (12, 10), (float("nan"), 0)])
def test_clamp(raw, want):
    assert scoring.clamp_score(raw) == want


def test_weighted_score_bounds(principles):
    assert scoring.weighted_score({p.id: 10 for p in principles}, principles) == pytest.approx(1.0)
    assert scoring.weighted_score({}, principles) == 0.0


def test_security_weighs_most(principles):
    only_security = {"security": 10}
    only_clarity = {"clarity": 10}
    assert scoring.weighted_score(only_security, principles) > scoring.weighted_score(only_clarity, principles)


def test_veto_on_low_security(principles):
    scores = {p.id: 9 for p in principles} | {"security": 1}
    assert "Security first" in scoring.veto_reason(scores, principles, False, None)


def test_hard_rule_beats_scores(principles):
    perfect = {p.id: 10 for p in principles}
    assert scoring.veto_reason(perfect, principles, True, "Scrapes a site that forbids it") == (
        "Scrapes a site that forbids it"
    )
    assert scoring.veto_reason(perfect, principles, False, None) is None


def test_softmax_sums_to_one_and_orders():
    p = scoring.softmax({"A": 0.9, "B": 0.6, "C": 0.3}, 0.1)
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["A"] > p["B"] > p["C"]


def test_softmax_is_stable_for_large_values():
    p = scoring.softmax({"A": 1000.0, "B": 999.0}, 0.01)
    assert sum(p.values()) == pytest.approx(1.0)


def test_softmax_rejects_bad_temperature():
    with pytest.raises(ValueError):
        scoring.softmax({"A": 1.0}, 0)


def test_normalise_drops_vetoed():
    assert scoring.normalise({"A": 0.5, "B": 0.3, "C": 0.2}, ["A", "C"]) == pytest.approx({"A": 5 / 7, "C": 2 / 7})
    assert scoring.normalise({"A": 0.0}, ["A"]) is None


def test_blend():
    assert scoring.blend({"A": 1.0, "B": 0.0}, None, 0.7) == {"A": 1.0, "B": 0.0}
    mixed = scoring.blend({"A": 1.0, "B": 0.0}, {"A": 0.0, "B": 1.0}, 0.7)
    assert mixed == pytest.approx({"A": 0.7, "B": 0.3})


def test_escalation():
    assert scoring.escalation({}, 0.45, 0.08) == "Every option was vetoed"
    assert "only" in scoring.escalation({"A": 0.4, "B": 0.35, "C": 0.25}, 0.45, 0.08)
    assert "within" in scoring.escalation({"A": 0.52, "B": 0.48}, 0.45, 0.08)
    assert scoring.escalation({"A": 0.8, "B": 0.2}, 0.45, 0.08) is None
