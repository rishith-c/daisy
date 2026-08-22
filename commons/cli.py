"""Command line for the Verified Commons.

    python3 -m commons.cli consent show
    python3 -m commons.cli consent grant --scope local
    python3 -m commons.cli admit --task "..." --gate physics.bend=pass --tokens 48000
    python3 -m commons.cli recall --task "..." --gate physics.bend
    python3 -m commons.cli stats
    python3 -m commons.cli publish --id <id> --out bundle/
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commons.consent import Ledger, SCOPES, SCOPE_TEXT       # noqa: E402
from commons.store import (Solution, admit, recall, record_reuse, stats,   # noqa: E402
                           NotVerified, connect)
from commons.publish import publish                          # noqa: E402


def parse_gate(s):
    if "=" in s:
        n, v = s.split("=", 1)
        return {"name": n, "passed": v.strip().lower() in ("pass", "true", "1", "ok")}
    return {"name": s, "passed": True}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="commons", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consent"); c.add_argument("action", choices=("show", "grant", "revoke"))
    c.add_argument("--scope", choices=SCOPES); c.add_argument("--target", default="")

    a = sub.add_parser("admit")
    a.add_argument("--task", required=True); a.add_argument("--brief", default="")
    a.add_argument("--gate", action="append", default=[], help="name=pass|fail")
    a.add_argument("--vendor", default=""); a.add_argument("--model", default="")
    a.add_argument("--kind", default="software"); a.add_argument("--artifact", default="")
    a.add_argument("--recipe", default=""); a.add_argument("--tokens", type=int, default=0)

    r = sub.add_parser("recall")
    r.add_argument("--task", required=True); r.add_argument("--gate", action="append", default=[])
    r.add_argument("--kind"); r.add_argument("--limit", type=int, default=5)

    u = sub.add_parser("reuse"); u.add_argument("--id", required=True)
    u.add_argument("--tokens", type=int, required=True)

    sub.add_parser("stats")

    p = sub.add_parser("publish"); p.add_argument("--id", required=True)
    p.add_argument("--out", required=True); p.add_argument("--target", default="makerworld")
    p.add_argument("--live", action="store_true")

    o = ap.parse_args(argv)

    if o.cmd == "consent":
        led = Ledger()
        if o.action == "show":
            print("consent — default is deny; nothing leaves without a grant\n")
            for s in SCOPES:
                print("  %-10s %-8s %s" % (s, "GRANTED" if led.allows(s) else "denied", SCOPE_TEXT[s]))
            return 0
        if not o.scope:
            print("--scope required"); return 2
        (led.grant if o.action == "grant" else led.revoke)(o.scope, o.target)
        print("%s %s%s" % (o.action + "ed", o.scope, (" for " + o.target) if o.target else ""))
        return 0

    if o.cmd == "admit":
        sol = Solution(task=o.task, brief=o.brief, gates=[parse_gate(g) for g in o.gate],
                       vendor=o.vendor, model=o.model, kind=o.kind, artifact=o.artifact,
                       recipe=o.recipe, tokens_cost=o.tokens)
        try:
            print(json.dumps({"admitted": admit(sol, o.db), "signature": sol.signature()}))
        except NotVerified as e:
            print(json.dumps({"refused": str(e)})); return 1
        return 0

    if o.cmd == "recall":
        hits = recall(o.task, kind=o.kind, limit=o.limit, gates=o.gate or None, db=o.db)
        if not hits:
            print(json.dumps({"hits": [], "note": "no verified precedent — nothing here fits"}))
            return 0
        print(json.dumps({"hits": hits}, indent=1))
        return 0

    if o.cmd == "reuse":
        print(json.dumps(record_reuse(o.id, o.tokens, o.db))); return 0

    if o.cmd == "stats":
        print(json.dumps(stats(o.db), indent=1)); return 0

    if o.cmd == "publish":
        con = connect(o.db)
        row = con.execute("SELECT id, task, brief, vendor, model, kind, artifact, gate_sig,"
                          " gates, recipe, tokens_cost, reuses FROM solution WHERE id = ?",
                          (o.id,)).fetchone()
        con.close()
        if not row:
            print(json.dumps({"error": "no such solution"})); return 1
        keys = ["id", "task", "brief", "vendor", "model", "kind", "artifact", "gate_sig",
                "gates", "recipe", "tokens_cost", "reuses"]
        sol = dict(zip(keys, row)); sol["gates"] = json.loads(sol["gates"])
        print(json.dumps(publish(sol, o.out, o.target, live=o.live), indent=1))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
