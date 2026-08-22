"""
What on this machine could Daisy import, and what would actually land.

The reference this is modelled on shows a list of detected apps and an Import
button. A list of names is the easy half; the half that decides whether the
button is safe to press is the one below it — for every source, the exact set
of files Daisy would write and what each write does. Nobody should click Import
to find out what Import does.

Session discovery is NOT reimplemented here. `agents/discover.py` already knows
the three on-disk session formats and reads them without taking a lock, so this
imports it. Everything else is new ground:

    rules       CLAUDE.md, AGENTS.md, CODEX.md, .cursor/rules
    mcp         ~/.claude.json, ~/.codex/config.toml, ~/.cursor/mcp.json
    skills      ~/.claude/skills, ~/.codex/skills, ~/.agents/skills, ~/.cursor/skills
    hooks       ~/.claude/settings.json
    cursor      ~/Library/Application Support/Cursor

Strictly read-only, like the module it builds on. Nothing here opens a file for
writing and nothing here reads a credential *value* into its output: an MCP
server reports which env keys and header names it carries and whether they are
populated, never what they contain, because this output ends up on a screen.

    python3 -m importer.cli detect
    python3 -m importer.cli detect --json

IS NOT a scan of the whole disk. Every location below is a documented config
path for a specific tool; there is no heuristic walk of the home directory,
because a detector that guesses will eventually offer to import someone's
private notes.

Zero third-party dependencies; tomllib is stdlib on 3.11+.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import discover                       # noqa: E402
from importer.state import ROOT, digest           # noqa: E402

try:
    import tomllib
except ImportError:                               # pragma: no cover - 3.10 and older
    tomllib = None

HOME = os.path.expanduser("~")

# Rules filenames every agent tool has converged on, plus Cursor's directory
# form. Order is the order they are listed in.
RULES_NAMES = ("CLAUDE.md", "AGENTS.md", "CODEX.md")

# An env or header entry whose *name* looks like a credential. Used to decide
# whether a server has anything to authenticate with — never to print a value.
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION|API", re.I)

# "${FOO}", "<your-key>", "YOUR_API_KEY", "changeme" — a value that is a
# reminder to fill something in rather than a credential.
PLACEHOLDER = re.compile(r"\$\{[^}]*\}|<[^>]+>|YOUR[_-]|CHANGE[_-]?ME|xxxx", re.I)

# Markdown link or bare backticked path inside a rules file / SKILL.md.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)\)")


@dataclass
class Effect:
    """One thing importing this source would do. The UI shows these verbatim."""
    action: str          # append | register | summarise | record
    target: str          # file inside Daisy's import state
    detail: str


@dataclass
class Item:
    """One importable thing inside a source."""
    key: str
    label: str
    detail: str = ""
    digest: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class Source:
    id: str
    tool: str            # claude | codex | opencode | cursor | agents
    kind: str            # sessions | rules | mcp | skills | hooks | app
    label: str
    path: str
    present: bool
    items: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    note: str = ""
    importable: bool = True

    @property
    def count(self) -> int:
        return len(self.items)


def _short(p: str) -> str:
    return p.replace(HOME, "~", 1) if p.startswith(HOME) else p


def _stat_digest(path: str) -> str:
    """Digest a file by content. Rules and skills are small; hashing the bytes
    beats hashing the mtime, which changes when nothing did (a `touch`, a
    checkout) and would make a resync move files that are identical."""
    try:
        with open(path, "rb") as fh:
            return digest(fh.read())
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# sessions — delegated to agents/discover.py
# ---------------------------------------------------------------------------

_SESSION_SCAN = {
    "claude":   (".claude/projects", "Claude Code", discover.scan_claude),
    "codex":    (".codex/sessions", "Codex", discover.scan_codex),
    "opencode": (".local/share/opencode/opencode.db", "OpenCode", discover.scan_opencode),
}


def sessions(vendor: str, home: str = None, root: str = None) -> Source:
    where, label, scan = _SESSION_SCAN[vendor]
    path = root or os.path.join(home or HOME, *where.split("/"))
    found = []
    try:
        found = scan(path)
    except Exception as exc:                       # a corrupt store is not fatal
        return Source(vendor + "-sessions", vendor, "sessions",
                      label + " sessions", _short(path), False,
                      note="unreadable: %s" % str(exc)[:60])
    items = []
    for s in found:
        # The digest covers what a summary would actually say. A session that
        # gained turns re-imports; one that was merely re-opened does not.
        items.append(Item(
            key=s.id,
            label=s.title or s.project or s.id,
            detail="%s · %s turns" % (s.project or "-", s.messages),
            digest=digest(s.id, s.messages, s.tokens_in, s.tokens_out, round(s.mtime)),
            payload={"vendor": s.vendor, "id": s.id, "title": s.title, "cwd": s.cwd,
                     "project": s.project, "model": s.model or s.version,
                     "messages": s.messages, "tokens_in": s.tokens_in,
                     "tokens_out": s.tokens_out, "cost_usd": s.cost_usd,
                     "mtime": s.mtime}))
    items.sort(key=lambda i: -i.payload.get("mtime", 0))
    return Source(
        vendor + "-sessions", vendor, "sessions", label + " sessions",
        _short(path), bool(items), items,
        [Effect("summarise", "runs.json",
                "one run-history row per session: project, model, turns, tokens, "
                "last touched. The transcript is not copied.")],
        note="read-only; the owning tool keeps the transcript")


# ---------------------------------------------------------------------------
# rules files
# ---------------------------------------------------------------------------

def _cursor_rules(base: str) -> list[str]:
    """.cursor/rules is a directory of .mdc files in current Cursor, and was a
    single file in older versions. Both still exist in the wild."""
    d = os.path.join(base, ".cursor", "rules")
    if os.path.isdir(d):
        return sorted(glob.glob(os.path.join(d, "*.mdc")) + glob.glob(os.path.join(d, "*.md")))
    return [d] if os.path.isfile(d) else []


def rules(project: str = None, home: str = None) -> Source:
    project = project or ROOT
    home = home or HOME
    seen, items = set(), []
    # Project rules first: they are the ones a run in this directory obeys.
    for base, scope in ((project, "project"), (home, "global"),
                        (os.path.join(home, ".claude"), "global"),
                        (os.path.join(home, ".codex"), "global")):
        cands = [os.path.join(base, n) for n in RULES_NAMES] + _cursor_rules(base)
        for p in cands:
            if not os.path.isfile(p) or p in seen:
                continue
            seen.add(p)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            items.append(Item(
                key=_short(p),
                label=os.path.basename(p),
                detail="%s · %d lines" % (scope, text.count("\n") + 1),
                digest=digest(text),
                payload={"path": p, "scope": scope, "text": text,
                         "lines": text.count("\n") + 1}))
    return Source(
        "rules", "mixed", "rules", "Project rules files",
        _short(project), bool(items), items,
        [Effect("append", "config.md",
                "each file lands as its own delimited block, appended. Existing "
                "text is never rewritten; a changed file appends a revision "
                "block and both are kept."),
         Effect("record", "ledger.json", "path and content digest, so a resync "
                "can tell an edit from a re-read.")],
        note="merged with a diff you see before it is written")


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

def _needs_auth_cache(home: str) -> set:
    """Claude Code keeps its own list of servers whose OAuth has not completed.
    Reading the tool's own answer beats inferring one."""
    try:
        d = json.load(open(os.path.join(home, ".claude", "mcp-needs-auth-cache.json"),
                           encoding="utf-8"))
        return set(d) if isinstance(d, dict) else set(d or [])
    except (OSError, ValueError):
        return set()


