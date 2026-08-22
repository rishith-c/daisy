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
from concurrent.futures import ThreadPoolExecutor
from glob import glob
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
    effort: str = ""
    provider: str = ""
    probe_ms: float = 0.0

    def command(self, prompt: str, model: str = "", effort: str = "",
                provider: str = "", speed: str = "") -> list:
        """Build the exact argv for the chosen model; no shell is involved."""
        options = []
        if model:
            if self.name == "claude":
                options.extend(["--model", model])
            elif self.name == "codex":
                options.extend(["--model", model])
                if effort:
                    options.extend(["-c", 'model_reasoning_effort="%s"' % effort])
                if speed == "fast":
                    options.extend(["-c", 'service_tier="fast"'])
            elif self.name == "opencode":
                selected = "%s/%s" % (provider, model) if provider else model
                options.extend(["--model", selected])
                if effort and effort != "automatic":
                    options.extend(["--variant", effort])
        command = []
        for arg in self.argv:
            if arg == "{prompt}":
                command.extend(options)
                command.append(prompt)
            else:
                command.append(arg.replace("{prompt}", prompt))
        return command


def _claude_candidate(home: str = None, which=shutil.which) -> Executor:
    """Run Claude with a supported Node when the global Node is too new.

    Claude's executable is a JavaScript file with an env-node shebang. On this
    Mac the global Node 26 crashes before the CLI can parse arguments, while
    the installed NVM Node 22 is supported. An explicit argv keeps the selected
    runtime visible and avoids changing the operator's shell configuration.
    """
    cli = which("claude")
    home = home or os.path.expanduser("~")
    compatible = []
    for path in glob(os.path.join(home, ".nvm", "versions", "node", "v*", "bin", "node")):
        match = re.search(r"/v(\d+)(?:\.|/)", path)
        if match and 20 <= int(match.group(1)) < 25:
            compatible.append((int(match.group(1)), path))
    if compatible:
        node = max(compatible)[1]
        nvm_cli = os.path.join(os.path.dirname(node), "claude")
        if os.path.exists(nvm_cli):
            cli = nvm_cli
        elif not cli:
            cli = "claude"
        return Executor("claude", node,
                        [node, cli, "-p", "{prompt}", "--output-format", "text"])
    cli = cli or "claude"
    return Executor("claude", "claude",
                    ["claude", "-p", "{prompt}", "--output-format", "text"])


def _candidates() -> list[Executor]:
    return [
        _claude_candidate(),
        # --skip-git-repo-check because a worktree is not always what codex
        # considers a trusted directory, and refusing to start is not a failure
        # of the prompt.
        Executor("codex", "codex",
                 ["codex", "exec", "--skip-git-repo-check", "--approve-for-me",
                  "{prompt}"]),
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


# Lines these CLIs emit about *other* processes. Codex starts every MCP server
# in the user's config and logs each one's failure to stderr; huggingface's
# server not being logged in says nothing about whether Codex can run.
_FOREIGN = re.compile(r"rmcp::|mcp[_.]|failed to load skill|Transport channel closed"
                      r"|resource_metadata=|\.well-known/oauth", re.I)


def _diagnose(text: str) -> str:
    own = "\n".join(l for l in (text or "").splitlines() if not _FOREIGN.search(l))
    for pat, why in _DIAGNOSE:
        if pat.search(own):
            return why
    return ""


def probe(ex: Executor, timeout: int = PROBE_TIMEOUT, cwd: str = None,
          model: str = "", provider: str = "") -> Executor:
    """Run a trivial prompt and decide whether this agent is usable."""
    ex.model, ex.provider = model, provider
    if not shutil.which(ex.binary):
        ex.ok, ex.detail = False, "not installed"
        return ex
    t0 = time.perf_counter()
    try:
        p = subprocess.run(ex.command(PROBE, model=model, provider=provider),
                           capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired:
        ex.ok, ex.detail = False, "timed out after %ds" % timeout
        return ex
    except OSError as exc:
        ex.ok, ex.detail = False, str(exc)[:80]
        return ex
    ex.probe_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    # Success is checked BEFORE diagnosis, not after. Codex answered the probe
    # correctly while its stderr carried an auth failure from an unrelated MCP
    # server, and diagnosing first reported a working agent as unusable. If the
    # agent produced the answer, it works — whatever else it complained about.
    if re.search(r"\bOK\b", p.stdout or ""):
        ex.ok, ex.detail = True, "responded to the probe"
        return ex
    why = _diagnose(out)
    if why:
        ex.ok, ex.detail = False, why
        return ex
    if re.search(r"\bOK\b", out):
        ex.ok, ex.detail = True, "responded to the probe (on stderr)"
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


def available_models(model_inventory: dict, cwd: str = None) -> list[Executor]:
    """Probe every inventory model concurrently and retain exact identities."""
    rows = [row for row in (model_inventory.get("models") or [])
            if row.get("vendor") and row.get("id")]

    def measure(row):
        candidates = {candidate.name: candidate for candidate in _candidates()}
        ex = candidates.get(str(row.get("vendor")))
        if not ex:
            return Executor(str(row.get("vendor")), "", [], ok=False,
                            detail="no executor adapter", model=str(row.get("id")))
        return probe(ex, cwd=cwd, model=str(row.get("id")),
                     provider=str(row.get("provider") or ""))

    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(rows))) as pool:
        return list(pool.map(measure, rows))


