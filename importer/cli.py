"""
Import — bring setup, sessions, rules and servers from the other agent tools on
this machine into Daisy.

    python3 -m importer.cli detect
    python3 -m importer.cli import --source claude-skills            # dry run
    python3 -m importer.cli import --source claude-skills --apply
    python3 -m importer.cli sync --on
    python3 -m importer.cli sync --apply
    python3 -m importer.cli attention
    python3 -m importer.cli status

Every command takes --json, which is the same payload the UI reads through
`window.__daisyImport`.

`import` is a dry run unless you pass --apply, and --source is required with no
default and no "all" keyword. Both are deliberate: an import you did not choose
is an import you cannot audit, and a destructive default is how a demo eats
someone's config.

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importer import ingest, sync as syncmod                      # noqa: E402
from importer.attention import attention                          # noqa: E402
from importer.detect import detect                                # noqa: E402
from importer.state import State                                  # noqa: E402

RULE = "-" * 78


def _imported(st: State) -> dict:
    """source id -> how many of its items are in the ledger."""
    out = {}
    for rec in (st.ledger().get("items") or {}).values():
        out[rec.get("source")] = out.get(rec.get("source"), 0) + 1
    return out


def payload(st: State) -> dict:
    """Everything the Import view needs, in one object — and nothing else. The
    per-item arrays are dropped: the view draws counts and effects, and shipping
    a few hundred session records into a webview to render the number 44 would
    be silly. `detect --json` is where the items live."""
    det = detect()
    done = _imported(st)
    for s in det["sources"]:
        s["imported"] = done.get(s["id"], 0)
        s.pop("items", None)
    return {"generated": det["generated"], "home": det["home"],
            "project": det["project"], "state": st.home,
            "sources": det["sources"], "sync": syncmod.status(st),
            "attention": attention(st),
            "runs": len(st.runs().get("runs") or []),
            "registry": {k: len(v) for k, v in st.registry().items()
                         if isinstance(v, dict)}}


# ---------------------------------------------------------------------------
# human output
# ---------------------------------------------------------------------------

def _print_detect(det, done):
    print("detected  —  %s" % det["generated"])
    print("  home %s   project %s" % (det["home"], det["project"]))
    print()
    print("  %-18s %-9s %-8s %6s %6s  %s"
          % ("source", "tool", "kind", "items", "in", "path"))
    print("  " + RULE)
    for s in det["sources"]:
        print("  %-18s %-9s %-8s %6s %6s  %s"
              % (s["id"], s["tool"], s["kind"], s["count"] or "-",
                 done.get(s["id"], "") or "-", s["path"]))
        if not s["importable"]:
            print("      not importable:")
            _para(s["note"], " " * 8)
    print()
    print("  what each import would change")
    print("  " + RULE)
    for s in det["sources"]:
        if not s["effects"] or not s["count"]:
            continue
        print("  %s" % s["id"])
        for e in s["effects"]:
            print("      %-10s %s" % (e["action"], e["target"]))
            _para(e["detail"], " " * 10)


def _para(text, indent, width=78):
    """Wrapped block at a fixed indent. Printed, not returned, because every
    caller wants it on its own lines."""
    for ln in textwrap.wrap(text or "", width=width - len(indent)) or [""]:
        print(indent + ln)


def _print_report(rep):
    head = "dry run — nothing written" if rep.dry_run else "applied"
    print("import %s  (%s)" % (rep.source, head))
    print("  %s" % rep.summary())
    for c in rep.changes:
        if c.status == "unchanged":
            continue
        print("  %-9s %-34s %s" % (c.status, c.item[:34], c.detail))
    if rep.conflicts:
        print("\n  conflicts — both kept, nothing overwritten")
        for c in rep.conflicts:
            print("    %s" % c["item"])
            _para(c["detail"], " " * 8)
    if rep.diff:
        print("\n  config.md diff")
        for ln in rep.diff[:60]:
            print("    " + ln)
        if len(rep.diff) > 60:
            print("    ... %d more diff lines" % (len(rep.diff) - 60))
    print("\n  writes: %s" % (", ".join(rep.writes) or "none"))
    if rep.dry_run and rep.writes:
        print("  re-run with --apply to write them")


def _print_attention(a):
    if not a["imported_anything"]:
        print("needs attention — nothing has been imported yet")
        return
    print("needs attention  —  %d item%s" % (a["total"], "" if a["total"] == 1 else "s"))
    print("  " + "   ".join("%s %d" % (t["name"], t["count"]) for t in a["tabs"]))
    if not a["total"]:
        print("\n  everything imported is complete and reachable")
        return
    for t in a["tabs"]:
        rows = a["by_tab"][t["name"]]
        if not rows:
            continue
        print("\n  %s" % t["name"])
        print("  " + RULE)
        for r in rows:
            print("  %s" % r["title"])
            _para(r["detail"], " " * 6)
            _para("fix  " + r["fix"], " " * 6)


def _print_status(p):
    s, a = p["sync"], p["attention"]
    total = sum(x["count"] for x in p["sources"])
    imported = sum(x["imported"] for x in p["sources"])
    print("import status  —  %s" % p["generated"])
    print("  state dir     %s" % p["state"])
    print("  detected      %d items across %d sources" % (total, len(p["sources"])))
    print("  imported      %d items · %d runs · %s"
          % (imported, p["runs"],
             " · ".join("%d %s" % (v, k) for k, v in sorted(p["registry"].items()))))
    print("  autosync      %s · last ran %s"
          % ("on" if s["enabled"] else "off", s["last_run_human"]))
    print("  attention     %d item%s" % (a["total"], "" if a["total"] == 1 else "s"))
    print()
    print("  %-18s %6s %6s  %s" % ("source", "items", "in", "watermark"))
    print("  " + RULE)
    for x in p["sources"]:
        wm = (s["watermarks"].get(x["id"]) or {}).get("cursor", "")
        print("  %-18s %6s %6s  %s"
              % (x["id"], x["count"] or "-", x["imported"] or "-", wm or "-"))


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="importer", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--state", help="import state directory (default: importer/state)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # The same two flags again after the subcommand, because `status --json` is
    # what anyone actually types. SUPPRESS keeps the trailing copy from
    # overwriting a leading one with its own default.
    def common(pr):
        pr.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        pr.add_argument("--state", default=argparse.SUPPRESS)
        return pr

    common(sub.add_parser("detect", help="what could be imported, and what would land"))

    im = common(sub.add_parser("import", help="import one explicitly named source"))
    im.add_argument("--source", required=True, action="append",
                    help="source id from `detect` (repeat for more than one)")
    im.add_argument("--item", action="append", default=[],
                    help="narrow to specific item keys within the source")
    # Mutually exclusive so `--dry-run --apply` is an error rather than a
    # coin flip. --dry-run does nothing on its own; it exists so the safe
    # default can be stated out loud in a script.
    mode = im.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="the default; accepted so it can be stated explicitly")
    mode.add_argument("--apply", action="store_true", help="actually write")

    sy = common(sub.add_parser("sync", help="autosync: toggle it, or run one pass"))
    g = sy.add_mutually_exclusive_group()
    g.add_argument("--on", action="store_true")
    g.add_argument("--off", action="store_true")
    sy.add_argument("--once", action="store_true", help="the default when no toggle is given")
    sy.add_argument("--apply", action="store_true", help="actually write")
    sy.add_argument("--force", action="store_true",
                    help="run even with autosync off, and ignore the watermarks")

    common(sub.add_parser("attention", help="imports that are not finished"))
    common(sub.add_parser("status", help="one screen: detection, sync, attention"))

    a = ap.parse_args(argv)
    a.json = getattr(a, "json", False)
    st = State(getattr(a, "state", None))

    if a.cmd == "detect":
        det = detect()
        done = _imported(st)
        if a.json:
            for s in det["sources"]:
                s["imported"] = done.get(s["id"], 0)
            print(json.dumps(det, indent=1))
            return 0
        _print_detect(det, done)
        return 0

    if a.cmd == "import":
        det = detect()
        reports, rc = [], 0
        for sid in a.source:
            try:
                rep = ingest.run(sid, det, st, dry_run=not a.apply, only=a.item or None)
            except (ingest.UnknownSource, ingest.NotImportable) as exc:
                print("cannot import %s: %s" % (sid, exc), file=sys.stderr)
                rc = 1
                continue
            reports.append(rep)
            if not a.json:
                _print_report(rep)
                print()
        if a.json:
            print(json.dumps([ingest.as_dict(r) for r in reports], indent=1))
        return rc

    if a.cmd == "sync":
        if a.on or a.off:
            s = syncmod.set_enabled(bool(a.on), st)
            print(json.dumps(s, indent=1) if a.json
                  else "autosync %s" % ("on" if s["enabled"] else "off"))
            return 0
        res = syncmod.sync_once(detect(), st, dry_run=not a.apply, force=a.force)
        if a.json:
            print(json.dumps(res, indent=1))
            return 0
        if not res["ran"]:
            print("autosync is off — turn it on with: python3 -m importer.cli sync --on")
            return 0
        print("sync %s  —  %d item%s moved · last ran %s"
              % ("(dry run)" if res["dry_run"] else "applied", res["moved"],
                 "" if res["moved"] == 1 else "s", res["last_run_human"]))
        for r in res["sources"]:
            if "skipped" in r:
                print("  %-18s skipped — %s" % (r["source"], r["skipped"]))
            else:
                print("  %-18s %s" % (r["source"], r["summary"]))
        return 0

    if a.cmd == "attention":
        res = attention(st)
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            _print_attention(res)
        return 0

    p = payload(st)
    if a.json:
        print(json.dumps(p, indent=1))
    else:
        _print_status(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
