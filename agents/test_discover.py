"""Tests for agent-session adoption.

    python3 -m agents.test_discover

Every case builds its own store in a tempdir. Nothing here reads the developer's
real ~/.claude or ~/.codex — a test suite that passes only on the machine that
wrote it is not a test suite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time

from .discover import (scan_claude, scan_codex, scan_opencode, running,
                       _state_for, LIVE_SEC, RECENT_SEC, TAIL_BYTES,
                       _CLI, _HELPER, _DESKTOP)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------

def write_claude(root, slug, sid, records, mtime=None):
    d = os.path.join(root, slug); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_claude():
    print("\nclaude — jsonl sessions")
    with tempfile.TemporaryDirectory() as t:
        write_claude(t, "-Users-me-proj", "aaa", [
            {"type": "user", "cwd": "/Users/me/proj"},
            {"type": "assistant", "message": {"model": "claude-opus-5"}},
            {"type": "assistant", "message": {"model": "claude-opus-5"}},
            {"type": "system"},
            {"type": "user", "customTitle": "Fix the bracket"},
        ])
        s = scan_claude(t)
        check("finds one session", len(s) == 1, str(len(s)))
        g = s[0]
        check("reads cwd from the records", g.cwd == "/Users/me/proj", g.cwd)
        check("reads the model", g.model == "claude-opus-5", g.model)
        check("counts only user and assistant turns", g.messages == 4, str(g.messages))
        check("prefers the custom title", g.title == "Fix the bracket", g.title)
        check("project is the leaf directory", g.project == "proj", g.project)
        check("id is the file stem", g.id == "aaa", g.id)


def test_claude_slug_fallback_and_junk():
    print("\nclaude — degraded inputs")
    with tempfile.TemporaryDirectory() as t:
        write_claude(t, "-Users-me-thing", "bbb", [{"type": "system"}])
        s = scan_claude(t)
        check("falls back to the slug when no record carries cwd",
              s[0].cwd == "/Users/me/thing", s[0].cwd)

        d = os.path.join(t, "-tmp"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ccc.jsonl"), "w") as fh:
            fh.write("not json at all\n{\"type\":\"user\",\"cwd\":\"/tmp\"}\n{broken\n")
        s2 = scan_claude(t)
        check("a malformed line does not sink the scan", len(s2) == 2, str(len(s2)))
        got = [x for x in s2 if x.id == "ccc"][0]
        check("valid lines around the junk still parse", got.cwd == "/tmp", got.cwd)


def test_claude_tail_sampling():
    print("\nclaude — bounded read on a large session")
    with tempfile.TemporaryDirectory() as t:
        big = [{"type": "user", "cwd": "/Users/me/big", "pad": "x" * 400}
               for _ in range(1200)]
        p = write_claude(t, "-Users-me-big", "ddd", big)
        check("fixture exceeds the tail window", os.path.getsize(p) > TAIL_BYTES,
              str(os.path.getsize(p)))
        s = scan_claude(t)[0]
        check("still identifies the session", s.cwd == "/Users/me/big", s.cwd)
        check("declares that it sampled rather than counted", s.note == "tail-sampled", s.note)
        check("count is a sample, not the whole file",
              0 < s.messages < 1200, str(s.messages))


def test_codex():
    print("\ncodex — rollout files")
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "2026", "06", "08"); os.makedirs(d)
        p = os.path.join(d, "rollout-2026-06-08T14-48-22-019ea935.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-06-08T14:48:22Z", "type": "session_meta",
                                 "payload": {"id": "019ea935", "cwd": "/Users/me/api",
                                             "cli_version": "0.117.0",
                                             "model_provider": "openai",
                                             "originator": "codex_exec"}}) + "\n")
            for _ in range(3):
                fh.write(json.dumps({"type": "response_item", "payload": {}}) + "\n")
        s = scan_codex(t)
        check("finds the rollout", len(s) == 1, str(len(s)))
        g = s[0]
        check("id comes from session_meta", g.id == "019ea935", g.id)
        check("cwd comes from session_meta", g.cwd == "/Users/me/api", g.cwd)
        check("cli version is carried", g.version == "0.117.0", g.version)
        check("originator is kept as the note", g.note == "codex_exec", g.note)
        check("an empty tree yields nothing", scan_codex(os.path.join(t, "nope")) == [])


def make_opencode(path, rows, time_col="time_updated"):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE session (id text PRIMARY KEY, project_id text,
                   slug text, directory text, title text, version text,
                   cost real DEFAULT 0, tokens_input integer DEFAULT 0,
                   tokens_output integer DEFAULT 0, %s integer)""" % time_col)
    con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def test_opencode():
    print("\nopencode — sqlite, read-only")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "opencode.db")
        now_ms = int(time.time() * 1000)
        make_opencode(db, [("s1", "p1", "slugly", "/Users/me/web", "Refactor router",
                            "1.18.15", 0.42, 1000, 250, now_ms)])
        s = scan_opencode(db)
        check("finds the session", len(s) == 1, str(len(s)))
        g = s[0]
        check("title comes from the row", g.title == "Refactor router", g.title)
        check("tokens are read", (g.tokens_in, g.tokens_out) == (1000, 250),
              str((g.tokens_in, g.tokens_out)))
        check("cost is read", abs(g.cost_usd - 0.42) < 1e-9, str(g.cost_usd))
        check("millisecond stamps become seconds", abs(g.mtime - now_ms / 1000.0) < 2,
              str(g.mtime))
        check("a fresh row reads as live", g.state == "live", g.state)

        before = os.path.getmtime(db)
        scan_opencode(db); scan_opencode(db)
        check("scanning never writes to the database",
              os.path.getmtime(db) == before)
        check("and leaves no journal behind",
              not os.path.exists(db + "-wal") and not os.path.exists(db + "-shm"))
        check("a missing database is not an error", scan_opencode(os.path.join(t, "no.db")) == [])


