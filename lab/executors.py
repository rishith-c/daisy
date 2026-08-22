"""
Which coding agents on this machine can actually be driven right now.

Daisy has claimed since the first commit that it coordinates Claude Code and
Codex. agents/discover.py proved it can *see* their sessions; this is the part
that runs one. The distinction matters, and the honest answer today is that
availability is per-machine and changes hour to hour:

    claude -p         installed, but the CLI's OAuth session had expired
    codex exec        authenticated, but the CLI is older than the model its
                      own config.toml selects
    opencode run      works

So an executor is not a name in a config file, it is a probe. `available()`
runs each candidate against a trivial prompt with a short timeout and reports
what came back. A factory that assumes its agents are reachable produces a run
that fails halfway with a stack trace instead of a plan that says "the software
lane cannot run, here is why".

Nothing here retries an unreachable agent into working. If the credential is
stale, the fix is a human running `claude` once — not this file pretending.

Zero third-party dependencies.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

PROBE = "Reply with exactly: OK"
PROBE_TIMEOUT = 75
RUN_TIMEOUT = 600


@dataclass
class Executor:
    name: str
    binary: str
    argv: list                     # argv template; "{prompt}" is substituted
    ok: bool = False
    detail: str = ""
    version: str = ""
    model: str = ""
    probe_ms: float = 0.0

    def command(self, prompt: str) -> list:
        return [a.replace("{prompt}", prompt) for a in self.argv]


def _candidates() -> list[Executor]:
    return [
        Executor("claude", "claude",
                 ["claude", "-p", "{prompt}", "--output-format", "text"]),
        # --skip-git-repo-check because a worktree is not always what codex
        # considers a trusted directory, and refusing to start is not a failure
        # of the prompt.
        Executor("codex", "codex",
                 ["codex", "exec", "--skip-git-repo-check", "{prompt}"]),
        Executor("opencode", "opencode",
                 ["opencode", "run", "{prompt}"]),
    ]


# Failure text each CLI produces when it is installed but cannot actually run.
# Matching on these gives the operator the real reason instead of "no output".
_DIAGNOSE = [
    (re.compile(r"OAuth session expired|could not be refreshed|Failed to authenticate", re.I),
     "credential expired — run the CLI once interactively and sign in"),
    (re.compile(r"requires a newer version", re.I),
     "CLI is older than the model its config selects — upgrade the CLI or pin an older model"),
    (re.compile(r"Not inside a trusted directory", re.I),
     "directory not trusted by the CLI"),
    (re.compile(r"invalid_request_error|invalid_token|AuthRequired", re.I),
     "authentication rejected"),
    (re.compile(r"rate.?limit|quota|usage limit", re.I),
     "rate limited or out of quota"),
]


def _diagnose(text: str) -> str:
    for pat, why in _DIAGNOSE:
        if pat.search(text):
            return why
    return ""


def probe(ex: Executor, timeout: int = PROBE_TIMEOUT, cwd: str = None) -> Executor:
    """Run a trivial prompt and decide whether this agent is usable."""
    if not shutil.which(ex.binary):
        ex.ok, ex.detail = False, "not installed"
        return ex
    t0 = time.perf_counter()
    try:
        p = subprocess.run(ex.command(PROBE), capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired:
        ex.ok, ex.detail = False, "timed out after %ds" % timeout
        return ex
    except OSError as exc:
        ex.ok, ex.detail = False, str(exc)[:80]
        return ex
    ex.probe_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    why = _diagnose(out)
    if why:
        ex.ok, ex.detail = False, why
        return ex
    # The probe asks for a literal token so success is checkable rather than
    # "something was printed" — several CLIs print banners even when they fail.
    if re.search(r"\bOK\b", out):
        ex.ok, ex.detail = True, "responded to the probe"
    else:
        ex.ok = False
        ex.detail = "no usable reply" + (": " + out.strip().splitlines()[-1][:60] if out.strip() else "")
    return ex


def available(names: list = None, cwd: str = None) -> list[Executor]:
    out = []
    for ex in _candidates():
        if names and ex.name not in names:
            continue
        out.append(probe(ex, cwd=cwd))
    return out


def pick(prefer: str = "auto", cwd: str = None) -> tuple:
    """Return (executor_or_None, all_probed). Never raises on unavailability."""
    probed = available(None if prefer == "auto" else [prefer], cwd=cwd)
    for ex in probed:
        if ex.ok:
            return ex, probed
    return None, probed


def run(ex: Executor, prompt: str, cwd: str = None,
        timeout: int = RUN_TIMEOUT) -> dict:
    """Drive a real agent. Returns what happened; never raises on agent error."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(ex.command(prompt), capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"agent": ex.name, "ok": False, "reason": "timeout",
                "ms": round((time.perf_counter() - t0) * 1000.0, 1), "stdout": "", "stderr": ""}
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    why = _diagnose((p.stdout or "") + (p.stderr or ""))
    return {"agent": ex.name, "ok": p.returncode == 0 and not why,
            "reason": why or ("exit %d" % p.returncode if p.returncode else ""),
            "ms": ms, "stdout": p.stdout or "", "stderr": (p.stderr or "")[-2000:]}
