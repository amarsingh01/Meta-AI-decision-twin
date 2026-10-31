"""HTTP API and static UI for the demo. Store and job launcher are injected, so it is testable."""

from __future__ import annotations

import re
import secrets
import time
import uuid
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from orchestrator.events import read_events
from twin.profile import load_decisions, load_profile

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
STATIC = frozenset({"app.js", "style.css"})
RUN_TTL_S = 4 * 3600

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def set_index(store: MutableMapping[str, Any], run_id: str, **fields: Any) -> None:
    index = dict(store.get("index", {}))
    index[run_id] = {**index.get(run_id, {}), **fields}
    store["index"] = index


class StartRun(BaseModel):
    task: str = Field(min_length=10, max_length=1500)
    brain: str = Field(pattern="^(spark|glimmer)$")


def create_api(
    store: MutableMapping[str, Any],
    access_token: str,
    web_dir: Path,
    launch: Callable[[str, str, str], None],
) -> FastAPI:
    if len(access_token) < 16:
        raise ValueError("access token is too short")
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @api.get("/")
    def index():
        return FileResponse(web_dir / "index.html")

    @api.get("/static/{name}")
    def static(name: str):
        if name not in STATIC:
            raise HTTPException(404)
        return FileResponse(web_dir / name)

    @api.get("/api/profile")
    def profile():
        return {"profile": load_profile().model_dump(), "decisions": [d.model_dump() for d in load_decisions()]}

    @api.get("/api/runs")
    def list_runs():
        index = store.get("index", {})
        items = sorted(index.items(), key=lambda kv: kv[1].get("started", 0), reverse=True)[:20]
        return [{"id": k, **v} for k, v in items]

    @api.get("/api/runs/{run_id}/events")
    def events(run_id: str, since: int = 0):
        if not RUN_ID.match(run_id) or not 0 <= since < 100_000:
            raise HTTPException(400, "bad request")
        return JSONResponse(read_events(store, run_id, since=since))

    @api.post("/api/runs")
    def start(body: StartRun, x_access_token: str = Header(default="")):
        if not secrets.compare_digest(x_access_token.encode(), access_token.encode()):
            raise HTTPException(401, "access token required to start a run")
        now = time.time()
        index = store.get("index", {})
        if any(v.get("status") == "running" and now - v.get("started", 0) < RUN_TTL_S for v in index.values()):
            raise HTTPException(429, "a build is already running")
        run_id = new_run_id()
        set_index(store, run_id, task=body.task[:200], brain=body.brain, status="running", started=now)
        launch(run_id, body.task, body.brain)
        return {"id": run_id}

    return api
