"""The build loop: the twin directs, the OpenClaw worker builds, the twin reviews.

Dependency-injected (worker, twin, event log) so the whole loop is unit-tested with fakes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from twin.engine import ScoringError
from twin.schema import Decision, Option, Question
from worker.protocol import Done, parse_worker_reply

from .events import EventLog


class Worker(Protocol):
    def send(self, message: str, timeout_s: int = 900): ...
    def run(self, cmd: str, timeout: int = 600) -> tuple[int, str]: ...
    def usage(self) -> dict: ...


class Twin(Protocol):
    def decide(self, q: Question) -> Decision: ...


@dataclass(frozen=True)
class Limits:
    max_turns: int = 40
    max_decisions: int = 14
    max_fix_rounds: int = 3


NUDGE = (
    "Continue the job. Stop with a DECISION_REQUEST block at the next real decision, "
    "or end with the DONE block when everything is finished and tested."
)
SELF_DECIDE = (
    "The decision budget for this build is used up. Make remaining small choices yourself, "
    "preferring: security first, legal data use, privacy, low cost, clean tested code, a UI "
    "that explains itself. Finish the job and end with the DONE block."
)


_PROSE_TAIL = re.compile(r"\s+\(.*$|\s*;\s*(curl|echo).*$")


def clean_test_command(raw: str) -> str:
    """Workers sometimes annotate the command ("npm test (6 pass)"); keep the command itself."""
    cmd = _PROSE_TAIL.sub("", (raw or "").strip().splitlines()[0] if raw and raw.strip() else "").strip()
    return cmd or "npm test 2>/dev/null || python3 -m pytest -q"


def _decision_event(d: Decision) -> dict:
    return d.model_dump(mode="json")


def _acceptance_question(done: Done, test_output: str, turn: int) -> Question:
    return Question(
        id=f"turn-{turn}-review",
        question="The worker says the build is finished and its tests pass. Accept it?",
        context=f"Worker summary: {done.summary}\nTest output (last lines): {test_output[-900:]}",
        options=(
            Option(id="A", label="Accept the build as finished"),
            Option(id="B", label="Ask for one more round: stronger tests and input validation"),
            Option(id="C", label="Ask for one more round: clearer UI explanations and disclaimers"),
        ),
    )


DEFAULT_LIMITS = Limits()


def run_build(task: str, worker: Worker, twin: Twin, log: EventLog, limits: Limits = DEFAULT_LIMITS) -> dict:
    """Drive one build to completion. Returns a summary; every step is also in the event log."""
    decisions = fix_rounds = 0
    message = f"TASK FROM THE OWNER:\n{task}\n\nRead AGENTS.md for how we work, then start."
    finished, outcome, run_cmd = False, "stopped", None

    for turn in range(1, limits.max_turns + 1):
        result = worker.send(message)
        for ev in getattr(worker, "guardian_events", list)():
            log.emit("guardian", turn=turn, **{k: v for k, v in ev.items() if k != "ts"}, at=ev.get("ts"))
        log.emit(
            "worker_turn",
            turn=turn,
            ok=result.ok,
            error=result.error,
            text=result.text[-3000:],
            files_changed=result.files_changed,
            duration_ms=result.duration_ms,
            usage=worker.usage(),
        )
        if not result.ok:
            log.emit("error", turn=turn, message=f"Worker turn failed: {result.error}")
            message = NUDGE
            continue

        parsed = parse_worker_reply(result.text, turn)
        if parsed.error:
            log.emit("note", turn=turn, message=parsed.error)
            message = f"{parsed.error}. Resend the block with valid JSON."
            continue

        if parsed.question:
            if decisions >= limits.max_decisions:
                log.emit("note", turn=turn, message="Decision budget used up; worker decides the rest")
                message = SELF_DECIDE
                continue
            try:
                decision = twin.decide(parsed.question)
            except ScoringError as exc:
                log.emit("error", turn=turn, message=f"Twin could not score the options: {exc}")
                message = "Rephrase that decision with 2 to 4 short, clearly different options."
                continue
            decisions += 1
            log.emit("decision", turn=turn, decision=_decision_event(decision))
            message = decision.reply_to_worker + "\nCarry this out, then continue."
            continue

        if parsed.done:
            test_cmd = clean_test_command(parsed.done.test)
            rc, out = worker.run(test_cmd, timeout=600)
            log.emit("verification", turn=turn, command=test_cmd, passed=rc == 0, output=out[-3000:])
            if rc != 0:
                fix_rounds += 1
                if fix_rounds > limits.max_fix_rounds:
                    outcome = "tests_failing"
                    break
                message = f"The tests do not pass (exit code {rc}). Output:\n{out[-2500:]}\nFix it, then DONE again."
                continue
            try:
                review = twin.decide(_acceptance_question(parsed.done, out, turn))
            except ScoringError as exc:
                log.emit(
                    "error", turn=turn, message=f"Twin could not review the build: {exc}; accepting on green tests"
                )
                finished, outcome, run_cmd = True, "accepted_on_tests", parsed.done.run
                break
            decisions += 1
            log.emit("decision", turn=turn, decision=_decision_event(review))
            if review.chosen == "A" or fix_rounds >= limits.max_fix_rounds:
                finished, outcome, run_cmd = True, "accepted", parsed.done.run
                log.emit("note", turn=turn, message="Build accepted", run=parsed.done.run, summary=parsed.done.summary)
                break
            fix_rounds += 1
            message = review.reply_to_worker + "\nDo that round now, rerun the tests, then DONE again."
            continue

        message = NUDGE

    usage = worker.usage()
    summary = {"finished": finished, "outcome": outcome, "decisions": decisions, "usage": usage}
    log.emit("run_finished", **summary)
    return {**summary, "run": run_cmd}
