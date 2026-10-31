"""OpenClaw worker in an isolated Modal sandbox.

Isolation:
  * OpenClaw and every command it runs execute as the unprivileged user `worker`.
  * The real Meta API key lives in /root/.gateway (mode 700), read only by the root-owned
    model gateway. OpenClaw is configured with a dummy key pointing at the gateway.
  * The sandbox has no Modal secrets attached, so nothing sensitive is in its environment.
  * The sandbox has a hard lifetime and is terminated when the run ends.

Must be used from code already running inside Modal (the orchestrator function).
"""

from __future__ import annotations

import base64
import json
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path

import modal

OPENCLAW_VERSION = "2026.10.9"
SITE_DIR = "/work/site"
GATEWAY_DIR = "/root/.gateway"
WORKER_USER = "worker"
WORKER_MODEL = "muse-spark-1.3"
SANDBOX_REGION = "us-east"

_HERE = Path(__file__).resolve().parent

worker_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "git")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
        "apt-get install -y nodejs",
        f"npm install -g openclaw@{OPENCLAW_VERSION}",
        "chmod 1777 /tmp",
        f"useradd --create-home --shell /bin/bash {WORKER_USER}",
        f"mkdir -p {SITE_DIR} && chown -R {WORKER_USER}:{WORKER_USER} /work",
        f"mkdir -p {GATEWAY_DIR} && chmod 700 {GATEWAY_DIR}",
        "pip install --no-cache-dir pytest==9.1.1",
    )
)


@dataclass(frozen=True)
class TurnResult:
    text: str
    ok: bool
    error: str | None
    duration_ms: int
    files_changed: str


def _as_worker(cmd: str) -> list[str]:
    return ["runuser", "-u", WORKER_USER, "--", "bash", "-lc", f"cd {SITE_DIR} && {cmd}"]