def _auth_state(name: str, cfg: dict, flagged: set) -> tuple[str, str]:
    """Authenticated, or configured-but-not? Returns (state, why).

    Values are inspected but never returned: the caller gets "populated" or
    "empty", which is all the UI needs and all a screenshot should carry.
    """
    if name in flagged:
        return "needs-auth", "the tool records an unfinished sign-in"
    env = dict(cfg.get("env") or {})
    hdr = dict(cfg.get("headers") or cfg.get("http_headers") or {})
    remote = bool(cfg.get("url")) or cfg.get("type") in ("http", "sse")
    creds = {k: v for k, v in list(env.items()) + list(hdr.items()) if SECRETISH.search(k)}
    if not creds:
        # A remote server with no credential material is waiting on an OAuth
        # round trip; a local stdio one simply has nothing to authenticate.
        return ("needs-auth", "remote server with no credential configured") if remote \
            else ("n/a", "local process, no credential needed")
    for k, v in creds.items():
        s = str(v or "")
        if not s:
            return "needs-auth", "%s is empty" % k
        if PLACEHOLDER.search(s):
            var = re.search(r"\$\{([^}]+)\}", s)
            if var and os.environ.get(var.group(1)):
                continue
            return "needs-auth", "%s is still a placeholder" % k
    return "ok", "carries " + ", ".join(sorted(creds))


