#!/usr/bin/env python3
"""
Advisory path claims, for two agents sharing one working tree.

Claude and Codex are editing the same files on the same disk at the same time.
Git branches do not help with that — both processes cd into the same directory,
so there is one set of bytes and whoever writes last wins.

The claims here are deliberately **advisory**. Nothing enforces them, and a
claim expires on its own after 45 minutes. A hard lock is the wrong shape for
agents: they crash, they hit session limits, they get killed mid-edit, and a
lock that outlives the process holding it turns one stalled agent into two.
An expiring note that says "someone was working here 3 minutes ago" carries
almost all of the value and none of the deadlock.

    python3 tools/claim.py take    lab/run.py --as codex
    python3 tools/claim.py list
    python3 tools/claim.py release lab/run.py --as codex
    python3 tools/claim.py check   lab/run.py

Exit codes: 0 clear or yours, 1 held by someone else. Zero dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCKS = os.path.join(ROOT, ".agents", "locks")
TTL = 45 * 60


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", path.strip("/")) + ".lock"


def _read(path: str):
    f = os.path.join(LOCKS, _slug(path))
    try:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if time.time() - d.get("at", 0) > TTL:
        try:
            os.remove(f)                 # expired claims clean themselves up
        except OSError:
            pass
        return None
    return d


def take(path: str, who: str, why: str = "") -> int:
    os.makedirs(LOCKS, exist_ok=True)
    cur = _read(path)
    if cur and cur.get("by") != who:
        age = int(time.time() - cur["at"])
        print(json.dumps({"ok": False, "path": path, "held_by": cur["by"],
                          "age_s": age, "why": cur.get("why", ""),
                          "note": "still warm — talk to %s or wait %d min"
                                  % (cur["by"], max(0, (TTL - age) // 60))}))
        return 1
    d = {"by": who, "at": time.time(), "path": path, "why": why,
         "pid": os.getpid()}
    with open(os.path.join(LOCKS, _slug(path)), "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    print(json.dumps({"ok": True, "path": path, "by": who}))
    return 0


def release(path: str, who: str) -> int:
    cur = _read(path)
    if cur and cur.get("by") != who:
        print(json.dumps({"ok": False, "path": path, "held_by": cur["by"],
                          "note": "not yours to release"}))
        return 1
    try:
        os.remove(os.path.join(LOCKS, _slug(path)))
    except OSError:
        pass
    print(json.dumps({"ok": True, "released": path}))
    return 0


def listing() -> int:
    os.makedirs(LOCKS, exist_ok=True)
    rows = []
    for name in sorted(os.listdir(LOCKS)):
        if not name.endswith(".lock"):
            continue
        try:
            d = json.load(open(os.path.join(LOCKS, name), encoding="utf-8"))
        except ValueError:
            continue
        age = time.time() - d.get("at", 0)
        if age > TTL:
            continue
        rows.append({"path": d.get("path"), "by": d.get("by"),
                     "age_min": round(age / 60, 1), "why": d.get("why", "")})
    if not rows:
        print("no live claims")
        return 0
    print("%-42s %-8s %7s  %s" % ("path", "by", "age", "why"))
    print("-" * 78)
    for r in rows:
        print("%-42s %-8s %6.1fm  %s" % (r["path"][:42], r["by"], r["age_min"], r["why"][:24]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("take", "release", "check"):
        p = sub.add_parser(name)
        p.add_argument("path")
        p.add_argument("--as", dest="who", default=os.environ.get("AGENT_NAME", ""))
        if name == "take":
            p.add_argument("--why", default="")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        return listing()
    if a.cmd == "check":
        cur = _read(a.path)
        print(json.dumps(cur or {"ok": True, "path": a.path, "held_by": None}))
        return 1 if (cur and cur.get("by") != a.who) else 0
    if not a.who:
        print("--as <name> required (or set AGENT_NAME)")
        return 2
    return take(a.path, a.who, a.why) if a.cmd == "take" else release(a.path, a.who)


if __name__ == "__main__":
    raise SystemExit(main())