class OpenClawWorker:
    def __init__(
        self,
        app: modal.App,
        api_key: str,
        token_budget: int = 4_000_000,
        lifetime_s: int = 3 * 3600,
        preview_port: int | None = None,
    ):
        self._app = app
        self._preview_port = preview_port
        self._api_key = api_key
        self._budget = token_budget
        self._lifetime = lifetime_s
        self.session_id = f"build-{uuid.uuid4().hex[:12]}"
        self.sb: modal.Sandbox | None = None

    # ------------------------------------------------------------------ low level

    def _exec(self, argv: list[str], stdin: str | None = None, timeout: int = 900) -> tuple[int, str, str]:
        assert self.sb is not None, "call start() first"
        p = self.sb.exec(*argv, timeout=timeout)
        if stdin is not None:
            p.stdin.write(stdin.encode())
            p.stdin.write_eof()
            p.stdin.drain()
        out, err = p.stdout.read(), p.stderr.read()
        p.wait()
        return p.returncode, out, err

    def _write_root_file(self, path: str, content: str, mode: str = "600") -> None:
        script = f"umask 077 && cat > {shlex.quote(path)} && chmod {mode} {shlex.quote(path)}"
        rc, _, err = self._exec(["bash", "-c", script], content)
        if rc != 0:
            raise RuntimeError(f"could not write {path}: {err[-300:]}")

    def export_archive(self, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        """The built project as a .tar.gz (no dependencies, git history or agent state)."""
        script = (
            f"cd {SITE_DIR} && tar --exclude=node_modules --exclude=.git --exclude=.venv --exclude=__pycache__ "
            "--exclude=.openclaw -czf - . | base64 -w0"
        )
        rc, out, err = self._exec(["bash", "-c", script], timeout=300)
        if rc != 0:
            raise RuntimeError(f"export failed: {err[-300:]}")
        data = base64.b64decode(out)
        if len(data) > max_bytes:
            raise RuntimeError(f"export too large ({len(data)} bytes)")
        return data

    def run(self, cmd: str, timeout: int = 600) -> tuple[int, str]:
        """Run a shell command as the worker user inside the project folder."""
        rc, out, err = self._exec(_as_worker(cmd), timeout=timeout)
        return rc, (out + err)[-6000:]

    # ------------------------------------------------------------------ lifecycle

    def start(self, rules: str) -> None:
        ports = [self._preview_port] if self._preview_port else []
        self.sb = modal.Sandbox.create(
            app=self._app,
            image=worker_image,
            timeout=self._lifetime,
            cpu=2.0,
            memory=4096,
            encrypted_ports=ports,
            # Meta's API answers 404 model_not_found from some regions; keep the builder in the US.
            region=SANDBOX_REGION,
        )

        # 1. Root-only model gateway holding the real key.
        self._write_root_file(f"{GATEWAY_DIR}/key", self._api_key)
        self._write_root_file(f"{GATEWAY_DIR}/gateway.py", (_HERE / "model_gateway.py").read_text(), mode="700")
        env = f"GATEWAY_MODELS={WORKER_MODEL} GATEWAY_TOKEN_BUDGET={self._budget}"
        self._exec(["bash", "-c", f"cd {GATEWAY_DIR} && {env} nohup python3 gateway.py >gateway.log 2>&1 &"])
        wait_port = (
            "for i in $(seq 30); do (echo >/dev/tcp/127.0.0.1/8080) 2>/dev/null && exit 0; sleep 0.2; done; exit 1"
        )
        rc, _, _ = self._exec(["bash", "-c", wait_port])
        if rc != 0:
            raise RuntimeError("model gateway did not start")

        # 2. OpenClaw for the worker user, pointed at the gateway with a dummy key.
        onboard = (
            "openclaw onboard --non-interactive --accept-risk --mode local --flow quickstart "
            "--custom-base-url http://127.0.0.1:8080/v1 "
            f"--custom-model-id {WORKER_MODEL} --custom-api-key not-a-real-key "
            "--custom-compatibility openai --custom-provider-id meta --custom-text-input "
            f"--no-install-daemon --skip-channels --skip-skills --skip-bootstrap --workspace {SITE_DIR} --json"
        )
        rc, out = self.run(onboard, timeout=300)
        if rc != 0:
            raise RuntimeError(f"OpenClaw onboarding failed: {out[-500:]}")

        # 3. Standing rules: OpenClaw loads AGENTS.md from its workspace on every turn.
        rc, _, err = self._exec(
            ["runuser", "-u", WORKER_USER, "--", "bash", "-c", f"cat > {SITE_DIR}/AGENTS.md"], rules
        )
        if rc != 0:
            raise RuntimeError(f"could not write AGENTS.md: {err[-300:]}")
        self.run(
            "git init -q 2>/dev/null; git config user.email worker@local; git config user.name worker; "
            "git add -A && git commit -qm 'start' || true"
        )

    def send(self, message: str, timeout_s: int = 900) -> TurnResult:
        """One OpenClaw turn in the persistent build session."""
        msg_file = f"/home/{WORKER_USER}/.turn-message.md"
        rc, _, err = self._exec(
            ["runuser", "-u", WORKER_USER, "--", "bash", "-c", f"umask 077 && cat > {msg_file}"], message
        )
        if rc != 0:
            raise RuntimeError(f"could not stage message: {err[-300:]}")
        cmd = (
            f"timeout -k 30 {timeout_s} openclaw agent --local --agent main "
            f"--session-id {self.session_id} --json --message-file {msg_file}"
        )
        rc, out, err = self._exec(_as_worker(cmd), timeout=timeout_s + 60)
        text, ok, error, dur = "", rc == 0, None, 0
        try:
            data = json.loads(out)
            text = "\n".join(p.get("text") or "" for p in data.get("payloads") or []).strip()
            dur = int((data.get("meta") or {}).get("durationMs") or 0)
            if data.get("ok") is False:
                ok, error = False, str((data.get("error") or {}).get("message", "unknown error"))[:500]
        except ValueError:
            ok, error = False, (err or out)[-500:] or f"exit code {rc}"
        _, changed = self.run(
            "git add -A && git diff --cached --stat | tail -15; git commit -qm turn >/dev/null 2>&1 || true"
        )
        return TurnResult(text=text, ok=ok, error=error, duration_ms=dur, files_changed=changed.strip())

    def usage(self) -> dict:
        rc, out, _ = self._exec(["cat", f"{GATEWAY_DIR}/usage.json"])
        try:
            return json.loads(out) if rc == 0 else {}
        except ValueError:
            return {}

    def tunnel_url(self, port: int) -> str | None:
        assert self.sb is not None
        tunnels = self.sb.tunnels()
        return tunnels[port].url if port in tunnels else None

    def stop(self) -> None:
        if self.sb is not None:
            self.sb.terminate()
            self.sb = None
