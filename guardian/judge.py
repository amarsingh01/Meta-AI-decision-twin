"""The guardian: hard rules first, then the decision twin for anything that depends on context.

check(task, action, recent) -> GuardResult with the final decision, which layer made it,
and (for model reviews) the full scored Decision for the UI.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from twin.brain import Brain
from twin.engine import ScoringError
from twin.profile import PROFILE_DIR, load_decisions, load_profile, split
from twin.schema import Decision, Option

from .assess import ActionAssessor
from .policy import Action, Verdict
from .policy import check as rule_check

GUARDIAN_PROFILE = PROFILE_DIR / "guardian.json"
GUARDIAN_DECISIONS = PROFILE_DIR / "guardian_decisions.jsonl"

OPTIONS = (
    Option(id="A", label="Allow it"),
    Option(id="B", label="Pause and ask the owner"),
    Option(id="C", label="Block it"),
)
_FROM_CHOICE = {"A": "allow", "B": "ask", "C": "block"}


@dataclass(frozen=True)
class GuardResult:
    decision: str  # allow | block | ask
    layer: str  # rule | model | fallback
    reason: str
    rule: str | None = None
    confidence: float | None = None
    scored: Decision | None = None
    latency_ms: int = 0
    action: str = ""
    extra: dict = field(default_factory=dict)

    def as_event(self) -> dict:
        return {
            "decision": self.decision,
            "layer": self.layer,
            "reason": self.reason,
            "rule": self.rule,
            "confidence": self.confidence,
            "action": self.action,
            "latency_ms": self.latency_ms,
            "scored": self.scored.model_dump(mode="json") if self.scored else None,
            **self.extra,
        }


class Guardian:
    def __init__(self, brain: Brain | None, n_examples: int = 4, extra_hosts: frozenset[str] = frozenset()):
        self.extra_hosts = frozenset(h.lower() for h in extra_hosts)
        self.profile = load_profile(Path(GUARDIAN_PROFILE))
        examples = split(load_decisions(Path(GUARDIAN_DECISIONS)), "train")
        self._assessor = ActionAssessor(brain, self.profile, examples, n_examples) if brain else None
        self.judge_name = brain.name if brain else None

    def check(self, task: str, action: Action, recent: Sequence[str] = ()) -> GuardResult:
        started = time.monotonic()
        verdict: Verdict = rule_check(action, self.extra_hosts)
        if verdict.decision != "review":
            return GuardResult(
                decision=verdict.decision,
                layer="rule",
                reason=verdict.reason,
                rule=verdict.rule,
                confidence=1.0,
                action=action.describe(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        if self._assessor is None:
            return GuardResult(
                "ask",
                "fallback",
                f"{verdict.reason}; no model available, so the owner decides",
                action=action.describe(),
            )
        try:
            a = self._assessor.assess(task, action.describe(), recent, verdict.reason)
        except ScoringError as exc:
            return GuardResult(
                "ask",
                "fallback",
                f"Model review failed ({exc}); the owner decides",
                action=action.describe(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        reason = a.rationale or verdict.reason
        if a.vetoes:
            reason = f"{reason} ({a.vetoes[0]})"
        return GuardResult(
            decision=a.decision,
            layer="model",
            reason=reason,
            confidence=a.confidence,
            action=action.describe(),
            latency_ms=int((time.monotonic() - started) * 1000),
            extra={"assessment": a.as_dict(), "judge": self.judge_name},
        )
