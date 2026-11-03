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
builds = modal.Volume.from_name("decision-twin-builds", create_if_missing=True)
BUILDS = "/builds"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("pydantic==2.13.5", "openai==3.42.0", "fastapi[standard]==0.162.0")
    .add_local_python_source("twin", "worker", "orchestrator", "webapi", "guardian")
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


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("meta-api"), modal.Secret.from_name("glimmer")],
    volumes={BUILDS: builds},
    timeout=4 * 3600,
)
def build_job(run_id: str, task: str, brain: str = "spark", guardian: str = "on", redteam: str = "") -> dict:
    from orchestrator.events import EventLog
    from orchestrator.loop import Limits, run_build
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
        guardian=_guardian_summary(guardian, redteam),
    )
    worker = OpenClawWorker(app, os.environ["META_API_KEY"])
    try:
        worker.start(WORKER_RULES, task=task, guardian=guardian, redteam=redteam)
        limits = Limits(max_turns=30, max_decisions=6) if guardian else Limits()
        summary = run_build(task, worker, DecisionTwin(twin_brain, profile), log, limits)
        for ev in worker.guardian_events():
            log.emit("guardian", turn=None, **{k: v for k, v in ev.items() if k != "ts"}, at=ev.get("ts"))
        receipt = worker.deploy_receipt()
        if receipt:
            log.emit(
                "note",
                message=f"Guardian deployed {receipt['files']} files; the agent never saw the key",
                receipt=receipt,
            )
        _save_build(run_id, worker, log, start_cmd=summary.get("run"))
        return summary
    except Exception as exc:  # surface any crash in the UI, then re-raise for Modal logs
        log.emit("error", message=f"Run crashed: {type(exc).__name__}: {str(exc)[:400]}")
        usage = worker.usage() if worker.sb else {}
        log.emit("run_finished", finished=False, outcome="crashed", decisions=None, usage=usage)
        raise
    finally:
        _set_index(run_id, status="finished")
        worker.stop()


def _guardian_summary(mode: str, redteam: str) -> dict | None:
    if mode not in {"on", "off"}:
        return None
    from guardian.judge import GUARDIAN_PROFILE
    from twin.profile import load_profile

    gp = load_profile(GUARDIAN_PROFILE)
    return {
        "mode": mode,
        "redteam": redteam or None,
        "model": "Meta muse-spark-1.3 (guardian reviews)",
        "principles": [p.model_dump() for p in gp.principles],
        "hard_rules": list(gp.hard_rules),
    }


def _save_build(run_id: str, worker, log, start_cmd: str | None = None) -> None:
    """Keep what the builder made, so it can be inspected after the sandbox is gone."""
    import io
    import tarfile

    try:
        data = worker.export_archive()
    except Exception as exc:
        log.emit("note", message=f"Could not export the built project: {exc}")
        return
    out = Path(BUILDS) / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "site.tar.gz").write_bytes(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        files = sorted(m.name.removeprefix("./") for m in tar.getmembers() if m.isfile())
    shot = None
    if start_cmd:
        try:
            shot = worker.screenshot(start_cmd)
        except Exception as exc:
            log.emit("note", message=f"Could not photograph the built site: {exc}")
    if not shot:
        static = worker.static_site_dir()
        if static:
            try:
                shot = worker.screenshot(f"python3 -m http.server 3999 --directory {static}", port=3999)
            except Exception as exc:
                log.emit("note", message=f"Could not photograph the static site: {exc}")
    if shot:
        (out / "screenshot.png").write_bytes(shot)
    builds.commit()
    log.emit("note", message=f"Saved the built project ({len(files)} files)", files=files[:200], screenshot=bool(shot))


def _set_index(run_id: str, **fields) -> None:
    from webapi import set_index

    set_index(runs, run_id, **fields)


@app.function(
    image=image, secrets=[modal.Secret.from_name("meta-api"), modal.Secret.from_name("glimmer")], timeout=3600
)
def run_eval(brain: str) -> dict:
    """Agreement on held-out decisions; stored for the UI.  modal run app.py::evaluate --brain glimmer"""
    from twin.evaluation import evaluate as _evaluate

    result = _evaluate(_make_brain(brain))
    runs[f"eval:{brain}"] = result
    return result


@app.function(image=image, secrets=[modal.Secret.from_name("meta-api")], timeout=3600)
def run_scorecard() -> dict:
    """Guardian scorecard (no guardian / rules only / rules + model); stored for the UI."""
    from guardian.judge import Guardian
    from guardian.scorecard import run
    from twin.brain import spark_from_env

    result = run(Guardian(spark_from_env()))
    runs["guardian:scorecard"] = result
    return result


@app.local_entrypoint()
def scorecard():
    r = run_scorecard.remote()
    print(r["summary"])


@app.local_entrypoint()
def evaluate(brain: str = "glimmer"):
    r = run_eval.remote(brain)
    print({k: v for k, v in r.items() if k != "rows"})
    for row in r["rows"]:
        print(row)


@app.local_entrypoint()
def main(task: str = DEFAULT_TASK, brain: str = "spark", guardian: str = "", redteam: str = ""):
    from webapi import new_run_id

    run_id = new_run_id()
    _set_index(
        run_id,
        task=task[:200],
        brain=brain,
        status="running",
        started=time.time(),
        guardian=guardian or None,
        redteam=redteam or None,
    )
    print(f"run {run_id}")
    print(build_job.remote(run_id, task, brain, guardian or None, redteam))


# --------------------------------------------------------------------------- web UI


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("twin-ui"), modal.Secret.from_name("meta-api")],
    volumes={BUILDS: builds},
    max_containers=1,
    timeout=300,
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from webapi import create_api

    def launch(run_id: str, task: str, brain: str, guardian: str = "on", redteam: str = "") -> None:
        build_job.spawn(run_id, task, brain, guardian, redteam)

    def read_build(run_id: str) -> bytes | None:
        path = Path(BUILDS) / run_id / "site.tar.gz"
        if not path.exists():
            builds.reload()
        return path.read_bytes() if path.exists() else None

    def read_shot(run_id: str) -> bytes | None:
        path = Path(BUILDS) / run_id / "screenshot.png"
        if not path.exists():
            builds.reload()
        return path.read_bytes() if path.exists() else None

    from guardian.judge import Guardian
    from guardian.policy import Action
    from twin.brain import spark_from_env

    guardian = Guardian(spark_from_env())

    def ask(task: str, kind: str, detail: str) -> dict:
        return guardian.check(task, Action(kind, detail)).as_event()

    return create_api(runs, os.environ["ACCESS_TOKEN"], Path("/root/web"), launch, read_build, read_shot, ask)
