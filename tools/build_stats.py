#!/usr/bin/env python3
"""Compute REAL precedent numbers and inject them into the UI.

The Precedent view in Daisy shows measured values from the actual engine —
not hand-written figures. Run this after changing the engine or reseeding.
"""

import json
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precedent.engine import Precedent, GateResult, embed, fingerprint, BYTES, DIM
from precedent.compact import compact

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "precedent", "precedent.db")
INDEX = os.path.join(ROOT, "index.html")


def bench_recall(p, reps=9):
    q = "the physics check says my bracket bends too much, stress is over allowable"
    g = [GateResult("physics.bend", False, 0.78)]
    p.recall(q, g, k=3)
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); p.recall(q, g, k=3); ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)


def bench_fingerprint(reps=400):
    q = "physics gate failed: sigma 212 MPa exceeds 172 MPa allowable at bracket.py:88"
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); fingerprint(q); ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)


def bench_compaction():
    random.seed(7)
    events = []
    for i in range(240):
        r = random.random()
        if r < .34:
            events.append({"kind": "tool", "text": "ran vitest on runs/1042/web-api — 18 passed, 0 failed"})
        elif r < .52:
            events.append({"kind": "read", "path": "runs/1042/web-api/lib/contract-guard.ts", "text": "read contract-guard.ts"})
        elif r < .62:
            events.append({"kind": "text", "text": "Checking the contract for endpoint %d before wiring the handler." % i})
        elif r < .70:
            events.append({"kind": "text", "text": ""})
        elif r < .78:
            events.append({"kind": "error", "id": "e%d" % i,
                           "text": "TypeError: cannot read property\n" + "\n".join("  at frame %d" % k for k in range(22))})
        else:
            events.append({"kind": "tool", "text": "npm run build — compiled in 4.2s"})
    events += [
        {"kind": "diff", "files": ["runs/1042/web-api/app/api/parts/route.ts",
                                   "runs/1042/web-frontend/styles/tokens.css"]},
        {"kind": "gate", "name": "taste.t1", "passed": False, "margin": 0.4},
        {"kind": "repair", "fixes": "taste.t1", "by": "resume-findings"},
        {"kind": "gate", "name": "physics.bend", "passed": False, "margin": 0.82},
        {"kind": "repair", "fixes": "physics.bend", "by": "algebra"},
        {"kind": "approval", "what": "merge run/1042", "who": "rishith"},
        {"kind": "next", "text": "record the F-11 precedent"},
    ]
    random.shuffle(events)
    t = time.perf_counter()
    e = compact("1042", events)
    ms = (time.perf_counter() - t) * 1000
    return e, ms


def main():
    p = Precedent(DB)
    fams = p.families()
    seen = sum(f["seen"] for f in fams)
    fixed = sum(f["fixed"] for f in fams)
    ess, cms = bench_compaction()

    stats = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "cases": p.count(),
        "families": len(fams),
        "fix_rate": round(100.0 * fixed / max(1, seen), 1),
        "recall_ms": round(bench_recall(p), 1),
        "fingerprint_ms": round(bench_fingerprint(), 3),
        "index_kb": round(BYTES * p.count() / 1024),
        "float_mb": round(DIM * 4 * p.count() / 1e6, 1),
        "quant_x": (DIM * 4) // BYTES,
        "db_mb": round(os.path.getsize(DB) / 1e6, 1),
        "compact_before": ess.bytes_before,
        "compact_after": ess.bytes_after,
        "compact_x": round(ess.ratio),
        "compact_ms": round(cms),
        "probe_score": round(ess.probe_score * 100),
        "probe_n": len(ess.probes),
        "top_families": [
            {"family": f["family"], "seen": f["seen"], "fixed": f["fixed"],
             "how": max(f["fix_kinds"], key=f["fix_kinds"].get) if f["fix_kinds"] else "-",
             "sig": ", ".join(f["signature"][:2])}
            for f in fams[:6]
        ],
    }

    out = os.path.join(ROOT, "precedent", "stats.json")
    with open(out, "w") as fh:
        json.dump(stats, fh, indent=2)

    html = open(INDEX).read()
    marker = "var PRECEDENT_STATS = "
    blob = marker + json.dumps(stats, separators=(",", ":")) + ";"
    if marker in html:
        import re
        html = re.sub(re.escape(marker) + r".*?;\n", blob + "\n", html, count=1, flags=re.S)
    else:
        html = html.replace('  "use strict";', '  "use strict";\n  ' + blob, 1)
    open(INDEX, "w").write(html)

    print("precedent stats -> stats.json + index.html")
    for k in ("cases", "families", "fix_rate", "recall_ms", "fingerprint_ms",
              "index_kb", "float_mb", "quant_x", "compact_x", "probe_score"):
        print("  %-16s %s" % (k, stats[k]))


if __name__ == "__main__":
    main()
