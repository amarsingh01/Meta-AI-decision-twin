"""The contract between the decision twin and the OpenClaw worker.

The worker is told to stop at every real decision point and emit a DECISION_REQUEST block,
and to emit a DONE block when the job is finished. Its free text is untrusted: we only take
data out of these blocks, validate it, and ignore everything else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from twin.schema import OPTION_IDS, Option, Question

REQ_OPEN, REQ_CLOSE = "<<<DECISION_REQUEST>>>", "<<<END_DECISION_REQUEST>>>"
DONE_OPEN, DONE_CLOSE = "<<<DONE>>>", "<<<END_DONE>>>"

_REQ = re.compile(re.escape(REQ_OPEN) + r"(.*?)" + re.escape(REQ_CLOSE), re.DOTALL)
_DONE = re.compile(re.escape(DONE_OPEN) + r"(.*?)" + re.escape(DONE_CLOSE), re.DOTALL)

WORKER_RULES = f"""\
You are the builder. A separate decision-maker (the owner's decision twin) makes every
important choice for you. Follow these rules for this whole session:

1. Work only inside /work/site. Never touch files outside it. Never print or look for secrets.
2. Whenever you reach a real decision (stack, library, data source, architecture, security
   approach, UI layout, what to do about a failing test, whether something is finished),
   STOP before acting and end your reply with exactly one block:
{REQ_OPEN}
{{"question": "one clear question", "context": "facts the decider needs, max 3 sentences",
  "options": [{{"id": "A", "label": "short option"}}, {{"id": "B", "label": "short option"}}]}}
{REQ_CLOSE}
   Give 2 to 4 genuinely different options with ids A, B, C, D. Then wait for the decision.
   A build like this normally has 4 to 8 such decisions. Typical ones: stack, data sources,
   how data is fetched and cached, how untrusted input is validated, page layout and style,
   how freshness and uncertainty are shown, testing approach, and whether the work is finished.
   The owner's requirements in the task are firm: make choices that satisfy them.
3. Small mechanical steps (creating a folder, fixing a typo, running a command) need no question.
4. When the decision arrives, carry it out exactly, then continue to the next step.
5. Write tests and run them. Never claim something works without running it.
6. When the whole job is finished and the tests pass, end your reply with:
{DONE_OPEN}
{{"summary": "what was built", "run": "command that starts it", "test": "command that runs the tests"}}
{DONE_CLOSE}
"""


@dataclass(frozen=True)
class Done:
    summary: str
    run: str
    test: str


@dataclass(frozen=True)
class Parsed:
    question: Question | None
    done: Done | None
    error: str | None


def _last_json(pattern: re.Pattern[str], text: str) -> dict | None:
    matches = pattern.findall(text)
    if not matches:
        return None
    raw = matches[-1].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("block has no JSON object")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("block is not a JSON object")
    return data


def parse_worker_reply(text: str, turn: int) -> Parsed:
    """Extract a validated question or a done signal from the worker's reply."""
    try:
        req = _last_json(_REQ, text)
        if req is not None:
            raw_opts = req.get("options") or []
            options = tuple(
                Option(id=OPTION_IDS[i], label=str(o.get("label", "")).strip()[:600])
                for i, o in enumerate(raw_opts[: len(OPTION_IDS)])
                if isinstance(o, dict)
            )
            q = Question(
                id=f"turn-{turn}",
                question=str(req.get("question", "")).strip()[:600],
                context=str(req.get("context", "")).strip()[:2400],
                options=options,
            )
            return Parsed(question=q, done=None, error=None)
        done = _last_json(_DONE, text)
        if done is not None:
            return Parsed(
                question=None,
                done=Done(
                    summary=str(done.get("summary", ""))[:1000],
                    run=str(done.get("run", ""))[:300],
                    test=str(done.get("test", ""))[:300],
                ),
                error=None,
            )
    except (ValueError, TypeError) as exc:
        return Parsed(question=None, done=None, error=f"Malformed block: {str(exc)[:200]}")
    return Parsed(question=None, done=None, error=None)
