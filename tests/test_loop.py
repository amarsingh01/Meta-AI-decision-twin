import json

from orchestrator.events import EventLog, read_events
from orchestrator.loop import Limits, run_build
from twin import DecisionTwin
from twin.profile import load_profile
from worker.protocol import DONE_CLOSE, DONE_OPEN, REQ_CLOSE, REQ_OPEN, parse_worker_reply
from worker.sandbox import TurnResult

PRINCIPLES = [p.id for p in load_profile().principles]


def _scores(**per_option):
    return json.dumps(
        {
            "options": [
                {"id": k, "scores": dict.fromkeys(PRINCIPLES, v), "violates_hard_rule": False, "rationale": k}
                for k, v in per_option.items()
            ]
        }
    )


class Brain:
    name, supports_logprobs = "fake", False

    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, messages, max_tokens=4000):
        return self.replies.pop(0)

    def letter_probs(self, messages, letters):
        return None


class FakeWorker:
    def __init__(self, replies, test_rc=0):
        self.replies, self.sent, self.test_rc = list(replies), [], test_rc

    def send(self, message, timeout_s=900):
        self.sent.append(message)
        return TurnResult(text=self.replies.pop(0), ok=True, error=None, duration_ms=1, files_changed="")

    def run(self, cmd, timeout=600):
        return self.test_rc, "3 passed"

    def usage(self):
        return {"prompt_tokens": 1, "completion_tokens": 1}


ASK_JSON = '{"question": "Which data source?", "options": [{"label": "scrape"}, {"label": "api"}]}'
ASK = f"{REQ_OPEN}{ASK_JSON}{REQ_CLOSE}"
DONE = f'{DONE_OPEN}{{"summary": "site", "run": "python app.py", "test": "pytest -q"}}{DONE_CLOSE}'


def test_full_loop_decides_builds_and_accepts():
    store = {}
    worker = FakeWorker(["thinking...", ASK, "built", DONE])
    twin = DecisionTwin(Brain([_scores(A=2, B=9), _scores(A=9, B=4, C=4)]))
    out = run_build("Build a stock site", worker, twin, EventLog(store, "r1"))
    assert out["finished"] and out["outcome"] == "accepted" and out["decisions"] == 2
    types = [e["type"] for e in read_events(store, "r1")]
    assert types.count("decision") == 2 and "verification" in types and types[-1] == "run_finished"
    assert worker.sent[2].startswith("Decision: option B")


def test_failing_tests_are_sent_back_then_give_up():
    worker = FakeWorker([DONE] * 5, test_rc=1)
    out = run_build("x", worker, DecisionTwin(Brain([])), EventLog({}, "r2"), Limits(max_fix_rounds=2))
    assert out["outcome"] == "tests_failing"
    assert "do not pass" in worker.sent[1]


def test_decision_budget():
    worker = FakeWorker([ASK, ASK, DONE])
    twin = DecisionTwin(Brain([_scores(A=2, B=9), _scores(A=9, B=1, C=1)]))
    out = run_build("x", worker, twin, EventLog({}, "r3"), Limits(max_decisions=1))
    assert "decision budget" in worker.sent[2].lower()
    assert out["finished"]


def test_parse_worker_reply_handles_junk_and_relabels():
    assert parse_worker_reply("no block here", 1).question is None
    bad = parse_worker_reply(f"{REQ_OPEN}not json{REQ_CLOSE}", 1)
    assert bad.error
    p = parse_worker_reply(
        REQ_OPEN + '{"question": "Pick one?", "options": [{"id": "Z", "label": "x"}, {"label": "y"}]}' + REQ_CLOSE, 3
    )
    assert [o.id for o in p.question.options] == ["A", "B"] and p.question.id == "turn-3"


def test_events_paging():
    store = {}
    log = EventLog(store, "r")
    for i in range(5):
        log.emit("note", i=i)
    assert [e["i"] for e in read_events(store, "r", since=3)] == [3, 4]
    assert EventLog(store, "r").emit("note")["seq"] == 5


def test_review_failure_does_not_crash_the_run():
    worker = FakeWorker([DONE])
    out = run_build("x", worker, DecisionTwin(Brain(["junk", "junk"])), EventLog({}, "r4"))
    assert out["finished"] and out["outcome"] == "accepted_on_tests"
