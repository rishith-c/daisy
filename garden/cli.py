"""Garden — the shared index of verified solutions.

    python3 -m garden.cli whoami
    python3 -m garden.cli search --gate physics.bend
    python3 -m garden.cli publish --id <commons id>
    python3 -m garden.cli status
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commons.store import connect                    # noqa: E402
from garden import index                             # noqa: E402
from garden.identity import providers, publisher     # noqa: E402
from garden.publish import publish, NotPublishable   # noqa: E402


def _solution(sid):
    con = connect()
    row = con.execute("SELECT id, task, brief, vendor, model, kind, artifact, gate_sig,"
                      " gates, recipe, tokens_cost, reuses FROM solution WHERE id LIKE ?",
                      (sid + "%",)).fetchone()
    con.close()
    if not row:
        return None
    keys = ["id", "task", "brief", "vendor", "model", "kind", "artifact", "gate_sig",
            "gates", "recipe", "tokens_cost", "reuses"]
    s = dict(zip(keys, row))
    s["gates"] = json.loads(s["gates"])
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="garden", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    sub.add_parser("status")
    s = sub.add_parser("search")
    s.add_argument("--gate", action="append", default=[])
    s.add_argument("--text", default="")
    s.add_argument("--kind")
    p = sub.add_parser("publish")
    p.add_argument("--id", required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument("--repo")
    a = ap.parse_args(argv)

    if a.cmd == "whoami":
        print("%-10s %-10s %-16s %s" % ("provider", "signed in", "account", "detail"))
        print("-" * 76)
        for pr in providers():
            print("%-10s %-10s %-16s %s" % (pr.name, "yes" if pr.signed_in else "no",
                                            pr.account[:16], pr.detail[:36]))
        print()
        w = publisher()
        print("publishing as: %s (via %s)" % (w.get("as") or "-", w.get("via") or "-"))
        if w.get("note"):
            print("  %s" % w["note"])
        return 0

    if a.cmd == "status":
        path = index.ensure_local()
        print(json.dumps({"clone": path, "entries": len(index.entries()),
                          "remote": index.has_remote() or None,
                          "repo_env": index.DEFAULT_REMOTE or None}, indent=1))
        return 0

    if a.cmd == "search":
        hits = index.search(a.gate or None, a.text, a.kind)
        if not hits:
            print(json.dumps({"hits": [], "note":
                              "nothing in Garden matches — this is new work"}))
            return 0
        for h in hits:
            print("%.3f  %-52s  %s" % (h["score"], h.get("title", "")[:52],
                                       ",".join(h["matched_gates"])))
        return 0

    if a.cmd == "publish":
        sol = _solution(a.id)
        if not sol:
            print(json.dumps({"error": "no solution %s in the commons" % a.id}))
            return 1
        try:
            print(json.dumps(publish(sol, live=a.live, repo=a.repo), indent=1))
        except NotPublishable as e:
            print(json.dumps({"refused": str(e)}))
            return 1
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
