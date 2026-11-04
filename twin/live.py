"""One live decision for the dashboard: draft options if none are given, then let a persona's twin decide.

Progress is written to a store entry so the page can follow along:
  {"stage": "drafting" | "scoring" | "done" | "error", ...}
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping
from typing import Any

from .brain import Brain
from .engine import DecisionTwin, _extract_json, safe_id
from .personas import load_personas
from .schema import OPTION_IDS, Option, Question


def draft_options(brain: Brain, question: str, context: str) -> list[str]:
    prompt = (
        "Propose exactly 3 genuinely different, realistic options for this decision in a small web project. "
        "Each option is a short label (at most 12 words). Make them differ in approach, not just wording. "
        'Reply with JSON only: {"options": ["...", "...", "..."]}\n\n'
        f"Decision: {question}\nContext: {context or 'none'}"
    )
    data = _extract_json(brain.chat([{"role": "user", "content": prompt}], max_tokens=8000))
    labels = [str(x).strip()[:120] for x in data.get("options", []) if str(x).strip()]
    if len(labels) < 2:
        raise ValueError("the model did not propose usable options")
    return labels[:3]


def run_live_decision(
    store: MutableMapping[str, Any],
    key: str,
    brain: Brain,
    persona: str,
    question: str,
    context: str = "",
    options: list[str] | None = None,
    drafted_by: str | None = None,
) -> dict:
    who = load_personas()[persona]
    state: dict[str, Any] = {"persona": who.summary(), "question": question, "context": context, "started": time.time()}

    def put(**kw: Any) -> None:
        state.update(kw, updated=time.time())
        store[key] = dict(state)

    if drafted_by:
        state["drafted_by"] = drafted_by
    try:
        if not options:
            put(stage="drafting")
            options = draft_options(brain, question, context)
            put(drafted_by=brain.name)
        q = Question(
            id=safe_id(f"live-{int(time.time() * 1000)}"),
            question=question,
            context=context or "A decision in a small web project.",
            options=tuple(Option(id=OPTION_IDS[i], label=label) for i, label in enumerate(options[:4])),
        )
        put(stage="scoring", options=[o.model_dump() for o in q.options])
        decision = DecisionTwin(brain, who.profile, examples=()).decide(q)
        put(
            stage="done", decision=decision.model_dump(mode="json"), settings=who.profile.decision_settings.model_dump()
        )
    except Exception as exc:  # shown on the page
        put(stage="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
    return state


def run_live_group(
    store: MutableMapping[str, Any],
    keys: list[str],
    brain: Brain,
    personas: list[str],
    question: str,
    context: str = "",
    options: list[str] | None = None,
) -> None:
    """Same question, same options for every owner, so differences come from their principles alone."""
    from concurrent.futures import ThreadPoolExecutor

    drafted_by = None
    if not options:
        for key in keys:
            store[key] = {"stage": "drafting", "started": time.time()}
        try:
            options, drafted_by = draft_options(brain, question, context), brain.name
        except Exception as exc:
            for key in keys:
                store[key] = {"stage": "error", "error": f"Could not draft options: {exc}"[:300]}
            return
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        list(
            pool.map(
                lambda kp: run_live_decision(store, kp[0], brain, kp[1], question, context, options, drafted_by),
                zip(keys, personas, strict=True),
            )
        )
