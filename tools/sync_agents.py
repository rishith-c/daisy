#!/usr/bin/env python3
"""Bake a fallback snapshot of adopted agent sessions into index.html.

The Mac app asks Swift for live data on every launch. A browser cannot — so a
small snapshot is baked in as the fallback, and the UI labels which one it is
showing rather than passing stale data off as live.

Only the leaf directory name is kept, never the full path: the snapshot ships in
a file that may end up on GitHub, and the shape of someone's home directory is
nobody else's business.

    python3 tools/sync_agents.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.discover import discover  # noqa: E402

IDX = os.path.join(ROOT, "index.html")
KEEP = 6
MARK = "var ADOPTED_SNAPSHOT = "


def main():
    d = discover()
    rows = []
    for s in d["sessions"][:KEEP]:
        rows.append({
            "vendor": s["vendor"], "state": s["state"],
            "project": s["project"] or "—",
            "model": s["model"] or s["version"] or "",
            "messages": s["messages"], "tokens": s["tokens_in"] + s["tokens_out"],
        })
    snap = {"generated": d["generated"], "counts": d["counts"],
            "live": d["live"], "processes": d["processes"],
            "total": len(d["sessions"]), "sessions": rows}

    h = open(IDX, encoding="utf-8").read()
    start = h.index(MARK) + len(MARK)
    end = h.index("\n", start)
    h = h[:start] + json.dumps(snap) + ";" + h[end:]
    open(IDX, "w", encoding="utf-8").write(h)

    print("snapshot baked: %d sessions across %s, %d live"
          % (snap["total"], ", ".join(snap["counts"]), snap["live"]))
    for r in rows:
        print("  %-9s %-6s %-22s %s" % (r["vendor"], r["state"], r["project"], r["model"]))


if __name__ == "__main__":
    main()
