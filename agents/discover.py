"""
Adopt the coding-agent sessions already running on this machine.

Conductor spawns agents: it makes a git worktree and starts `claude` inside it,
so it owns every session it shows you. That is the easy direction. This is the
other one — the sessions already exist, started from a terminal hours ago, in
three different tools that agree on nothing, and the job is to find them and
put them behind one schema without touching them.

    ~/.claude/projects/<cwd-slug>/<uuid>.jsonl     JSONL, one record per event
    ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl   JSONL, session_meta header
    ~/.local/share/opencode/opencode.db            SQLite, a `session` table

Strictly read-only. Nothing here opens a file for writing, and the SQLite
connection is opened in immutable mode, because the owning tool may be mid-write
and corrupting a developer's real session history to populate a dashboard would
be indefensible.

    python3 -m agents.discover              human-readable
    python3 -m agents.discover --json       for the UI

Zero third-party dependencies; sqlite3 is stdlib.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, asdict, field

HOME = os.path.expanduser("~")

# A session touched inside this window is "live"; inside the second one it is
# "recent". Wall-clock on the session file is the honest signal — asking the
# process for its cwd needs lsof, which costs ~40ms per pid and lies inside
# worktrees anyway.
LIVE_SEC = 120
RECENT_SEC = 3600

# Reading whole files is not an option: a single Claude session in this repo is
# ~9,000 records. Header plus a bounded tail gives identity and current state.
TAIL_BYTES = 256 * 1024


@dataclass
class Session:
    vendor: str                  # claude | codex | opencode
    id: str
    title: str
    cwd: str
    model: str = ""
    version: str = ""
    messages: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    mtime: float = 0.0
    state: str = "idle"          # live | recent | idle
    project: str = ""
    note: str = ""

    def age_s(self) -> float:
        return max(0.0, time.time() - self.mtime)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _state_for(mtime: float) -> str:
    age = time.time() - mtime
    if age <= LIVE_SEC:
        return "live"
    if age <= RECENT_SEC:
        return "recent"
    return "idle"


def _tail_lines(path: str, limit: int = TAIL_BYTES) -> list[str]:
    """Last `limit` bytes as whole lines. The first (probably partial) line is
    dropped rather than handed to json.loads."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()
            raw = fh.read()
    except OSError:
        return []
    return raw.decode("utf-8", "replace").splitlines()


def _first_line(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.readline().decode("utf-8", "replace"))
    except Exception:
        return {}


def _shorten(cwd: str) -> str:
    return cwd.replace(HOME, "~", 1) if cwd.startswith(HOME) else cwd


# ---------------------------------------------------------------------------
# claude code
# ---------------------------------------------------------------------------

def scan_claude(root: str = None) -> list[Session]:
    root = root or os.path.join(HOME, ".claude", "projects")
    out = []
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        st = os.stat(path)
        sid = os.path.basename(path)[:-6]
        slug = os.path.basename(os.path.dirname(path))
        # The slug is cwd with separators replaced, which is lossy for any
        # directory containing a dash. Records carry the real cwd; fall back to
        # the slug only when none of them do.
        cwd, model, title, msgs = "", "", "", 0
        for line in _tail_lines(path):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cwd = d.get("cwd") or cwd
            title = d.get("customTitle") or title
            m = d.get("message")
            if isinstance(m, dict) and m.get("model"):
                model = m["model"]
            if d.get("type") in ("user", "assistant"):
                msgs += 1
        if not cwd:
            cwd = "/" + slug.lstrip("-").replace("-", "/")
        out.append(Session(
            vendor="claude", id=sid, title=title or _shorten(cwd),
            cwd=cwd, model=model, messages=msgs, mtime=st.st_mtime,
            state=_state_for(st.st_mtime), project=os.path.basename(cwd) or slug,
            note="tail-sampled" if st.st_size > TAIL_BYTES else ""))
    return out


# ---------------------------------------------------------------------------
# codex
# ---------------------------------------------------------------------------

def scan_codex(root: str = None) -> list[Session]:
    root = root or os.path.join(HOME, ".codex", "sessions")
    out = []
    for path in glob.glob(os.path.join(root, "*", "*", "*", "rollout-*.jsonl")):
        st = os.stat(path)
        meta = _first_line(path).get("payload") or {}
        sid = meta.get("id") or os.path.basename(path)
        cwd = meta.get("cwd", "")
        msgs = 0
        for line in _tail_lines(path):
            if '"type"' in line and ("response_item" in line or "message" in line):
                msgs += 1
        out.append(Session(
            vendor="codex", id=sid, title=_shorten(cwd) or sid,
            cwd=cwd, model=meta.get("model_provider", ""),
            version=meta.get("cli_version", ""), messages=msgs,
            mtime=st.st_mtime, state=_state_for(st.st_mtime),
            project=os.path.basename(cwd),
            note=meta.get("originator", "")))
    return out


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------

