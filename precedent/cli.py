"""Terminal interface to the factory's case law.

    python3 -m precedent.cli stats
    python3 -m precedent.cli families
    python3 -m precedent.cli recall "bracket bends too much" --gate physics.bend=0.78
    python3 -m precedent.cli sql "SELECT family, COUNT(*) n FROM cases GROUP BY family"
    python3 -m precedent.cli bench
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

from .engine import Precedent, GateResult, embed, fingerprint, pack_bits, hamming, BYTES, DIM

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "precedent.db")


def _gates(specs: list[str]) -> list[GateResult]:
    out = []
    for s in specs or []:
        if "=" in s:
            name, margin = s.split("=", 1)
            out.append(GateResult(name, False, float(margin)))
        else:
            out.append(GateResult(s, False, None))
    return out


def cmd_stats(a) -> None:
    p = Precedent(a.db)
    fams = p.families()
    size = os.path.getsize(a.db)
    seen = sum(f["seen"] for f in fams)
    fixed = sum(f["fixed"] for f in fams)
    print("archived runs      %d" % p.count())
    print("failure families   %d" % len(fams))
    print("repeat-fix rate    %.1f%%" % (100.0 * fixed / max(1, seen)))
    print("archive on disk    %.1f MB" % (size / 1e6))
    print("index footprint    %d B/case  (%d-d binary)  = %.0f KB total"
          % (BYTES, DIM, BYTES * p.count() / 1024))
    print("float rescore      %d B/case  = %.1f MB   (%dx larger)"
          % (DIM * 4, DIM * 4 * p.count() / 1e6, (DIM * 4) // BYTES))


def cmd_families(a) -> None:
    p = Precedent(a.db)
    print("%-24s %6s %6s  %s" % ("family", "seen", "fixed", "how"))
    for f in p.families():
        how = ", ".join("%s x%d" % (k, v) for k, v in sorted(f["fix_kinds"].items(), key=lambda kv: -kv[1]))
        print("%-24s %6d %6d  %s" % (f["family"][:24], f["seen"], f["fixed"], how or "-"))


def cmd_recall(a) -> None:
    p = Precedent(a.db)
    t0 = time.time()
    hits = p.recall(a.query, _gates(a.gate), k=a.k, min_score=a.min)
    dt = (time.time() - t0) * 1000
    if not hits:
        print("no precedent — the factory has not seen this before  (%.1f ms)" % dt)
        print("this is a valid answer; do not fabricate a match")
        return
    print("%d cited  ·  %.1f ms  ·  tier=%s  ·  0 tokens\n" % (len(hits), dt, hits[0].tier))
    for h in hits:
        c = h.case
        print("  run %s   sim %.3f   %s   [%s]" % (c.run_id, h.score, c.family, c.fix_kind))
        if h.parts:
            print("      %s" % h.parts)
        print("      %s" % c.narrative[:104])
        print("      fix: %s\n" % (c.fix[:104] or "(unresolved)"))


def cmd_sql(a) -> None:
    p = Precedent(a.db)
    try:
        rows = p.sql(a.query)
    except ValueError as e:
        print("refused: %s" % e); sys.exit(2)
    if not rows:
        print("(no rows)"); return
    cols = list(rows[0].keys())
    print(" | ".join(cols))
    print("-+-".join("-" * len(c) for c in cols))
    for r in rows:
        print(" | ".join(str(r[c])[:48] for c in cols))


def cmd_bench(a) -> None:
    p = Precedent(a.db)
    q = "the physics check says my bracket bends too much, stress is over allowable"
    g = [GateResult("physics.bend", False, 0.78)]
    p.recall(q, g, k=3)

    fp = []
    for _ in range(200):
        t = time.perf_counter(); fingerprint(q); fp.append((time.perf_counter() - t) * 1000)
    em = []
    for _ in range(200):
        t = time.perf_counter(); embed(q); em.append((time.perf_counter() - t) * 1000)
    rc = []
    for _ in range(15):
        t = time.perf_counter(); p.recall(q, g, k=3); rc.append((time.perf_counter() - t) * 1000)

    v = embed(q); b = pack_bits(v)
    hm = []
    for _ in range(2000):
        t = time.perf_counter(); hamming(b, b); hm.append((time.perf_counter() - t) * 1e6)

    print("cases            %d" % p.count())
    print("fingerprint      %.3f ms   (tier 0, exact)" % statistics.median(fp))
    print("embed            %.3f ms" % statistics.median(em))
    print("hamming          %.1f us   (per comparison)" % statistics.median(hm))
    print("hybrid recall    %.1f ms   (tiers 1-3 fused)" % statistics.median(rc))
    print("index size       %.0f KB   vs %.1f MB float  (%dx)"
          % (BYTES * p.count() / 1024, DIM * 4 * p.count() / 1e6, (DIM * 4) // BYTES))


def main() -> None:
    ap = argparse.ArgumentParser(prog="precedent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats").set_defaults(f=cmd_stats)
    sub.add_parser("families").set_defaults(f=cmd_families)
    sub.add_parser("bench").set_defaults(f=cmd_bench)

    r = sub.add_parser("recall"); r.set_defaults(f=cmd_recall)
    r.add_argument("query")
    r.add_argument("--gate", action="append", help="name=margin, repeatable")
    r.add_argument("-k", type=int, default=3)
    r.add_argument("--min", type=float, default=0.0)

    s = sub.add_parser("sql"); s.set_defaults(f=cmd_sql)
    s.add_argument("query")

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