def test_opencode_schema_drift():
    print("\nopencode — schema it does not recognise")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "o.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE session (id text, mystery text)")
        con.execute("INSERT INTO session VALUES ('x','y')"); con.commit(); con.close()
        s = scan_opencode(db)
        check("a session table with only id still yields a row", len(s) == 1, str(len(s)))
        db2 = os.path.join(t, "p.db")
        con = sqlite3.connect(db2); con.execute("CREATE TABLE other (a text)")
        con.commit(); con.close()
        check("no session table yields nothing rather than raising",
              scan_opencode(db2) == [])


def test_state():
    print("\nliveness")
    now = time.time()
    check("just touched is live", _state_for(now) == "live")
    check("inside the live window is live", _state_for(now - LIVE_SEC + 5) == "live")
    check("past it is recent", _state_for(now - LIVE_SEC - 5) == "recent")
    check("past the recent window is idle", _state_for(now - RECENT_SEC - 5) == "idle")


def test_process_matching():
    print("\nprocess matching — the desktop apps are not sessions")
    helpers = [
        "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Helpers/browser_crashpad_handler --monitor-self",
        "/Applications/ChatGPT.app/Contents/Frameworks/Codex (Renderer).app/Contents/MacOS/Codex (Renderer) --type=renderer",
        "/Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer) --type=renderer",
    ]
    for h in helpers:
        check("helper is filtered: %s" % h.split("/")[-1][:34], bool(_HELPER.search(h)))
    check("a bare cli binary matches", bool(_CLI.search("/Users/me/.local/bin/claude")))
    check("opencode with flags matches", bool(_CLI.search("opencode")))
    check("a lookalike path does not match", not _CLI.search("/usr/bin/claude-helper"))
    check("the Claude desktop binary matches its own pattern",
          bool(_DESKTOP["claude"].search("/Applications/Claude.app/Contents/MacOS/Claude")))
    check("running() returns both categories",
          set(running().keys()) == {"cli", "desktop"})


def main():
    print("agent adoption — test suite")
    test_claude()
    test_claude_slug_fallback_and_junk()
    test_claude_tail_sampling()
    test_codex()
    test_opencode()
    test_opencode_schema_drift()
    test_state()
    test_process_matching()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
