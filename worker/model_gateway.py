"""Model gateway that runs inside the worker sandbox, as root.

OpenClaw (running as an unprivileged user) talks to http://127.0.0.1:8080/v1 with a dummy
key. This gateway:
  * holds the real Meta API key in a root-only file, so the agent's shell can never read it
  * forwards only POST /v1/chat/completions, and only for allow-listed models
  * enforces a per-run token budget so a runaway agent cannot drain credits
  * strips every header except the ones the upstream needs
  * records token usage to a root-only file the orchestrator can read

Standard library only, so it runs on the bare sandbox image.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("GATEWAY_UPSTREAM", "https://api.meta.ai")
KEY_FILE = os.environ.get("GATEWAY_KEY_FILE", "/root/.gateway/key")
USAGE_FILE = os.environ.get("GATEWAY_USAGE_FILE", "/root/.gateway/usage.json")
ALLOWED_MODELS = frozenset(os.environ.get("GATEWAY_MODELS", "muse-spark-1.3").split(","))
TOKEN_BUDGET = int(os.environ.get("GATEWAY_TOKEN_BUDGET", "4000000"))
MAX_BODY = 8 * 1024 * 1024
ALLOWED_PATHS = frozenset({"/v1/chat/completions"})
# Meta has briefly answered 404 model_not_found for a valid model; treat it like a transient error.
RETRY_STATUSES = frozenset({404, 408, 429, 500, 502, 503, 504})
RETRIES = 3


class Budget:
    """Thread-safe running total of tokens, persisted after every call."""

    def __init__(self, limit: int, path: str) -> None:
        self.limit, self.path = limit, path
        self.used = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "refused": 0, "upstream_errors": 0}
        self._lock = threading.Lock()

    def exhausted(self) -> bool:
        with self._lock:
            return self.used["prompt_tokens"] + self.used["completion_tokens"] >= self.limit

    def add(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.used["prompt_tokens"] += prompt
            self.used["completion_tokens"] += completion
            self.used["calls"] += 1
            self._save()

    def upstream_error(self, status: int, body: bytes, request: bytes = b"") -> None:
        with self._lock:
            self.used["upstream_errors"] += 1
            self.used["last_upstream_error"] = f"{status} {body[:200].decode('utf-8', errors='replace')}"
            self.used["last_failed_request"] = describe_request(request)
            self._save()

    def refuse(self) -> None:
        with self._lock:
            self.used["refused"] += 1
            self._save()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.used, f)
        os.replace(tmp, self.path)


def usage_from_stream(raw: bytes) -> tuple[int, int]:
    """Pull the final usage block out of an SSE stream or a plain JSON body."""
    prompt = completion = 0
    text = raw.decode("utf-8", errors="replace")
    candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")] or [text]
    for chunk in candidates:
        if not chunk or chunk == "[DONE]":
            continue
        try:
            usage = json.loads(chunk).get("usage") or {}
        except (ValueError, AttributeError):
            continue
        prompt = usage.get("prompt_tokens", prompt) or prompt
        completion = usage.get("completion_tokens", completion) or completion
    return int(prompt), int(completion)


def describe_request(body: bytes) -> dict:
    """Shape of a request for debugging, without any message content."""
    try:
        payload = json.loads(body)
    except ValueError:
        return {"bytes": len(body)}
    shape = {k: (f"<{len(v)} items>" if isinstance(v, list) else v) for k, v in payload.items() if k != "messages"}
    return shape | {"messages": len(payload.get("messages") or [])}


def check_request(path: str, body: bytes) -> tuple[int, str] | None:
    """Return (status, reason) if the request must be refused, else None."""
    if path not in ALLOWED_PATHS:
        return 403, "path not allowed"
    if len(body) > MAX_BODY:
        return 413, "request too large"
    try:
        payload = json.loads(body)
    except ValueError:
        return 400, "body is not JSON"
    if not isinstance(payload, dict) or payload.get("model") not in ALLOWED_MODELS:
        return 403, "model not allowed"
    return None


def make_handler(key: str, budget: Budget) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, reason: str) -> None:
            budget.refuse()
            self._reply(status, json.dumps({"error": {"message": f"gateway: {reason}"}}).encode())

        def do_GET(self) -> None:
            self._error(405, "method not allowed")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._error(413, "request too large")
                return
            body = self.rfile.read(length)
            refusal = check_request(self.path, body)
            if refusal:
                self._error(*refusal)
                return
            if budget.exhausted():
                self._error(429, "token budget for this run is used up")
                return
            req = urllib.request.Request(  # noqa: S310 - fixed https upstream
                UPSTREAM + self.path,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            status, data, ctype = 502, b"", "application/json"
            for attempt in range(RETRIES + 1):
                try:
                    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310
                        status, data = resp.status, resp.read()
                        ctype = resp.headers.get("Content-Type", "application/json")
                except urllib.error.HTTPError as exc:
                    status, data, ctype = exc.code, exc.read(), "application/json"
                except (urllib.error.URLError, TimeoutError) as exc:
                    reason = f"gateway: upstream unreachable ({type(exc).__name__})"
                    status, data = 502, json.dumps({"error": {"message": reason}}).encode()
                if status not in RETRY_STATUSES or attempt == RETRIES:
                    break
                budget.upstream_error(status, data, body)
                time.sleep(2 * 2**attempt)  # 2s, 4s, 8s
            if status == 200:
                budget.add(*usage_from_stream(data))
            else:
                budget.upstream_error(status, data, body)
            self._reply(status, data, ctype)

        def log_message(self, fmt: str, *args: object) -> None:  # keep request bodies out of logs
            sys.stderr.write(f"gateway {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    return Handler


def main() -> None:
    with open(KEY_FILE) as f:
        key = f.read().strip()
    budget = Budget(TOKEN_BUDGET, USAGE_FILE)
    budget._save()
    server = ThreadingHTTPServer(("127.0.0.1", 8080), make_handler(key, budget))
    server.serve_forever()


if __name__ == "__main__":
    main()
