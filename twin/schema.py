"""Typed data contracts for the decision twin.

Everything that crosses a boundary (profile files, questions from the worker agent,
model output) is parsed into these models, so bad data fails loudly at the edge.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OPTION_IDS = ("A", "B", "C", "D")
MAX_TEXT = 600  # characters per free-text field coming from outside


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- profile


class Principle(_Frozen):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    name: str
    weight: float = Field(gt=0, le=1)
    description: str
    veto_below: int | None = Field(default=None, ge=0, le=10)


class DecisionSettings(_Frozen):
    temperature: float = Field(default=0.08, gt=0, le=1)
    blend_principles_vs_model: float = Field(default=0.7, ge=0, le=1)
    escalate_if_top_below: float = Field(default=0.45, ge=0, le=1)
    escalate_if_margin_below: float = Field(default=0.08, ge=0, le=1)


class Profile(_Frozen):
    owner: str
    synthetic: bool
    note: str
    version: str
    principles: tuple[Principle, ...]
    hard_rules: tuple[str, ...]
    decision_settings: DecisionSettings = DecisionSettings()

    @model_validator(mode="after")
    def _check(self) -> Profile:
        ids = [p.id for p in self.principles]
        if len(ids) != len(set(ids)):
            raise ValueError("principle ids must be unique")
        total = sum(p.weight for p in self.principles)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"principle weights must sum to 1.0, got {total:.4f}")
        return self


class Option(_Frozen):
    id: str
    label: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("id")
    @classmethod
    def _known_id(cls, v: str) -> str:
        if v not in OPTION_IDS:
            raise ValueError(f"option id must be one of {OPTION_IDS}")
        return v


class PastDecision(_Frozen):
    id: str
    domain: str
    question: str
    options: tuple[Option, ...]
    chosen: str
    why: str
    principles: tuple[str, ...]
    split: str = Field(pattern=r"^(train|holdout)$")
    synthetic: bool = True


# --------------------------------------------------------------------------- questions


class Question(_Frozen):
    """A decision point raised by the worker agent. Its text is untrusted."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    question: str = Field(min_length=3, max_length=MAX_TEXT)
    context: str = Field(default="", max_length=4 * MAX_TEXT)
    options: tuple[Option, ...] = Field(min_length=2, max_length=len(OPTION_IDS))

    @model_validator(mode="after")
    def _unique_options(self) -> Question:
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique")
        return self


# --------------------------------------------------------------------------- model output


class ModelOptionScore(BaseModel):
    """One option as scored by the model. Lenient on input, clamped on use."""

    model_config = ConfigDict(extra="ignore")

    id: str
    scores: dict[str, float]
    violates_hard_rule: bool = False
    veto_reason: str | None = None
    rationale: str | None = ""

    @field_validator("rationale", mode="before")
    @classmethod
    def _text(cls, v: object) -> str:
        return "" if v is None else str(v)


class ModelScoring(BaseModel):
    model_config = ConfigDict(extra="ignore")

    options: list[ModelOptionScore]


# --------------------------------------------------------------------------- results


class OptionResult(_Frozen):
    id: str
    label: str
    scores: dict[str, int]  # principle id -> 0..10
    weighted: float  # 0..1
    vetoed: bool
    veto_reason: str | None
    rationale: str
    p_principles: float
    p_model: float | None
    p_final: float


class Decision(_Frozen):
    question_id: str
    question: str
    options: tuple[OptionResult, ...]
    chosen: str | None
    confidence: float
    needs_human: bool
    escalation_reason: str | None
    reply_to_worker: str
    brain: str
    model_probabilities_available: bool
    examples_used: tuple[str, ...]
    latency_ms: int
