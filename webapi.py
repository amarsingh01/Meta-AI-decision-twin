"""HTTP API and static UI for the demo. Store and job launcher are injected, so it is testable."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import re
import secrets
import tarfile
import time
import uuid
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from orchestrator.events import read_events
from twin.profile import PROFILE_DIR, load_decisions, load_profile

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
STATIC = frozenset({"app.js", "style.css"})
RUN_TTL_S = 4 * 3600

# Generated sites run in a sandbox: opaque origin, no access to this app, no outside requests.
BUILT_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https://upload.wikimedia.org https://api.gbif.org; "
    "font-src 'self' data:; connect-src 'self' https://api.open-meteo.com https://air-quality-api.open-meteo.com "
    "https://api.gbif.org https://en.wikipedia.org; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
)
BUILT_TYPES = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".png", ".jpg", ".ico", ".txt", ".md", ".csv"}
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")

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


ASK_PER_IP_HOUR = 6
ASK_PER_DAY = 200
GUARDIAN_PROFILE_FILE = PROFILE_DIR / "guardian.json"


class AskGuardian(BaseModel):
    task: str = Field(min_length=5, max_length=400)
    kind: str = Field(pattern="^(exec|write|read|fetch|credential)$")
    detail: str = Field(min_length=1, max_length=300)


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _take(store: MutableMapping[str, Any], key: str, limit: int) -> bool:
    n = int(store.get(key, 0))
    if n >= limit:
        return False
    store[key] = n + 1
    return True


class StartRun(BaseModel):
    task: str = Field(min_length=10, max_length=1500)
    brain: str = Field(pattern="^(spark|glimmer)$")
    guardian: str = Field(default="on", pattern="^(on|off)$")
    redteam: str = Field(default="", pattern="^(|exfil)$")


def create_api(
    store: MutableMapping[str, Any],
    access_token: str,
    web_dir: Path,
    launch: Callable[..., None],
    read_build: Callable[[str], bytes | None] = lambda run_id: None,
    read_shot: Callable[[str], bytes | None] = lambda run_id: None,
    ask: Callable[[str, str, str], dict] | None = None,
) -> FastAPI:
    if len(access_token) < 16:
        raise ValueError("access token is too short")
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = BUILT_CSP if request.url.path.startswith("/built/") else CSP
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

    @api.get("/api/eval")
    def evaluation():
        return {b: store.get(f"eval:{b}") for b in ("glimmer", "spark")}

    @api.get("/api/guardian")
    def guardian_info():
        profile = json.loads(GUARDIAN_PROFILE_FILE.read_text()) if GUARDIAN_PROFILE_FILE.exists() else None
        return {"profile": profile, "scorecard": store.get("guardian:scorecard"), "ask_enabled": ask is not None}

    @api.post("/api/guardian/ask")
    def guardian_ask(body: AskGuardian, request: Request):
        if ask is None:
            raise HTTPException(503, "the live guardian is not enabled here")
        day, hour = time.strftime("%Y%m%d"), time.strftime("%Y%m%d%H")
        if not _take(store, f"ask:ip:{_client_key(request)}:{hour}", ASK_PER_IP_HOUR):
            raise HTTPException(429, f"limit reached: {ASK_PER_IP_HOUR} questions per hour")
        if not _take(store, f"ask:day:{day}", ASK_PER_DAY):
            raise HTTPException(429, "the daily limit for the live guardian is reached; try tomorrow")
        return ask(body.task, body.kind, body.detail)

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

    def _build_file(run_id: str, path: str) -> tuple[bytes, str] | None:
        if not RUN_ID.match(run_id) or not SAFE_PATH.match(path) or ".." in path.split("/"):
            return None
        ext = Path(path).suffix.lower()
        if ext not in BUILT_TYPES:
            return None
        data = read_build(run_id)
        if not data:
            return None
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for name in (f"./{path}", path):
                try:
                    member = tar.getmember(name)
                except KeyError:
                    continue
                if member.isfile() and member.size < 5 * 1024 * 1024:
                    f = tar.extractfile(member)
                    if f:
                        return f.read(), mimetypes.guess_type(path)[0] or "text/plain"
        return None

    @api.get("/built/{run_id}/{path:path}")
    def built(run_id: str, path: str = ""):
        found = (
            _build_file(run_id, path or "index.html")
            or _build_file(run_id, f"{path.rstrip('/')}/index.html".lstrip("/"))
            or (
                None
                if path
                else next(
                    (f for d in ("dist", "build", "public", "out") if (f := _build_file(run_id, f"{d}/index.html"))),
                    None,
                )
            )
        )
        if not found:
            raise HTTPException(404)
        return Response(content=found[0], media_type=found[1])

    @api.get("/api/runs/{run_id}/screenshot.png")
    def screenshot(run_id: str):
        data = read_shot(run_id) if RUN_ID.match(run_id) else None
        if not data or not data.startswith(b"\x89PNG"):
            raise HTTPException(404)
        return Response(content=data, media_type="image/png")

    @api.get("/api/runs/{run_id}/build.tar.gz")
    def build_archive(run_id: str):
        data = read_build(run_id) if RUN_ID.match(run_id) else None
        if not data:
            raise HTTPException(404)
        headers = {"Content-Disposition": f'attachment; filename="{run_id}-site.tar.gz"'}
        return Response(content=data, media_type="application/gzip", headers=headers)

    @api.post("/api/runs")
    def start(body: StartRun, x_access_token: str = Header(default="")):
        if not secrets.compare_digest(x_access_token.encode(), access_token.encode()):
            raise HTTPException(401, "access token required to start a run")
        now = time.time()
        index = store.get("index", {})
        if any(v.get("status") == "running" and now - v.get("started", 0) < RUN_TTL_S for v in index.values()):
            raise HTTPException(429, "a build is already running")
        run_id = new_run_id()
        set_index(
            store,
            run_id,
            task=body.task[:200],
            brain=body.brain,
            status="running",
            started=now,
            guardian=body.guardian,
            redteam=body.redteam or None,
        )
        launch(run_id, body.task, body.brain, body.guardian, body.redteam)
        return {"id": run_id}

    return api
