"""Run events: an append-only log per run that the UI reads.

The store is a small interface so the loop can be tested with an in-memory store and run
in production on a Modal Dict.
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping
from typing import Any, Literal

EventType = Literal[
    "run_started",
    "worker_turn",
    "decision",
    "verification",
    "guardian",
    "note",
    "error",
    "run_finished",
]


class EventLog:
    """Events stored as `<run>:<seq>` keys plus a `<run>:n` counter. One writer per run."""

    def __init__(self, store: MutableMapping[str, Any], run_id: str):
        self._store, self.run_id = store, run_id
        self._n = int(store.get(f"{run_id}:n", 0))

    def emit(self, type_: EventType, **data: Any) -> dict:
        event = {"seq": self._n, "type": type_, "ts": time.time(), **data}
        self._store[f"{self.run_id}:{self._n}"] = event
        self._n += 1
        self._store[f"{self.run_id}:n"] = self._n
        return event


def read_events(store: MutableMapping[str, Any], run_id: str, since: int = 0, limit: int = 200) -> list[dict]:
    n = int(store.get(f"{run_id}:n", 0))
    end = min(n, since + limit)
    return [store[f"{run_id}:{i}"] for i in range(max(0, since), end)]
