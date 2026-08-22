"""Talk to Daisy, and read back what it kept.

    python3 -m chat.cli new  --title "SR-11 bracket" --model claude
    python3 -m chat.cli send --to 4f2a "what does the physics gate check?"
    python3 -m chat.cli send --to 4f2a --resume
    python3 -m chat.cli list
    python3 -m chat.cli show 4f2a
    python3 -m chat.cli search "bending margin"
    python3 -m chat.cli archive 4f2a
    python3 -m chat.cli export 4f2a > thread.json

Ids may be abbreviated to any unique prefix. Every command takes --json, and
`list --json` is the exact payload the desktop shell feeds to
window.__daisyChat.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import store                                          # noqa: E402
from chat.session import send, resume, classify, RUN_THRESHOLD  # noqa: E402


def _age(t: float) -> str:
    d = max(0.0, time.time() - t)
    for cut, unit, div in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600)):
        if d < cut:
            return "%d%s" % (d // div, unit)
    return "%dd" % (d // 86400)


def _print_classification(c: dict) -> None:
    """The classification is shown, never inferred from behaviour."""
    print("  %-4s  score %.1f / %.1f%s" % (c["mode"].upper(), c["score"], c["threshold"],
                                           "  (override %s)" % c["override"] if c["override"] else ""))
    for s in c["signals"]:
        print("        %+.1f  %-13s %s" % (s["weight"], s["name"], ", ".join(s["matched"])[:52]))
    print("  %s" % c["why"])
    print("  %s: prefix the message with %s" % (c["counter"]["label"], c["counter"]["how"].strip()))


def cmd_new(a, con):
    cid = store.new_conversation(title=a.title or "", model=a.model, con=con)
    if a.json:
        print(json.dumps(store.get_conversation(cid, con=con)))
    else:
        print("conversation %s  (model %s)" % (cid, a.model))
    return 0


def cmd_send(a, con):
    if a.resume:
        out = resume(a.to, con=con, allow_substitute=a.any_model)
    else:
        if not a.text:
            print("nothing to send; pass a message or --resume", file=sys.stderr)
            return 2
        out = send(a.to, " ".join(a.text), mode=a.mode, con=con,
                   allow_substitute=a.any_model)
    if a.json:
        print(json.dumps(out, default=str))
        return 0 if out.get("ok") else 1
    if "classification" in out:
        _print_classification(out["classification"])
    if not out.get("ok"):
        print("\n  not answered: %s" % out.get("reason", "unknown"))
        if out.get("recovery"):
            print("  %s" % out["recovery"])
        return 1
    if out["mode"] == "run":
        r = out["run"]
        print("\n  run %s queued%s" % (r["id"], (" · lanes " + ", ".join(r["lanes"])) if r["lanes"] else ""))
        print("  %s" % r["command"])
        return 0
    if out.get("trim_note"):
        print("\n  %s" % out["trim_note"])
    if out.get("substituted"):
        s = out["substituted"]
        print("\n  answered by %s instead of %s — %s" % (s["to"], s["from"], s["detail"]))
    print("\n%s\n" % out["reply"]["content"])
    print("  %s · %s ms · ~%d tokens (estimated)"
          % (out["agent"], out.get("ms"), out["reply"]["tokens"]))
    return 0


def cmd_list(a, con):
    rows = store.list_conversations(include_archived=a.all, con=con)
    if a.json:
        # The shape window.__daisyChat consumes. `demo` is false because this
        # came out of a real database; the UI sets it true for itself when the
        # callback never fires.
        print(json.dumps({"conversations": rows, "stats": store.stats(con=con),
                          "threshold": RUN_THRESHOLD, "demo": False}, default=str))
        return 0
    if not rows:
        print("no conversations yet — python3 -m chat.cli new")
        return 0
    print("%-14s %-5s %-5s %-6s %s" % ("id", "msgs", "runs", "age", "title"))
    print("-" * 76)
    for r in rows:
        print("%-14s %-5d %-5d %-6s %s%s"
              % (r["id"], r["messages"], r["runs"], _age(r["updated"]),
                 r["title"] or "(untitled)", "  [archived]" if r["archived"] else ""))
    return 0


def cmd_show(a, con):
    c = store.get_conversation(a.id, con=con)
    if not c:
        print("no conversation %r" % a.id, file=sys.stderr)
        return 1
    msgs = store.messages(c["id"], con=con)
    if a.json:
        print(json.dumps({"conversation": c, "messages": msgs,
                          "runs": store.runs(c["id"], con=con),
                          "unanswered": store.unanswered(c["id"], con=con)}, default=str))
        return 0
    print("%s  %s  (model %s%s)" % (c["id"], c["title"] or "(untitled)", c["model"],
                                    ", archived" if c["archived"] else ""))
    print("-" * 76)
    for m in msgs:
        cls = (m["meta"] or {}).get("classification") or {}
        tag = "  [%s %.1f]" % (cls["mode"], cls["score"]) if cls else ""
        print("\n%s%s%s" % (m["role"], ("  ·  " + m["model"]) if m["model"] else "", tag))
        print(m["content"])
    pend = store.unanswered(c["id"], con=con)
    if pend:
        print("\n-- the last turn was never answered; `send --to %s --resume` retries it"
              % c["id"][:8])
    return 0


def cmd_search(a, con):
    res = store.search(" ".join(a.text), limit=a.limit, conversation_id=a.to,
                       engine=a.engine, include_archived=a.all, con=con)
    if a.json:
        print(json.dumps(res, default=str))
        return 0
    print("%d hit%s via %s%s" % (len(res["hits"]), "" if len(res["hits"]) == 1 else "s",
                                 res["engine"], "" if res["fts5"] else "  (no FTS5 in this SQLite build)"))
    for h in res["hits"]:
        body = " ".join(h["content"].split())
        print("  %-14s %-9s %s" % (h["conversation_id"], h["role"], body[:58]))
    return 0


def cmd_archive(a, con):
    ok = store.archive(a.id, on=not a.undo, con=con)
    if a.json:
        print(json.dumps({"ok": ok, "id": a.id, "archived": not a.undo}))
    else:
        print(("archived %s" if not a.undo else "restored %s") % a.id if ok
              else "no conversation %r" % a.id)
    return 0 if ok else 1


def cmd_export(a, con):
    doc = store.export_conversation(a.id, con=con)
    if not doc:
        print("no conversation %r" % a.id, file=sys.stderr)
        return 1
    print(json.dumps(doc, indent=None if a.json else 2, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chat", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database file (default ~/.daisy/daisy.db)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")

    # The same two flags on every subcommand, so `list --json` works as well as
    # `--json list`. SUPPRESS keeps the subparser copy from resetting a value the
    # top-level parser already took.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help="database file (default ~/.daisy/daisy.db)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", parents=[common], help="start a conversation")
    n.add_argument("--title", default="")
    n.add_argument("--model", default="auto", help="claude, codex, opencode or auto")

    s = sub.add_parser("send", parents=[common], help="send a message")
    s.add_argument("--to", required=True, help="conversation id or prefix")
    s.add_argument("text", nargs="*")
    s.add_argument("--mode", choices=["chat", "run"],
                   help="override the classifier explicitly (same as a /chat or /run prefix)")
    s.add_argument("--resume", action="store_true", help="retry the turn a crash left unanswered")
    s.add_argument("--any-model", action="store_true",
                   help="answer with another agent if this conversation's model is unusable")

    l = sub.add_parser("list", parents=[common], help="conversations, newest first")
    l.add_argument("--all", action="store_true", help="include archived")

    sh = sub.add_parser("show", parents=[common], help="one conversation in full")
    sh.add_argument("id")

    se = sub.add_parser("search", parents=[common], help="full-text search across messages")
    se.add_argument("text", nargs="+")
    se.add_argument("--to", help="restrict to one conversation")
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--all", action="store_true", help="include archived")
    se.add_argument("--engine", choices=["auto", "fts", "like"], default="auto")

    ar = sub.add_parser("archive", parents=[common], help="archive a conversation")
    ar.add_argument("id")
    ar.add_argument("--undo", action="store_true", help="restore it instead")

    ex = sub.add_parser("export", parents=[common], help="one conversation as JSON")
    ex.add_argument("id")

    a = ap.parse_args(argv)
    con = store.connect(a.db)
    try:
        return {"new": cmd_new, "send": cmd_send, "list": cmd_list, "show": cmd_show,
                "search": cmd_search, "archive": cmd_archive, "export": cmd_export}[a.cmd](a, con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
