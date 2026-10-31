"""Load and validate the persona: weighted principles and past decisions."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .schema import PastDecision, Profile

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profile"


@lru_cache(maxsize=4)
def load_profile(path: Path = PROFILE_DIR / "principles.json") -> Profile:
    return Profile.model_validate_json(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load_decisions(path: Path = PROFILE_DIR / "decisions.jsonl") -> tuple[PastDecision, ...]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(PastDecision.model_validate(json.loads(line)))
            except Exception as exc:  # re-raise with the line number for a clear error
                raise ValueError(f"{path.name} line {n}: {exc}") from exc
    return tuple(rows)


def split(decisions: tuple[PastDecision, ...], name: str) -> tuple[PastDecision, ...]:
    return tuple(d for d in decisions if d.split == name)
