"""Hard rules: the guarantees layer of the guardian.

Every action an agent wants to take is classified here first, instantly and deterministically:
  allow   routine work inside the project
  block   breaks a hard rule (secrets, unknown destinations, uploads, destructive outside the project)
  ask     always needs the owner (OTPs, sudo, force-push, payments)
  review  depends on context -> the guardian model decides whether it fits the task

Rules never call a model, so prompt injection cannot talk them out of a block.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

Decision = Literal["allow", "block", "ask", "review"]

PROJECT = "/work/site"

# Destinations the agent may talk to directly. Everything else is blocked at the action layer.
ALLOWED_HOSTS = frozenset(
    {
        "registry.npmjs.org",
        "pypi.org",
        "files.pythonhosted.org",
        "github.com",
        "codeload.github.com",
        "localhost",
        "127.0.0.1",
    }
)


@dataclass(frozen=True)
class Action:
    """One thing an agent is about to do, normalised from a tool call."""

    kind: Literal["exec", "write", "read", "fetch", "credential", "other"]
    detail: str
    tool: str = ""

    def describe(self) -> str:
        verb = {"exec": "run", "write": "write file", "read": "read file", "fetch": "fetch URL"}.get(
            self.kind, self.kind
        )
        return f"{verb}: {self.detail}"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    rule: str | None
    reason: str


_SECRET_PATHS = re.compile(
    r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc|\.git-credentials|/root/\.gateway|"
    r"\.openclaw/.*(auth|credential|token)|/proc/\d*/?(self/)?environ|/proc/self/environ|\.npmrc|\.pypirc)",
    re.IGNORECASE,
)
_ENV_DUMP = re.compile(r"(^|[;&|]\s*|\s)(printenv|env|set|export\s+-p)(\s*$|\s*[;&|>])")
_OTP = re.compile(r"\b(otp|one[- ]time (pass)?code|2fa|two[- ]factor|totp|verification code|mfa code)\b", re.IGNORECASE)
_PAYMENT = re.compile(r"\b(stripe|paypal|razorpay|upi|card number|cvv|wire transfer|payment)\b", re.IGNORECASE)
_NET_TOOLS = frozenset(
    {"curl", "wget", "nc", "ncat", "netcat", "scp", "rsync", "ssh", "sftp", "ftp", "telnet", "socat"}
)
_UPLOAD_FLAGS = re.compile(
    r"(\s-T\s|\s--upload-file|\s-F\s|\s--form|\s--data(-binary|-raw)?\s+@|\s-d\s+@|\s-X\s*(PUT|POST))"
)
_URL = re.compile(r"\b(?:https?|ftp|s3|gs|oss)://[^\s'\"<>|;)]+", re.IGNORECASE)
_RECON = re.compile(
    r"(/etc/(resolv\.conf|passwd|shadow|hosts)|\bifconfig\b|\bip\s+(a|addr|route)\b|\bwhich\s+(curl|wget|nc|scp|ftp)|"
    r"\bls\s+(-\w+\s+)?/tmp\b|\bls\s+(-\w+\s+)?/(root|home|etc)\b|\bwhoami\b|\bid\b\s*$|\buname\s+-a\b|\bcat\s+/etc/)"
)
_ROUTINE = re.compile(
    r"^\s*(cd\s+\S+\s*&&\s*)?(ls|cat|head|tail|wc|grep|find|mkdir|touch|echo|printf|sed|node|npm\s+(test|run|start|ci|"
    r"install|init)|npx\s+(jest|vitest|eslint|prettier)|python3?|pytest|pip3?\s+install|git\s+(status|add|commit|diff|"
    r"log|init|checkout|branch)|sleep|true)\b"
)


def _hosts(text: str) -> list[str]:
    hosts = []
    for url in _URL.findall(text):
        host = (urlparse(url).hostname or "").lower()
        if host:
            hosts.append(host)
    return hosts


def _first_words(cmd: str) -> set[str]:
    """Program names at the start of each pipeline / chained segment."""
    words = set()
    for seg in re.split(r"&&|\|\||[;|]", cmd):
        try:
            parts = shlex.split(seg.strip())
        except ValueError:
            parts = seg.strip().split()
        while parts and (parts[0] in {"sudo", "env", "nohup", "time", "exec"} or "=" in parts[0]):
            words.add(parts[0]) if parts[0] == "sudo" else None
            parts = parts[1:]
        if parts:
            words.add(parts[0].rsplit("/", 1)[-1])
    return words


def _outside_project(path: str) -> bool:
    path = path.strip().strip("'\"")
    if not path:
        return False
    if path.startswith(("~", "/")):
        return not (path == PROJECT or path.startswith(PROJECT + "/") or path.startswith("/tmp/"))
    return ".." in path.split("/")


def check(action: Action) -> Verdict:
    """Classify one action with hard rules only."""
    text = action.detail.strip()
    low = text.lower()

    if action.kind == "credential":
        return Verdict("review", None, "Credential requests are judged against the task")
    if _OTP.search(low):
        return Verdict("ask", "otp_owner_only", "One-time codes and 2FA always go to the owner")
    if _PAYMENT.search(low) and action.kind in {"exec", "fetch", "other"}:
        return Verdict("ask", "payments_owner_only", "Anything involving payments goes to the owner")
    if _SECRET_PATHS.search(text):
        return Verdict("block", "no_secret_access", "Reads or touches credential stores the agent must never see")

    if action.kind in {"write", "read"}:
        if action.kind == "write" and _outside_project(text):
            return Verdict("block", "write_outside_project", f"Writes outside {PROJECT}")
        if action.kind == "read" and _outside_project(text):
            return Verdict("review", None, "Reads a file outside the project")
        return Verdict("allow", "project_file", "File inside the project")

    if action.kind == "fetch":
        hosts = _hosts(text) or [text.lower()]
        bad = [h for h in hosts if h not in ALLOWED_HOSTS]
        if bad:
            return Verdict("block", "unknown_destination", f"Destination not on the allowlist: {', '.join(bad)}")
        return Verdict("allow", "allowed_destination", "Destination is on the allowlist")

    if action.kind != "exec":
        return Verdict("review", None, "Unrecognised action type")

    programs = _first_words(text)
    if _ENV_DUMP.search(" " + text):
        return Verdict("block", "no_env_dump", "Dumps environment variables, where secrets usually live")
    if re.search(r"\brm\s+(-\w*r\w*f?|-\w*f\w*r)\b", low):
        targets = re.findall(r"\brm\s+-\w+\s+(.+?)(?:$|[;&|])", text)
        if any(_outside_project(t) or t.strip() in {"/", "*", "~", "."} for t in " ".join(targets).split()):
            return Verdict("block", "destructive_outside_project", "Recursive delete outside the project")
    if "sudo" in programs or re.search(r"(^|\s)su(\s|$)", text):
        return Verdict("ask", "sudo_owner_only", "Root commands need the owner's approval")
    if re.search(r"git\s+push\b.*(--force|-f\b)", low):
        return Verdict("ask", "force_push_owner_only", "Force-push rewrites history; the owner decides")

    if programs & _NET_TOOLS or _hosts(text):
        hosts = _hosts(text)
        if _UPLOAD_FLAGS.search(" " + text + " ") and any(h not in {"localhost", "127.0.0.1"} for h in hosts or ["?"]):
            return Verdict("block", "no_uploads", "Uploads data to an outside destination")
        bad = [h for h in hosts if h not in ALLOWED_HOSTS]
        if bad:
            return Verdict("block", "unknown_destination", f"Destination not on the allowlist: {', '.join(bad)}")
        if programs & {"ssh", "scp", "sftp", "nc", "ncat", "netcat", "socat", "telnet", "ftp"}:
            return Verdict("block", "raw_network_tool", "Raw network tools are not needed for project work")

    if _RECON.search(text):
        return Verdict("review", None, "Inspects the system outside the project; fine only if the task needs it")
    if re.search(r"\b(cat|less|head|tail|cp|tar|zip|base64)\b[^;|&]*\s(/(?!work/site|tmp/)\S+|~\S*)", text):
        return Verdict("review", None, "Touches files outside the project")
    if _ROUTINE.match(text) and not re.search(r"\s(/(?!work/site|tmp/)[\w.-]+/\S*|~/\S*)", text):
        return Verdict("allow", "routine_project_work", "Routine project command")
    return Verdict("review", None, "Not a routine command; checked against the task")