def _mcp_item(name: str, cfg: dict, origin: str, flagged: set) -> Item:
    state, why = _auth_state(name, cfg, flagged)
    transport = "http" if (cfg.get("url") or cfg.get("type") in ("http", "sse")) else "stdio"
    env_keys = sorted((cfg.get("env") or {}).keys())
    hdr_keys = sorted((cfg.get("headers") or cfg.get("http_headers") or {}).keys())
    return Item(
        key=name,
        label=name,
        detail="%s · %s" % (transport, why),
        digest=digest(json.dumps(cfg, sort_keys=True, default=str)),
        payload={"name": name, "transport": transport, "origin": origin,
                 "command": cfg.get("command", ""), "url": cfg.get("url", ""),
                 "args": list(cfg.get("args") or []),
                 "env_keys": env_keys, "header_keys": hdr_keys,
                 "auth": state, "auth_why": why})


_MCP_EFFECTS = [
    Effect("register", "registry.json",
           "name, transport, command or URL, and the names of the env vars and "
           "headers it needs. No credential value is copied — Daisy records that "
           "a key is required, not what it is."),
    Effect("record", "ledger.json", "so a server that changes config is spotted "
           "on the next sync instead of silently diverging."),
]


def mcp_claude(home: str = None) -> Source:
    home = home or HOME
    p = os.path.join(home, ".claude.json")
    flagged = _needs_auth_cache(home)
    items = []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if d:
        for name, cfg in sorted((d.get("mcpServers") or {}).items()):
            items.append(_mcp_item(name, cfg, "global", flagged))
        # Project-scoped servers keep their own namespace: two projects may
        # legitimately define "github" differently and collapsing them would
        # invent a config neither project has.
        for proj, pcfg in sorted((d.get("projects") or {}).items()):
            for name, cfg in sorted((pcfg.get("mcpServers") or {}).items()):
                it = _mcp_item(name, cfg, _short(proj), flagged)
                it.key = "%s@%s" % (name, os.path.basename(proj.rstrip("/")) or "root")
                items.append(it)
    return Source("claude-mcp", "claude", "mcp", "Claude Code MCP servers",
                  _short(p), bool(items), items, list(_MCP_EFFECTS))


def mcp_codex(home: str = None) -> Source:
    home = home or HOME
    p = os.path.join(home, ".codex", "config.toml")
    items, note = [], ""
    if tomllib is None:
        note = "needs Python 3.11+ for tomllib"
    else:
        try:
            with open(p, "rb") as fh:
                d = tomllib.load(fh)
            for name, cfg in sorted((d.get("mcp_servers") or {}).items()):
                items.append(_mcp_item(name, cfg, "global", set()))
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            note = str(exc)[:70]
    return Source("codex-mcp", "codex", "mcp", "Codex MCP servers",
                  _short(p), bool(items), items, list(_MCP_EFFECTS), note=note)


def mcp_cursor(home: str = None) -> Source:
    home = home or HOME
    p = os.path.join(home, ".cursor", "mcp.json")
    items = []
    try:
        d = json.load(open(p, encoding="utf-8"))
        for name, cfg in sorted((d.get("mcpServers") or {}).items()):
            items.append(_mcp_item(name, cfg, "global", set()))
    except (OSError, ValueError):
        pass
    return Source("cursor-mcp", "cursor", "mcp", "Cursor MCP servers",
                  _short(p), bool(items), items, list(_MCP_EFFECTS))


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

def _skill_refs(md_dir: str, text: str) -> list[str]:
    """Relative paths a SKILL.md points at. A skill whose references moved is
    the most common half-installed skill, and it is cheap to check."""
    out = []
    for m in MD_LINK.finditer(text):
        t = m.group(1)
        if t.startswith(("http:", "https:", "mailto:", "/")):
            continue
        # `[see the docs](link)` is prose about links, not a link. A reference
        # only counts if it is shaped like a path.
        if "/" not in t and "." not in t:
            continue
        out.append(t)
    return sorted(set(out))


def _skill_item(d: str) -> Item:
    md = os.path.join(d, "SKILL.md")
    name = os.path.basename(d)
    try:
        text = open(md, encoding="utf-8", errors="replace").read()
    except OSError:
        text = ""
    desc = ""
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    if m:
        desc = m.group(1).strip().strip("\"'")
    refs = _skill_refs(d, text)
    missing = [r for r in refs if not os.path.exists(os.path.join(d, r))]
    reqs = os.path.join(d, "requirements.txt")
    pip = []
    if os.path.isfile(reqs):
        for ln in open(reqs, encoding="utf-8", errors="replace"):
            ln = ln.split("#")[0].strip()
            # `-e .` and `--index-url ...` are pip flags, not packages.
            if ln and not ln.startswith("-"):
                pip.append(re.split(r"[<>=\[; ]", ln)[0])
    return Item(
        key=name, label=name, detail=(desc[:90] or "no description"),
        digest=digest(_stat_digest(md), sorted(os.listdir(d))),
        payload={"name": name, "dir": d, "description": desc,
                 "refs": refs, "missing_refs": missing, "pip": pip,
                 "has_skill_md": os.path.isfile(md)})


