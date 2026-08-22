"""
Needs attention — what a previous import brought in but could not finish.

This is the honest half of Import and it is not a footnote. An import that
reports "47 skills imported" and says nothing about the four that point at
files which are not there has told you a number, not the truth. Every check
below runs against what was actually imported, never against the whole machine:
Daisy has no business grading config it was not asked to take.

Four things go wrong, in descending order of how often:

    skill needs something      SKILL.md links a reference that is not on disk,
                               or requirements.txt names packages that are not
                               installed. Either way the skill is mounted and
                               half of it is missing.
    server not authenticated   an MCP server is configured and carries no usable
                               credential. `detect._auth_state` decides this the
                               same way garden/identity.py decides a sign-in —
                               by the shape of what is there, never by reading
                               the value.
    rules point at nothing     an imported rules file cites a path that does not
                               exist. That instruction cannot be followed by any
                               agent, in any tool.
    source vanished            the file or directory an import came from was
                               deleted or moved afterwards.

Every item names a fix that is a command you can actually run, because "needs
attention" with no next step is a nag.

IS NOT a linter for the config's contents, a security audit, or a judgement
about whether a skill is any good. It answers one question: can the thing that
was imported actually run?

Zero third-party dependencies.
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, asdict

from .detect import HOME, MD_LINK, _needs_auth_cache
from .state import State

# A backticked token is a path only if it is shaped like one. `--json` and
# `evaluate()` are not files, and flagging them would drown the real findings.
BACKTICK = re.compile(r"`([^`\n]{2,120})`")
PATHISH = re.compile(r"^[~./A-Za-z0-9_][\w./~-]*\.(?:md|py|json|toml|ya?ml|txt|sh|css|js|ts|html)$")

# Tabs in the UI, in the order they are drawn.
TABS = ("Skills", "Servers", "Rules")


@dataclass
class Attention:
    id: str
    tab: str
    kind: str            # skill | mcp | rules | source
    title: str
    detail: str
    fix: str
    source: str = ""


def _refs(text: str) -> list:
    """Path-shaped references inside a markdown document."""
    out = []
    for m in MD_LINK.finditer(text):
        t = m.group(1)
        if not t.startswith(("http:", "https:", "mailto:", "#")):
            out.append(t)
    for m in BACKTICK.finditer(text):
        t = m.group(1).strip()
        if PATHISH.match(t):
            out.append(t)
    return sorted(set(out))


def _missing(base: str, refs: list) -> list:
    """Refs that resolve nowhere — relative to the file, or from $HOME for a
    tilde path. A ref that resolves either way is fine."""
    out = []
    for r in refs:
        cands = [os.path.join(base, r)]
        if r.startswith("~"):
            cands = [os.path.expanduser(r)]
        elif r.startswith("/"):
            cands = [r]
        if not any(os.path.exists(c) for c in cands):
            out.append(r)
    return out


# ---------------------------------------------------------------------------

def _skills(st: State, reg: dict) -> list:
    out = []
    for key, rec in sorted((reg.get("skills") or {}).items()):
        d = rec.get("dir") or ""
        name = rec.get("name") or key
        if not os.path.isdir(d):
            out.append(Attention(
                key, "Skills", "source", name,
                "the directory it was imported from is gone: %s" % d,
                "python3 -m importer.cli detect", rec.get("source", "")))
            continue
        missing = rec.get("missing_refs") or []
        if missing:
            out.append(Attention(
                key, "Skills", "skill", name,
                "SKILL.md points at %d file%s that %s not there: %s"
                % (len(missing), "" if len(missing) == 1 else "s",
                   "is" if len(missing) == 1 else "are", ", ".join(missing[:3])),
                "restore the missing files under %s, then re-import" % d,
                rec.get("source", "")))
        absent = [p for p in (rec.get("pip") or [])
                  if importlib.util.find_spec(_mod(p)) is None]
        if absent:
            out.append(Attention(
                key + "#pip", "Skills", "skill", name,
                "requirements.txt names %d package%s this interpreter cannot "
                "import: %s" % (len(absent), "" if len(absent) == 1 else "s",
                                ", ".join(absent[:4])),
                "pip install -r %s" % os.path.join(d, "requirements.txt"),
                rec.get("source", "")))
    return out


def _mod(pkg: str) -> str:
    """pip name -> import name, for the cases a dash makes obvious."""
    return pkg.replace("-", "_").split("[")[0]


def _servers(st: State, reg: dict, home: str) -> list:
    flagged = _needs_auth_cache(home)
    out = []
    for key, rec in sorted((reg.get("mcp") or {}).items()):
        name = rec.get("name") or key
        if rec.get("auth") == "needs-auth" or name in flagged:
            why = rec.get("auth_why") or "the tool records an unfinished sign-in"
            need = rec.get("env_keys") or rec.get("header_keys") or []
            out.append(Attention(
                key, "Servers", "mcp", name,
                "configured but not authenticated — %s%s"
                % (why, (". Needs: " + ", ".join(need)) if need else ""),
                "authenticate %s in %s, then: python3 -m importer.cli import "
                "--source %s --apply" % (name, rec.get("source", "the owning tool"),
                                         rec.get("source", "")),
                rec.get("source", "")))
        cmd = (rec.get("command") or "").strip()
        # An absolute command path that no longer exists is a server that will
        # fail on first use; a bare name is resolved on PATH at run time and is
        # not something this check can honestly rule on.
        if cmd.startswith("/") and not os.path.exists(cmd):
            out.append(Attention(
                key + "#cmd", "Servers", "mcp", name,
                "its command is not on disk: %s" % cmd,
                "reinstall the server, or remove it from %s" % rec.get("source", ""),
                rec.get("source", "")))
    return out


def _rules(st: State, home: str) -> list:
    out = []
    for item_id, rec in sorted((st.ledger().get("items") or {}).items()):
        if rec.get("kind") != "rules":
            continue
        short = item_id.split("/", 1)[-1]
        path = os.path.expanduser(short) if short.startswith("~") else short
        if not os.path.isfile(path):
            out.append(Attention(
                item_id, "Rules", "source", rec.get("label") or short,
                "imported from %s, which is no longer there" % short,
                "python3 -m importer.cli detect", rec.get("source", "")))
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        missing = _missing(os.path.dirname(path), _refs(text))
        if missing:
            out.append(Attention(
                item_id, "Rules", "rules", rec.get("label") or short,
                "cites %d path%s that do%s not exist: %s"
                % (len(missing), "" if len(missing) == 1 else "s",
                   "es" if len(missing) == 1 else "", ", ".join(missing[:3])),
                "fix the reference in %s, then: python3 -m importer.cli "
                "import --source rules --apply" % short, rec.get("source", "")))
    return out


def attention(st: State = None, home: str = None) -> dict:
    st = st or State()
    home = home or HOME
    reg = st.registry()
    items = _skills(st, reg) + _servers(st, reg, home) + _rules(st, home)
    by_tab = {t: [asdict(a) for a in items if a.tab == t] for t in TABS}
    return {
        "total": len(items),
        "tabs": [{"name": t, "count": len(by_tab[t])} for t in TABS],
        "items": [asdict(a) for a in items],
        "by_tab": by_tab,
        "clean": not items and bool(st.ledger().get("items")),
        "imported_anything": bool(st.ledger().get("items")),
    }
