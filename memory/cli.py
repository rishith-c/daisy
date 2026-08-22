"""Terminal interface to Super Memory.

    python3 -m memory.cli ingest --runs runs --sessions 2
    python3 -m memory.cli recall "which file did the taste gate reject"
    python3 -m memory.cli forgotten "bracket web thickness"
    python3 -m memory.cli audit
    python3 -m memory.cli stats

`recall` prints two sections and never merges them: what is still held, and what
was compacted away with the pointer needed to get it back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import boundary, ingest, recall as rc, store  # noqa: E402

DB = store.DEFAULT_DB
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def cmd_ingest(a) -> int:
    con = store.connect(a.db)
    done = []
    if a.runs:
        done += ingest.ingest_runs(con, a.runs if os.path.isabs(a.runs)
                                   else os.path.join(ROOT, a.runs))
    if a.session:
        done.append(ingest.ingest_session(con, a.session, max_bytes=a.max_bytes))
    if a.sessions:
        for s, path in ingest.sessions(limit=a.sessions):
            done.append(ingest.ingest_session(con, path, max_bytes=a.max_bytes))
    if not done:
        print("nothing to ingest — pass --runs, --session or --sessions")
        return 2

    for d in done:
        print("%-34s %5d new  %5d dup  %5d facts" %
              (d["source"][:34], d["events"], d["duplicates"], d["facts"]))
    if not a.no_compact:
        print()
        for d in done:
            b = boundary.compact_span(con, d["run_id"], d["source"])
            if not b["summary_id"]:
                continue
            print("compacted %-24s %4d events -> %d retained, %d residue  "
                  "(x%.1f, probe %.0f%%%s)"
                  % (d["source"][:24], b["events"], b["retained"], b["dropped"],
                     b["ratio"], 100 * b["probe_score"],
                     ", retried" if b["retried"] else ""))
    con.close()
    return 0


def cmd_recall(a) -> int:
    con = store.connect(a.db)
    r = rc.recall(con, a.query, gates=a.gate, k=a.k)
    if r.empty():
        print("nothing above the evidence floor (%.2f) — this memory does not hold it"
              % rc.EVIDENCE_FLOOR)
        print("%d rows scanned in %.1f ms · this is a valid answer" % (r.scanned, r.ms))
        con.close()
        return 0

    print("HELD  %d  ·  FORGOTTEN  %d  ·  %d rows in %.1f ms  ·  0 tokens\n"
          % (len(r.held), len(r.forgotten), r.scanned, r.ms))
    for h in r.held:
        print("  %.3f  %s" % (h.score, h.line()))
        print("         %s · run %s · %s" % (
            " ".join("%s %s" % (k, v) for k, v in sorted(h.parts.items())),
            h.run_id, _when(h.ts)))
    if r.forgotten:
        print("\n  --- compacted out of context, recoverable ---")
        for f in r.forgotten:
            print("  %.3f  %s" % (f.score, f.line()))
            if a.verbatim:
                ev = rc.verbatim(con, f)
                print("         verbatim: %s" % str(ev.get("text", ""))[:200])
    con.close()
    return 0


def cmd_forgotten(a) -> int:
    con = store.connect(a.db)
    r = rc.forgotten(con, a.query, gates=a.gate, k=a.k)
    if not r.forgotten:
        print("no residue matches — nothing was compacted away about this")
        print("%d residue rows scanned in %.1f ms" % (r.scanned, r.ms))
        con.close()
        return 0
    print("%d forgotten · %d residue rows in %.1f ms\n" % (len(r.forgotten), r.scanned, r.ms))
    for f in r.forgotten:
        print("  %.3f  %s" % (f.score, f.line()))
        ev = rc.verbatim(con, f)
        if a.verbatim and ev:
            print("         verbatim: %s" % str(ev.get("text", ""))[:400])
        elif not ev:
            print("         POINTER DEAD — this should be impossible")
    con.close()
    return 0


def cmd_audit(a) -> int:
    con = store.connect(a.db)
    rep = boundary.audit_all(con)
    if a.json:
        print(json.dumps(rep, indent=1))
        con.close()
        return 0
    t = rep["totals"]
    if not t["compactions"]:
        print("no compactions on file — run: python3 -m memory.cli ingest --runs runs")
        con.close()
        return 0
    print("compactions        %d" % t["compactions"])
    print("events             %d  ->  %d retained, %d dropped"
          % (t["events"], t["events_retained"], t["events_dropped"]))
    print("residue rows       %d  (%d live pointers, %d dangling)"
          % (t["residue_rows"], t["residue_live_pointers"],
             t["residue_rows"] - t["residue_live_pointers"]))
    print("bytes              %d  ->  %d   (x%.1f)"
          % (t["bytes_before"], t["bytes_after"], t["ratio"]))
    print()
    print("tier-1 facts       %d" % t["facts"])
    print("  still in context %d   (%.1f%%)  an agent holding only the summary knows these"
          % (t["facts_in_context"], 100 * t["context_coverage"]))
    print("  tier-0 only      %d   reachable only by following a pointer"
          % t["facts_tier0_only"])
    print("  unreachable      %d   <- must be zero" % t["facts_unreachable"])
    print("  total coverage   %.1f%%" % (100 * t["total_coverage"]))
    print()
    print("reconciles         %s" % ("yes" if t["reconciles"] else "NO"))
    if not t["reconciles"]:
        print("  the store disagrees with its own audit — investigate before trusting recall")
    for c in rep["compactions"] if a.each else []:
        print("\n  %s  run %s  span %d-%d" % (c["summary_id"][:12], c["run_id"],
                                              c["span"][0], c["span"][1]))
        print("    facts %d in context / %d tier-0 only / %d unreachable"
              % (c["facts_in_context"], c["facts_tier0_only"], c["facts_unreachable"]))
        if c["tier0_only_subjects"]:
            print("    only via tier 0: %s" % ", ".join(c["tier0_only_subjects"][:6]))
    con.close()
    return 0 if rep["totals"]["reconciles"] else 1


def cmd_stats(a) -> int:
    con = store.connect(a.db)
    s = store.stats(con)
    if a.json:
        print(json.dumps(s, indent=1))
        con.close()
        return 0
    print("tier 0  events     %6d   across %d source(s), %d run(s)"
          % (s["events"], s["sources"], s["runs"]))
    print("tier 1  facts      %6d   %s" % (s["facts"], ", ".join(
        "%s %d" % kv for kv in sorted(s["fact_kinds"].items(), key=lambda kv: -kv[1]))))
    print("tier 2  summaries  %6d   %d B -> %d B  (x%.1f)"
          % (s["summaries"], s["bytes_before"], s["bytes_after"], s["compression"]))
    print("tier 3  residue    %6d   %s" % (s["residue"], ", ".join(
        "%s %d" % kv for kv in sorted(s["residue_reasons"].items(), key=lambda kv: -kv[1]))))
    print("        dangling   %6d   <- must be zero" % s["dangling_pointers"])
    print("        index      %6.1f KB  (%d B per row, 512-d binary)"
          % (s["index_bytes"] / 1024.0, store.BYTES))
    if os.path.exists(a.db):
        print("        on disk    %6.1f MB" % (os.path.getsize(a.db) / 1e6))
    con.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="memory.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="pull real sessions and runs into tier 0")
    i.add_argument("--runs", help="a runs/ directory of Daisy run folders")
    i.add_argument("--session", help="one Claude Code .jsonl session file")
    i.add_argument("--sessions", type=int, help="N most recent sessions on this machine")
    i.add_argument("--max-bytes", type=int, default=ingest.MAX_BYTES)
    i.add_argument("--no-compact", action="store_true",
                   help="ingest without drawing a forgetting boundary")

    r = sub.add_parser("recall", help="what do I know — and what did I drop")
    r.add_argument("query")
    r.add_argument("--gate", action="append", help="a gate name you are failing")
    r.add_argument("-k", type=int, default=5)
    r.add_argument("--verbatim", action="store_true", help="follow tier-3 pointers")

    f = sub.add_parser("forgotten", help="tier 3 only: what did I compact away")
    f.add_argument("query")
    f.add_argument("--gate", action="append")
    f.add_argument("-k", type=int, default=8)
    f.add_argument("--verbatim", action="store_true")

    u = sub.add_parser("audit", help="prove which facts survived compaction")
    u.add_argument("--each", action="store_true", help="one block per compaction")
    u.add_argument("--json", action="store_true")

    s = sub.add_parser("stats", help="tier sizes and index footprint")
    s.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    return {"ingest": cmd_ingest, "recall": cmd_recall, "forgotten": cmd_forgotten,
            "audit": cmd_audit, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
