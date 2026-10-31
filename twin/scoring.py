"""Pure scoring maths: weighted principle scores, vetoes, probabilities.

No I/O and no model calls, so every rule here is unit-tested directly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .schema import Principle


def clamp_score(value: float) -> int:
    """Model scores are 0..10 integers; anything else is clamped and rounded."""
    if not math.isfinite(value):
        return 0
    return round(min(10.0, max(0.0, value)))


def weighted_score(scores: Mapping[str, int], principles: Sequence[Principle]) -> float:
    """Weighted mean of 0..10 scores, scaled to 0..1. A missing principle counts as 0."""
    return sum(p.weight * scores.get(p.id, 0) for p in principles) / 10.0


def veto_reason(
    scores: Mapping[str, int],
    principles: Sequence[Principle],
    violates_hard_rule: bool,
    model_reason: str | None,
) -> str | None:
    """Return why an option is vetoed, or None. Hard rules beat any score."""
    if violates_hard_rule:
        return model_reason or "Breaks a hard rule"
    for p in principles:
        if p.veto_below is not None and scores.get(p.id, 0) < p.veto_below:
            return f"{p.name} score {scores.get(p.id, 0)}/10 is below the veto line ({p.veto_below})"
    return None


def softmax(values: Mapping[str, float], temperature: float) -> dict[str, float]:
    """Softmax over the given keys. Empty input gives an empty result."""
    if not values:
        return {}
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    peak = max(values.values())
    exps = {k: math.exp((v - peak) / temperature) for k, v in values.items()}
    total = sum(exps.values())
    return {k: e / total for k, e in exps.items()}


def normalise(probs: Mapping[str, float], keep: Sequence[str]) -> dict[str, float] | None:
    """Restrict a distribution to `keep` and renormalise. None if nothing is left."""
    kept = {k: max(0.0, probs.get(k, 0.0)) for k in keep}
    total = sum(kept.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in kept.items()}


def blend(
    p_principles: Mapping[str, float],
    p_model: Mapping[str, float] | None,
    alpha: float,
) -> dict[str, float]:
    """alpha * principles + (1 - alpha) * model. Falls back to principles alone."""
    if p_model is None:
        return dict(p_principles)
    keys = list(p_principles)
    mixed = {k: alpha * p_principles[k] + (1 - alpha) * p_model.get(k, 0.0) for k in keys}
    total = sum(mixed.values())
    return {k: v / total for k, v in mixed.items()} if total > 0 else dict(p_principles)


def escalation(
    final: Mapping[str, float],
    top_below: float,
    margin_below: float,
) -> str | None:
    """Return why a human should decide, or None when the twin is confident enough."""
    if not final:
        return "Every option was vetoed"
    ranked = sorted(final.values(), reverse=True)
    if ranked[0] < top_below:
        return f"Top option is only {ranked[0]:.0%} likely (needs {top_below:.0%})"
    if len(ranked) > 1 and ranked[0] - ranked[1] < margin_below:
        return f"Top two options are within {ranked[0] - ranked[1]:.0%} of each other"
    return None
