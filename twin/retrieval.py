"""Pick the past decisions most similar to a new question (BM25 over a few dozen rows).

Small and dependency-free on purpose: the profile has tens of decisions, not millions.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from .schema import PastDecision

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "should",
        "the",
        "this",
        "to",
        "use",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "we",
        "our",
    ]
)


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def _doc(d: PastDecision) -> list[str]:
    return _tokens(" ".join([d.domain, d.question, d.why, *(o.label for o in d.options)]))


class Retriever:
    def __init__(self, decisions: Sequence[PastDecision], k1: float = 1.4, b: float = 0.75):
        self._decisions = tuple(decisions)
        self._docs = [Counter(_doc(d)) for d in self._decisions]
        self._lens = [sum(c.values()) for c in self._docs]
        self._avg = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        df: Counter[str] = Counter()
        for c in self._docs:
            df.update(c.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._k1, self._b = k1, b

    def top(self, text: str, k: int = 4) -> list[PastDecision]:
        query = set(_tokens(text))
        if not query or not self._docs:
            return list(self._decisions[:k])
        scored = []
        for i, c in enumerate(self._docs):
            s = 0.0
            for t in query:
                f = c.get(t, 0)
                if f:
                    norm = self._k1 * (1 - self._b + self._b * self._lens[i] / self._avg)
                    s += self._idf[t] * f * (self._k1 + 1) / (f + norm)
            scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self._decisions[i] for _, i in scored[:k]]
