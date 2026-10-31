"""Model back-ends for the twin. Any OpenAI-compatible endpoint works.

Two capabilities matter:
  * chat():         free-form completion, used for per-principle scoring (JSON).
  * letter_probs(): the model's own probability for each option letter, read from token
                    log-probabilities. Hosted reasoning APIs usually refuse logprobs; a
                    self-hosted open-weight model (Muse Glimmer on vLLM) exposes them.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

Message = dict[str, str]


class Brain(Protocol):
    name: str
    supports_logprobs: bool

    def chat(self, messages: Sequence[Message], max_tokens: int = 4000) -> str: ...

    def letter_probs(self, messages: Sequence[Message], letters: Sequence[str]) -> dict[str, float] | None: ...


@dataclass
class OpenAICompatBrain:
    """A brain behind any OpenAI-compatible chat endpoint."""

    name: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    supports_logprobs: bool = False
    timeout_s: float = 120.0
    chat_extra: dict[str, Any] = field(default_factory=dict)
    probe_extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout_s, max_retries=2)

    def chat(self, messages: Sequence[Message], max_tokens: int = 4000) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=max_tokens,
            **self.chat_extra,
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError(f"{self.name}: empty reply (finish_reason={resp.choices[0].finish_reason})")
        return content

    def letter_probs(self, messages: Sequence[Message], letters: Sequence[str]) -> dict[str, float] | None:
        if not self.supports_logprobs:
            return None
        # Reasoning models think first, then answer. We let it think and read the probability
        # distribution at the position where it writes the option letter.
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=2000,
            logprobs=True,
            top_logprobs=20,
            **self.probe_extra,
        )
        lp = resp.choices[0].logprobs
        if lp is None or not lp.content:
            return None
        wanted = {x.upper() for x in letters}
        for tok in reversed(lp.content):
            if tok.token.strip().rstrip(".):").upper() in wanted:
                return letters_from_top_logprobs([(t.token, t.logprob) for t in tok.top_logprobs], letters)
        return None


def letters_from_top_logprobs(top: Sequence[tuple[str, float]], letters: Sequence[str]) -> dict[str, float] | None:
    """Sum probability mass per option letter across token spellings (\"B\", \" B\", \"B.\")."""
    wanted = set(letters)
    mass = dict.fromkeys(letters, 0.0)
    for token, logprob in top:
        key = token.strip().rstrip(".):").upper()
        if key in wanted:
            mass[key] += math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in mass.items()}


def spark_from_env(model: str = "muse-spark-1.3") -> OpenAICompatBrain:
    """Muse Spark via Meta Model API (Standard tier: prompts are not used for training)."""
    return OpenAICompatBrain(
        name=f"Meta {model} (hosted API)",
        model=model,
        base_url=os.environ.get("META_BASE_URL", "https://api.meta.ai/v1"),
        api_key=os.environ["META_API_KEY"],
        supports_logprobs=False,
    )


def glimmer_from_env() -> OpenAICompatBrain:
    """Muse Glimmer (open weights) served by our own vLLM endpoint on Modal."""
    return OpenAICompatBrain(
        name="Meta Muse Glimmer 30B (open weights, self-hosted)",
        model=os.environ.get("GLIMMER_MODEL", "muse-glimmer"),
        base_url=os.environ["GLIMMER_BASE_URL"],
        api_key=os.environ["GLIMMER_API_KEY"],
        supports_logprobs=True,
        timeout_s=900.0,  # first request may wait for a cold start (about 3 minutes)
        probe_extra={"extra_body": {"chat_template_kwargs": {"reasoning_strength": "low"}}},
    )
