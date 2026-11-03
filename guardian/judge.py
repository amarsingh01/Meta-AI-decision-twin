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
from twin.engine import DecisionTwin, ScoringError, safe_id
from twin.profile import PROFILE_DIR, load_decisions, load_profile, split
from twin.schema import Decision, Option, Question

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
        }


class Guardian:
    def __init__(self, brain: Brain | None, n_examples: int = 4):
        self.profile = load_profile(Path(GUARDIAN_PROFILE))
        examples = split(load_decisions(Path(GUARDIAN_DECISIONS)), "train")
        self._twin = DecisionTwin(brain, self.profile, examples, n_examples) if brain else None

    def check(self, task: str, action: Action, recent: Sequence[str] = ()) -> GuardResult:
        started = time.monotonic()
        verdict: Verdict = rule_check(action)
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
        if self._twin is None:
            return GuardResult(
                "ask",
                "fallback",
                f"{verdict.reason}; no model available, so the owner decides",
                action=action.describe(),
            )
        history = "; ".join(recent[-6:]) or "none yet"
        q = Question(
            id=safe_id(f"guard-{int(time.time() * 1000)}"),
            question=f"The agent wants to {action.describe()}. What should the guardian do?",
            context=(
                f"Task the owner gave: {task}\nRecent agent actions: {history}\n"
                f"Why the rules sent this for review: {verdict.reason}"
            )[:2400],
            options=OPTIONS,
        )
        try:
            d = self._twin.decide(q)
        except ScoringError as exc:
            return GuardResult(
                "ask",
                "fallback",
                f"Model review failed ({exc}); the owner decides",
                action=action.describe(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        decision = _FROM_CHOICE.get(d.chosen or "", "ask")
        if d.needs_human and decision == "allow":
            decision = "ask"  # never allow on a close call
        chosen = next((o for o in d.options if o.id == d.chosen), None)
        return GuardResult(
            decision=decision,
            layer="model",
            reason=(chosen.rationale if chosen else d.escalation_reason) or verdict.reason,
            confidence=d.confidence,
            scored=d,
            action=action.describe(),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
