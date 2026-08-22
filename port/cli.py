"""
Terminal interface to the governance plane. JSON out, so it pipes into anything.

    python3 -m port.cli bootstrap
    python3 -m port.cli plan     --run 1042
    python3 -m port.cli gate     --run 1042 --lane hardware --name physics.bend \
                                 --value 69.0 --allowable 50.0 --margin 0.72 --failed
    python3 -m port.cli approve  --run 1042 --request          # BLOCKS here
    python3 -m port.cli approve  --run 1042 --grant --by rishith
    python3 -m port.cli status   --run 1042
    python3 -m port.cli audit    --run 1042

The demo is two terminals, and it is the point of the whole package:

    terminal A   python3 -m port.cli plan --run 1042
                 python3 -m port.cli approve --run 1042 --request     <- sits there
    terminal B   python3 -m port.cli approve --run 1042 --grant --by rishith

Terminal A does not release until terminal B decides, and if terminal B denies,
terminal A exits non-zero having released nothing.

Every command prints which mode it ran in. A dry run says so in `port.note` and
never phrases a spooled request as a live one.

Exit codes:  0 done - 1 blocked or refused - 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import factory as F
from .blueprints import PROJECT
from .client import PortClient

REFUSALS = (F.NotPlanned, F.NotApproved, F.PlanDrift, F.ScorecardRed, F.SelfApproval)


def _client(a, default_run: str = "1042") -> PortClient:
    # --run is resolved here rather than by argparse defaults: a default set on
    # a subparser silently overrides one the user typed before the subcommand,
    # which is a fine way to bootstrap into the wrong spool.
    a.run = a.run or default_run
    return PortClient(run_id=a.run, spool=a.spool, force_dry=True if a.dry else None)


def _emit(client: PortClient, payload: dict, code: int = 0) -> int:
    payload["port"] = client.describe()
    print(json.dumps(payload, indent=2, default=str))
    return code


def cmd_bootstrap(a) -> int:
    c = _client(a, "bootstrap")
    return _emit(c, {"command": "bootstrap", **F.bootstrap(c)})


def cmd_plan(a) -> int:
    c = _client(a)
    brief = dict(PROJECT)
    if a.brief:
        brief["text"] = a.brief
    lanes = None
    if a.lane:
        lanes = []
        for spec in a.lane:
            name, _, agent = spec.partition(":")
            lanes.append({"name": name, "agent": agent or "claude"})
    return _emit(c, {"command": "plan",
                     **F.commit_plan(c, a.run, brief, lanes, a.gate or None),
                     "next": "no agent may start until this exists; "
                             "port.cli status --run %s confirms it" % a.run})


def cmd_gate(a) -> int:
    c = _client(a)
    repair = None
    if a.repair_param:
        repair = {"parameter": a.repair_param, "from": a.repair_from, "to": a.repair_to,
                  "derivation": a.repair_derivation, "kind": a.repair_kind}
    try:
        F.require_plan(c, a.run)
    except REFUSALS as e:
        return _emit(c, {"command": "gate", "refused": type(e).__name__, "why": str(e)}, 1)
    out = F.record_gate(c, a.run, a.lane, a.name, not a.failed, value=a.value,
                        allowable=a.allowable, margin=a.margin, unit=a.unit,
                        formula=a.formula, detail=a.detail, repair=repair)
    return _emit(c, {"command": "gate", "gate": out, "summary": F.sync(c, a.run)})


def cmd_approve(a) -> int:
    c = _client(a)
    if a.grant or a.deny:
        try:
            out = (F.grant if a.grant else F.deny)(c, a.run, a.by, a.reason, a.action_run)
        except (F.SelfApproval, ValueError) as e:
            return _emit(c, {"command": "approve", "refused": type(e).__name__,
                             "why": str(e)}, 1)
        return _emit(c, {"command": "approve", "decision": out})

    if not a.request:
        return _emit(c, {"command": "approve",
                         "error": "choose --request (open and wait) or --grant/--deny"}, 2)

    # The blocking half. Everything above this line is a decision being made;
    # everything below is the factory waiting for one.
    try:
        opened = F.open_approval(c, a.run, a.by or "labctl")
    except REFUSALS as e:
        return _emit(c, {"command": "approve", "refused": type(e).__name__, "why": str(e)}, 1)

    print(json.dumps({"command": "approve", "state": "waiting", **opened,
                      "hint": "decide with: python3 -m port.cli approve --run %s "
                              "--grant --by <name>" % a.run}, indent=2), file=sys.stderr)

    d = F.await_approval(c, a.run, a.timeout, a.poll)
    payload = {"command": "approve", "decision": {"state": d.state, "by": d.by,
                                                  "reason": d.reason, "polls": d.polls,
                                                  "waited_s": round(d.waited_s, 1)}}
    if not d.granted:
        payload["released"] = False
        payload["blocked_because"] = "approval %s" % d.state
        payload["audit"] = F.audit(c, a.run)
        return _emit(c, payload, 1)
    try:
        payload["release"] = F.release(c, a.run)
    except REFUSALS as e:
        payload["released"] = False
        payload["refused"] = type(e).__name__
        payload["why"] = str(e)
        return _emit(c, payload, 1)
    payload["released"] = True
    payload["seal"] = F.seal(c, a.run)
    return _emit(c, payload)


def cmd_status(a) -> int:
    c = _client(a)
    out = F.status(c, a.run)
    out["command"] = "status"
    out.pop("port", None)
    return _emit(c, out)


def cmd_audit(a) -> int:
    c = _client(a)
    out = F.audit(c, a.run)
    out["command"] = "audit"
    return _emit(c, out, 0 if out["chain"]["intact"] else 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="port", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=None, help="run identifier (default 1042)")
    ap.add_argument("--spool", default=None, help="override the spool path")
    ap.add_argument("--dry", action="store_true",
                    help="force dry mode even with credentials present")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="push blueprints, scorecard, action, service catalog"
                   ).set_defaults(f=cmd_bootstrap)

    p = sub.add_parser("plan", help="commit the plan BEFORE any agent starts")
    p.set_defaults(f=cmd_plan)
    p.add_argument("--brief", default="")
    p.add_argument("--lane", action="append", help="name:agent, repeatable")
    p.add_argument("--gate", action="append", help="gate name, repeatable")

    g = sub.add_parser("gate", help="record one deterministic gate result")
    g.set_defaults(f=cmd_gate)
    g.add_argument("--lane", required=True)
    g.add_argument("--name", required=True)
    g.add_argument("--failed", action="store_true")
    g.add_argument("--value", type=float)
    g.add_argument("--allowable", type=float)
    g.add_argument("--margin", type=float)
    g.add_argument("--unit", default="")
    g.add_argument("--formula", default="")
    g.add_argument("--detail", default="")
    g.add_argument("--repair-param", default="")
    g.add_argument("--repair-from", default="")
    g.add_argument("--repair-to", default="")
    g.add_argument("--repair-derivation", default="")
    g.add_argument("--repair-kind", default="algebra")

    v = sub.add_parser("approve", help="open the gate and wait, or decide")
    v.set_defaults(f=cmd_approve)
    v.add_argument("--request", action="store_true", help="open the approval and BLOCK")
    v.add_argument("--grant", action="store_true")
    v.add_argument("--deny", action="store_true")
    v.add_argument("--by", default="")
    v.add_argument("--reason", default="")
    v.add_argument("--action-run", default="", help="Port action run id, when there is one")
    v.add_argument("--timeout", type=float, default=F.APPROVAL_TIMEOUT_S)
    v.add_argument("--poll", type=float, default=F.POLL_S)

    sub.add_parser("status", help="run, gates, approval, scorecard").set_defaults(f=cmd_status)
    sub.add_parser("audit", help="verify the hash-chained request log").set_defaults(f=cmd_audit)

    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    raise SystemExit(main())
