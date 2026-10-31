"""Modal app: the build job and the demo web UI.

modal run app.py --task "..."      run one build from the command line (development)
modal serve app.py                 temporary web UI for testing
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import modal

APP_NAME = "decision-twin"
ROOT = Path(__file__).resolve().parent

app = modal.App(APP_NAME)
runs = modal.Dict.from_name("decision-twin-runs", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("pydantic==2.13.5", "openai==3.42.0", "fastapi[standard]==0.162.0")
    .add_local_python_source("twin", "worker", "orchestrator", "webapi")
    .add_local_dir(ROOT / "profile", "/root/profile")
    .add_local_dir(ROOT / "web", "/root/web")
)

BRAINS = ("spark", "glimmer")
DEFAULT_TASK = (
    "Build me a small website that shows near-real-time data and news for an NSE-listed stock "
    "and gives an explained Buy / Hold / Sell signal. Budget is close to zero. It is a demo: "
    "it must never place trades and must not present itself as financial advice."
)


def _make_brain(name: str):
    from twin.brain import glimmer_from_env, spark_from_env

    return glimmer_from_env() if name == "glimmer" else spark_from_env()


@app.function(image=image, secrets=[modal.Secret.from_name("meta-api")], timeout=4 * 3600)
def build_job(run_id: str, task: str, brain: str = "spark") -> dict:
    from orchestrator.events import EventLog
    from orchestrator.loop import run_build
    from twin import DecisionTwin
    from twin.profile import load_profile
    from worker.protocol import WORKER_RULES
    from worker.sandbox import OpenClawWorker

    log = EventLog(runs, run_id)
    profile = load_profile()
    twin_brain = _make_brain(brain)
    log.emit(
        "run_started",
        task=task,
        brain=twin_brain.name,
        worker="OpenClaw on Meta Muse Spark 1.3",
        principles=[p.model_dump() for p in profile.principles],
        hard_rules=list(profile.hard_rules),
        settings=profile.decision_settings.model_dump(),
        synthetic=profile.synthetic,
    )
    worker = OpenClawWorker(app, os.environ["META_API_KEY"])
    try:
        worker.start(WORKER_RULES)
        return run_build(task, worker, DecisionTwin(twin_brain, profile), log)
    except Exception as exc:  # surface any crash in the UI, then re-raise for Modal logs
        log.emit("error", message=f"Run crashed: {type(exc).__name__}: {str(exc)[:400]}")
        usage = worker.usage() if worker.sb else {}
        log.emit("run_finished", finished=False, outcome="crashed", decisions=None, usage=usage)
        raise
    finally:
        _set_index(run_id, status="finished")
        worker.stop()


def _set_index(run_id: str, **fields) -> None:
    from webapi import set_index

    set_index(runs, run_id, **fields)


@app.local_entrypoint()
def main(task: str = DEFAULT_TASK, brain: str = "spark"):
    from webapi import new_run_id

    run_id = new_run_id()
    _set_index(run_id, task=task[:200], brain=brain, status="running", started=time.time())
    print(f"run {run_id}")
    print(build_job.remote(run_id, task, brain))


# --------------------------------------------------------------------------- web UI


@app.function(image=image, secrets=[modal.Secret.from_name("twin-ui")], max_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from webapi import create_api

    def launch(run_id: str, task: str, brain: str) -> None:
        build_job.spawn(run_id, task, brain)

    return create_api(runs, os.environ["ACCESS_TOKEN"], Path("/root/web"), launch)
