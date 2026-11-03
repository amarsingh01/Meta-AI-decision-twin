"""Credential broker: the agent asks, the guardian decides, and the guardian does the privileged step.

The agent runs `guardian-request <resource> "<why>"`. It never receives a secret. For the demo
the only brokered resource is `deploy`: the guardian copies the built site to a root-owned deploy
target using a demo deploy key, and tells the agent what happened.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from guardian.policy import PROJECT, Action

DEPLOY_TARGET = Path("/srv/deploy")
_SKIP = {"node_modules", ".git", ".openclaw", ".venv", "__pycache__"}


class Broker:
    def __init__(self, hook, state_dir: str):
        self.hook = hook
        self.state = Path(state_dir)

    def _key_fingerprint(self) -> str:
        try:
            return hashlib.sha256((self.state / "deploy_key").read_bytes()).hexdigest()[:12]
        except OSError:
            return "none"

    def _deploy(self) -> str:
        src = Path(PROJECT)
        for candidate in ("dist", "build", "public"):
            if (src / candidate).is_dir():
                src = src / candidate
                break
        if DEPLOY_TARGET.exists():
            shutil.rmtree(DEPLOY_TARGET)
        shutil.copytree(src, DEPLOY_TARGET, ignore=lambda d, names: [n for n in names if n in _SKIP])
        files = sum(1 for p in DEPLOY_TARGET.rglob("*") if p.is_file())
        receipt = {"ts": time.time(), "files": files, "source": str(src), "key_fingerprint": self._key_fingerprint()}
        (self.state / "deploy_receipt.json").write_text(json.dumps(receipt))
        return (
            f"Deployed {files} files from {src} to the deploy target. "
            "The deploy key was used by the guardian and never shown to you."
        )

    def __call__(self, resource: str, reason: str) -> dict:
        action = Action("credential", f"{resource}: {reason}", "guardian-request")
        if self.hook.guardian is None:
            res_decision, res_reason, event = "allow", "Guardian is off", None
        else:
            with self.hook._lock:
                recent = tuple(self.hook._recent)
            res = self.hook.guardian.check(self.hook.task(), action, recent)
            res_decision, res_reason, event = res.decision, res.reason, res.as_event()
            with self.hook._lock:
                self.hook._recent.append(f"{action.describe()} -> {res.decision}")
        performed = ""
        if res_decision == "allow" and resource.strip().lower().startswith("deploy"):
            try:
                performed = self._deploy()
            except OSError as exc:
                performed = f"The guardian approved the deploy but it failed: {exc}"
        elif res_decision == "allow":
            performed = f"Approved, but the guardian has no broker for '{resource}' in this demo."
        self.hook._log(
            {
                "tool": "guardian-request",
                "redteam": False,
                "performed": performed,
                **(
                    event
                    or {"decision": res_decision, "layer": "off", "reason": res_reason, "action": action.describe()}
                ),
            }
        )
        if res_decision == "allow":
            return {"granted": True, "message": performed}
        verb = "Denied" if res_decision == "block" else "Waiting for the owner's approval"
        return {"granted": False, "message": f"{verb}: {res_reason}"}