def scan_opencode(db: str = None) -> list[Session]:
    db = db or os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
    if not os.path.exists(db):
        return []
    # immutable=1: never take a lock, never write a -wal, never block the tool
    # that owns this file.
    uri = "file:%s?immutable=1&mode=ro" % db.replace("?", "%3f").replace("#", "%23")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error:
        return []
    out = []
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(session)")}
        want = ["id", "title", "directory", "version", "cost",
                "tokens_input", "tokens_output", "slug"]
        sel = [c for c in want if c in cols]
        if not sel:
            return []
        time_col = next((c for c in ("time_updated", "updated_at", "time_created")
                         if c in cols), None)
        q = "SELECT %s%s FROM session" % (", ".join(sel),
                                          (", " + time_col) if time_col else "")
        for row in con.execute(q):
            d = dict(zip(sel + ([time_col] if time_col else []), row))
            ts = d.get(time_col) or 0
            # opencode stamps milliseconds
            mt = float(ts) / 1000.0 if ts and float(ts) > 1e11 else float(ts or 0)
            if not mt:
                mt = os.path.getmtime(db)
            cwd = d.get("directory", "") or ""
            out.append(Session(
                vendor="opencode", id=str(d.get("id", "")),
                title=d.get("title") or d.get("slug") or "untitled",
                cwd=cwd, version=str(d.get("version", "")),
                tokens_in=int(d.get("tokens_input") or 0),
                tokens_out=int(d.get("tokens_output") or 0),
                cost_usd=float(d.get("cost") or 0.0),
                mtime=mt, state=_state_for(mt),
                project=os.path.basename(cwd)))
    except sqlite3.Error as exc:
        return [Session(vendor="opencode", id="-", title="unreadable",
                        cwd="", note=str(exc)[:60], mtime=time.time())]
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# running processes
# ---------------------------------------------------------------------------

# The desktop apps ship helper processes with "Codex" and "Claude" in the path
# (Electron renderers, crashpad handlers, a GPU process). Those are not agent
# sessions, and matching on the bare word finds dozens of them.
#
# Two categories, counted separately because they are genuinely different: a CLI
# process is one you started in a terminal, a desktop process is the app. A
# live session with zero CLI processes is not a contradiction — it is Claude
# Code running inside Claude.app, which is how this file got written.
_CLI = re.compile(r"(?:^|/)(claude|codex|opencode)$")
_HELPER = re.compile(r"(--type=|crashpad|Helpers/|\(Renderer\)|\(Service\)|\(GPU\))")
_DESKTOP = {"claude": re.compile(r"/Claude\.app/Contents/MacOS/Claude$"),
            "codex":  re.compile(r"/ChatGPT\.app/Contents/MacOS/ChatGPT$")}


def running() -> dict[str, dict[str, int]]:
    cli = {"claude": 0, "codex": 0, "opencode": 0}
    desktop = {"claude": 0, "codex": 0}
    try:
        ps = subprocess.run(["ps", "-Ao", "args="], capture_output=True,
                            text=True, timeout=5).stdout
    except Exception:
        return {"cli": cli, "desktop": desktop}
    for line in ps.splitlines():
        line = line.strip()
        if not line or _HELPER.search(line):
            continue
        head = line.split()[0]
        m = _CLI.search(head)
        if m:
            cli[m.group(1)] += 1
            continue
        for name, pat in _DESKTOP.items():
            if pat.search(line):
                desktop[name] += 1
    return {"cli": cli, "desktop": desktop}


# ---------------------------------------------------------------------------

def discover() -> dict:
    sessions = scan_claude() + scan_codex() + scan_opencode()
    sessions.sort(key=lambda s: s.mtime, reverse=True)
    procs = running()
    by_vendor = {}
    for s in sessions:
        by_vendor.setdefault(s.vendor, 0)
        by_vendor[s.vendor] += 1
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "processes": procs,
        "counts": by_vendor,
        "live": sum(1 for s in sessions if s.state == "live"),
        "sessions": [asdict(s) for s in sessions],
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--vendor", choices=("claude", "codex", "opencode"))
    a = ap.parse_args(argv)

    d = discover()
    rows = d["sessions"]
    if a.vendor:
        rows = [r for r in rows if r["vendor"] == a.vendor]

    if a.json:
        print(json.dumps(dict(d, sessions=rows[:a.limit]), indent=1))
        return 0

    cli, desk = d["processes"]["cli"], d["processes"]["desktop"]
    print("adopted sessions  —  %s" % d["generated"])
    print("  cli         claude %d   codex %d   opencode %d"
          % (cli["claude"], cli["codex"], cli["opencode"]))
    print("  desktop     claude %d   codex %d" % (desk["claude"], desk["codex"]))
    print("  sessions    %s   (%d live)"
          % ("  ".join("%s %d" % kv for kv in sorted(d["counts"].items())), d["live"]))
    print()
    print("  %-9s %-6s %-26s %-20s %6s %9s" %
          ("vendor", "state", "project", "model/version", "msgs", "tokens"))
    print("  " + "-" * 80)
    for r in rows[:a.limit]:
        tok = r["tokens_in"] + r["tokens_out"]
        print("  %-9s %-6s %-26s %-20s %6s %9s" % (
            r["vendor"], r["state"], (r["project"] or _shorten(r["cwd"]))[:26],
            (r["model"] or r["version"])[:20],
            r["messages"] or "-", "{:,}".format(tok) if tok else "-"))
    if not rows:
        print("  no agent sessions found on this machine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