def summarize_models(measured: list[Executor]) -> list[Executor]:
    """Collapse exact model probes into truthful onboarding vendor rows."""
    names = list(dict.fromkeys(row.name for row in measured))
    out = []
    for name in names:
        rows = [row for row in measured if row.name == name]
        working = [row for row in rows if row.ok]
        detail = "%d/%d selectable models responded" % (len(working), len(rows))
        if not working and rows:
            detail += " — " + (rows[0].detail or "no usable reply")
        out.append(Executor(
            name, rows[0].binary if rows else name, [], ok=bool(working),
            detail=detail, probe_ms=max((row.probe_ms for row in rows), default=0)))
    return out


def select(name: str, candidates: list[Executor] = None) -> tuple:
    """Resolve an installed adapter without spending a second model request.

    A user-initiated run is itself the authoritative availability check. The
    onboarding and Daisy Chain paths still probe because they must compare a
    roster before choosing; a single selected run reports its own CLI error.
    """
    for ex in (candidates if candidates is not None else _candidates()):
        if ex.name != name:
            continue
        if shutil.which(ex.binary):
            return ex, ""
        return None, "not installed"
    return None, "no executor adapter"


def pick(prefer: str = "auto", cwd: str = None, model: str = "",
         provider: str = "") -> tuple:
    """Return (executor_or_None, all_probed). Never raises on unavailability."""
    names = None if prefer == "auto" else [prefer]
    probed = []
    for ex in _candidates():
        if names and ex.name not in names:
            continue
        probed.append(probe(ex, cwd=cwd, model=model, provider=provider))
    for ex in probed:
        if ex.ok:
            return ex, probed
    return None, probed


def run(ex: Executor, prompt: str, cwd: str = None,
        timeout: int = RUN_TIMEOUT, model: str = "", effort: str = "",
        provider: str = "", speed: str = "") -> dict:
    """Drive a real agent. Returns what happened; never raises on agent error."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(ex.command(prompt, model=model, effort=effort,
                                      provider=provider, speed=speed),
                           capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"agent": ex.name, "ok": False, "reason": "timeout",
                "ms": round((time.perf_counter() - t0) * 1000.0, 1), "stdout": "", "stderr": ""}
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    # Same rule: a zero exit with output is success, regardless of what the
    # CLI logged about its own plugins.
    why = "" if (p.returncode == 0 and (p.stdout or "").strip()) \
          else _diagnose((p.stdout or "") + (p.stderr or ""))
    return {"agent": ex.name, "ok": p.returncode == 0 and not why,
            "reason": why or ("exit %d" % p.returncode if p.returncode else ""),
            "ms": ms, "stdout": p.stdout or "", "stderr": (p.stderr or "")[-2000:]}
