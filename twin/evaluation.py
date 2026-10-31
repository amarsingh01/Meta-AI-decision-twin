"""Agreement score: does the twin choose what the persona chose on decisions it has never seen?

The held-out synthetic decisions are never used as retrieval examples. For each one the twin
decides, and we compare with the recorded choice.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from .brain import Brain
from .engine import DecisionTwin
from .profile import load_decisions, split
from .schema import Question


def evaluate(brain: Brain) -> dict:
    twin = DecisionTwin(brain)
    holdout = split(load_decisions(), "holdout")

    def one(d):
        q = Question(id=d.id, question=d.question, options=d.options)
        try:
            out = twin.decide(q)
        except Exception as exc:
            return {"id": d.id, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        by = {o.id: o for o in out.options}
        return {
            "id": d.id,
            "question": d.question,
            "truth": d.chosen,
            "chosen": out.chosen,
            "agree": out.chosen == d.chosen,
            "p_truth": by[d.chosen].p_final,
            "p_model_truth": by[d.chosen].p_model,
            "needs_human": out.needs_human,
        }

    started = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, holdout))
    scored = [r for r in rows if "error" not in r]
    n = len(scored)
    model_p = [r["p_model_truth"] for r in scored if r["p_model_truth"] is not None]
    return {
        "brain": twin.brain.name,
        "n": n,
        "errors": len(rows) - n,
        "agreement": sum(r["agree"] for r in scored) / n if n else None,
        "mean_p_truth": sum(r["p_truth"] for r in scored) / n if n else None,
        "mean_model_p_truth": sum(model_p) / len(model_p) if model_p else None,
        "flagged_for_human": sum(r["needs_human"] for r in scored),
        "seconds": round(time.time() - started),
        "ts": time.time(),
        "rows": rows,
    }