_SKILL_ROOTS = {
    "claude-skills": ("claude", ".claude/skills", "Claude Code skills"),
    "codex-skills":  ("codex", ".codex/skills", "Codex skills"),
    "agents-skills": ("agents", ".agents/skills", "Shared agent skills"),
    "cursor-skills": ("cursor", ".cursor/skills", "Cursor skills"),
}


def skills(source_id: str, home: str = None, root: str = None) -> Source:
    tool, rel, label = _SKILL_ROOTS[source_id]
    base = root or os.path.join(home or HOME, *rel.split("/"))
    items = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md")):
                items.append(_skill_item(d))
    return Source(
        source_id, tool, "skills", label, _short(base), bool(items), items,
        [Effect("register", "registry.json",
                "name, description, and the directory it is mounted from. The "
                "files stay where they are — Daisy points at them, it does not "
                "copy them."),
         Effect("record", "ledger.json",
                "with a digest over SKILL.md and the file list, so an edited "
                "skill is re-registered and an untouched one is not.")],
        note="a skill with a missing reference lands in Needs attention")


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------

def hooks(home: str = None) -> Source:
    home = home or HOME
    p = os.path.join(home, ".claude", "settings.json")
    items = []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    for event, groups in sorted((d.get("hooks") or {}).items()):
        for gi, g in enumerate(groups if isinstance(groups, list) else []):
            for hi, h in enumerate(g.get("hooks") or []):
                cmd = str(h.get("command", ""))
                items.append(Item(
                    key="%s/%d/%d" % (event, gi, hi),
                    label=event,
                    detail=(g.get("matcher") or "*") + " · " + os.path.basename(cmd.split()[0] if cmd else "-"),
                    digest=digest(event, g.get("matcher", ""), cmd),
                    payload={"event": event, "matcher": g.get("matcher", ""),
                             "command": cmd, "type": h.get("type", "command")}))
    return Source(
        "claude-hooks", "claude", "hooks", "Claude Code hooks", _short(p),
        bool(items), items,
        [Effect("record", "registry.json",
                "the event, the matcher and the command line are listed so you "
                "can see what the other tool runs."),
         Effect("record", "ledger.json", "one entry per hook.")],
        note="listed, never installed — Daisy will not wire another tool's shell "
             "commands into its own run loop on a button press")


# ---------------------------------------------------------------------------
# cursor — the app, as opposed to its config files
# ---------------------------------------------------------------------------

def cursor_app(home: str = None) -> Source:
    home = home or HOME
    base = os.path.join(home, "Library", "Application Support", "Cursor")
    present = os.path.isdir(base)
    ws = os.path.join(base, "User", "workspaceStorage")
    n = len(os.listdir(ws)) if os.path.isdir(ws) else 0
    db = os.path.join(base, "User", "globalStorage", "state.vscdb")
    size = os.path.getsize(db) if os.path.isfile(db) else 0
    installed = any(os.path.isdir(p) for p in
                    ("/Applications/Cursor.app",
                     os.path.join(home, "Applications", "Cursor.app")))
    detail = "%d workspaces" % n
    if size:
        detail += " · %.0f MB chat store" % (size / 1e6)
    if not installed and present:
        detail += " · app not installed, data left behind"
    return Source(
        "cursor", "cursor", "app", "Cursor", _short(base), present,
        [Item("workspaces", "workspace storage", detail, digest(n, size))] if present else [],
        [], importable=False,
        note="chats live in a VS Code state.vscdb key/value blob store, not a "
             "documented session format. Daisy imports Cursor's rules, MCP "
             "servers and skills and stops there rather than reverse-engineering "
             "a binary blob it cannot read reliably.")


# ---------------------------------------------------------------------------

def detect(home: str = None, project: str = None) -> dict:
    home = home or HOME
    srcs = [
        sessions("claude", home), sessions("codex", home), sessions("opencode", home),
        rules(project, home),
        mcp_claude(home), mcp_codex(home), mcp_cursor(home),
        skills("claude-skills", home), skills("codex-skills", home),
        skills("agents-skills", home), skills("cursor-skills", home),
        hooks(home),
        cursor_app(home),
    ]
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "home": _short(home), "project": _short(project or ROOT),
            "sources": [asdict(s) | {"count": s.count} for s in srcs]}


def by_id(det: dict, source_id: str) -> dict:
    for s in det["sources"]:
        if s["id"] == source_id:
            return s
    return {}
