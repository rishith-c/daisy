"""
The governed loop:  brief -> plan -> build -> test -> approval -> release -> audit.

WHY this file exists
--------------------
An approval that arrives after the work is finished is a notification. The only
thing that turns it into a gate is ORDER: the plan is committed to Port before
an agent is spawned, and every spawn asks Port whether that plan exists.

    THE PROPERTY, stated plainly so it can be tested rather than claimed:

        delete Port and no agent ever starts.

    spawn() calls require_plan() before it touches the work callable, and
    require_plan() raises — it does not return False — so there is no path
    where a caller forgets to check. Take the Run entity away, or take Port
    away, and every lane in the run refuses to start. There is a test for
    exactly this, and it asserts the agent callable was never invoked.

Three more places where the order is the whole point:

    - The approval is bound to a plan hash. release() recomputes nothing and
      trusts nothing: it compares the hash the human was shown against the hash
      the run currently carries, and refuses if they differ. Approving a plan
      and then editing it is the oldest way to defeat a review board.
    - A timeout is not consent. await_approval() returns state "timeout", and
      release() accepts exactly one state, "granted". There is no path where
      waiting long enough releases a run.
    - The factory cannot approve itself. grant() rejects a decider whose name
      is the orchestrator, an agent, or the machine — the same law CLAUDE.md
      states as "the agent token has no approval action".

What this file deliberately does NOT do:

    - it does not run agents, build anything, or evaluate a gate. It records
      what deterministic things elsewhere in the repo decided. The thing
      deciding whether to release is not the thing that wrote the code.
    - it does not query Port to reconstruct a run's gates. It replays the
      client's own spool, which is complete for this run and needs no search
      API. Across machines you would swap that for POST /v1/entities/search,
      and the two would have to agree.
    - it does not retry a denial, escalate, or notify. A denied run stops, and
      a human decides what happens next.

Zero third-party dependencies.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict

from . import client as pclient
from .blueprints import (BLUEPRINTS, ORDER, SCORECARD, SERVICES, APPROVE_ACTION, PROJECT,
                         BP_APPROVAL, BP_ARTIFACT, BP_BRIEF, BP_GATE, BP_LANE, BP_REPAIR,
                         BP_RUN, BP_SERVICE, FOS_MIN, evaluate)
from .client import UPSERT, PortClient, PortError

RETRY_CAP = 2                    # CLAUDE.md law: two retries, findings injected verbatim
APPROVAL_TIMEOUT_S = 600.0
POLL_S = 1.0

# Names that may not appear as the decider on an approval. This is the factory
# refusing to sign its own work; it is a short list on purpose, and it is a
# convention rather than a security boundary — the real boundary is Port's own
# permissions, where the agent's token simply does not have the action.
NOT_A_HUMAN = frozenset({"agent", "agents", "bot", "claude", "codex", "daisy", "factory",
                         "labctl", "orchestrator", "system", "ci", "root", "automation"})


class NotPlanned(Exception):
    """No committed plan in Port for this run — so nothing may start."""


class NotApproved(Exception):
    """No granted human decision for this run."""


class PlanDrift(Exception):
    """The plan changed after it was approved."""


class ScorecardRed(Exception):
    """The release scorecard is not at Gold."""


class SelfApproval(Exception):
    """The factory tried to approve its own work."""


@dataclass(frozen=True)
class Decision:
    state: str                   # granted | denied | timeout | pending
    by: str = ""
    reason: str = ""
    plan_sha: str = ""
    polls: int = 0
    waited_s: float = 0.0

    @property
    def granted(self) -> bool:
        return self.state == "granted"


# ---------------------------------------------------------------------------
# identifiers — derived, never invented, so a second process finds the same row
# ---------------------------------------------------------------------------

def brief_id(run: str) -> str:
    return "brief-%s" % run


def approval_id(run: str) -> str:
    return "approval-%s" % run


def lane_id(run: str, lane: str) -> str:
    return "%s/%s" % (run, lane)


def gate_id(run: str, lane: str, name: str) -> str:
    return "%s/%s/%s" % (run, lane, name)


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def plan_hash(plan: dict) -> str:
    """blake2b over the canonical plan. What the human approves, byte for byte."""
    return hashlib.blake2b(json.dumps(plan, sort_keys=True, default=str).encode("utf-8"),
                           digest_size=16).hexdigest()


# ---------------------------------------------------------------------------
# bootstrap — push the model itself
# ---------------------------------------------------------------------------

def bootstrap(client: PortClient) -> dict:
    """Create the blueprints, the scorecard, the approval action and the catalog.

    Blueprints go in dependency order because Port validates a relation's
    target at create time. Re-running is safe: a 409 means the blueprint is
    already there, and the definition is PATCHed over it instead — a bootstrap
    you are afraid to run twice is a bootstrap nobody runs at all.
    """
    out = {"blueprints": [], "scorecard": None, "action": None, "services": []}
    for ident in ORDER:
        r = client.call("blueprint_create", body=BLUEPRINTS[ident],
                        params={"create_catalog_page": "true"})
        if r.status == 409:
            r = client.call("blueprint_update", blueprint=ident, body=BLUEPRINTS[ident])
            out["blueprints"].append({"identifier": ident, "status": r.status, "action": "updated"})
        else:
            out["blueprints"].append({"identifier": ident, "status": r.status, "action": "created"})

    r = client.call("scorecard_create", blueprint=BP_RUN, body=SCORECARD)
    out["scorecard"] = {"identifier": SCORECARD["identifier"], "status": r.status,
                        "rules": len(SCORECARD["rules"])}

    r = client.call("action_create", body=APPROVE_ACTION)
    out["action"] = {"identifier": APPROVE_ACTION["identifier"], "status": r.status}

    for svc in SERVICES:
        r = client.call("entity_create", blueprint=BP_SERVICE, body=svc, params=UPSERT)
        out["services"].append({"identifier": svc["identifier"], "status": r.status})
    return out


# ---------------------------------------------------------------------------
# (a) the plan — committed BEFORE anything runs
# ---------------------------------------------------------------------------

def commit_plan(client: PortClient, run: str, brief: dict | None = None,
                lanes: list[dict] | None = None, gates: list[str] | None = None,
                fos_target: float = FOS_MIN, retry_cap: int = RETRY_CAP) -> dict:
    """Write the Brief, the Lanes and the Run, in that order, and hash the plan.

    Nothing in the factory is allowed to run before this returns. The Run entity
    is what every later spawn checks for, so committing it IS the gate opening —
    which is why it is written last, after the things it points at exist.
    """
    brief = dict(brief or PROJECT)
    lanes = lanes or [{"name": "web-frontend", "agent": "claude"},
                      {"name": "web-api", "agent": "codex"},
                      {"name": "hardware", "agent": "claude"}]
    gates = gates or ["taste.t1", "taste.t2", "contract.conformance",
                      "physics.bend", "physics.shear", "scrape.freshness"]

    plan = {
        "run": run,
        "brief": brief.get("text", ""),
        "lanes": [{"name": l["name"], "agent": l.get("agent", "claude"),
                   "model": l.get("model", "")} for l in lanes],
        "gates": sorted(gates),
        "fos_target": fos_target,
        "retry_cap": retry_cap,
    }
    sha = plan_hash(plan)
    ts = now_iso()

    brief.setdefault("submitted_at", ts)
    client.call("entity_create", blueprint=BP_BRIEF, params=UPSERT, body={
        "identifier": brief_id(run), "title": "Brief for %s" % run, "properties": brief})

    for lane in lanes:
        client.call("entity_create", blueprint=BP_LANE, params=UPSERT, body={
            "identifier": lane_id(run, lane["name"]), "title": lane["name"],
            "properties": {"agent": lane.get("agent", "claude"),
                           "model": lane.get("model", ""),
                           "worktree": lane.get("worktree", "worktrees/%s" % lane["name"]),
                           "branch": lane.get("branch", "%s/%s" % (run, lane["name"])),
                           "status": "planned"}})

    r = client.call("entity_create", blueprint=BP_RUN, params=UPSERT, body={
        "identifier": run, "title": "Run %s" % run,
        "properties": {"status": "planned", "plan_sha": sha, "plan": _markdown(plan),
                       "mode": client.mode, "started_at": ts,
                       "fos_target": fos_target, "retry_cap": retry_cap,
                       "gates_run": 0, "gates_failed": 0, "approved": False,
                       "approved_by": ""},
        "relations": {"brief": brief_id(run),
                      "lanes": [lane_id(run, l["name"]) for l in lanes],
                      "services": [s["identifier"] for s in SERVICES]}})

    return {"run": run, "plan_sha": sha, "committed_at": ts, "status": r.status,
            "lanes": [l["name"] for l in lanes], "gates_planned": plan["gates"],
            "mode": client.mode}


def require_plan(client: PortClient, run: str) -> dict:
    """Confirm Port holds a committed plan for this run, or raise.

    Fails closed on every failure mode, including the interesting one: if Port
    cannot be reached at all, that is NOT treated as permission to proceed. An
    ungoverned run is worse than no run.
    """
    try:
        r = client.call("entity_get", blueprint=BP_RUN, entity=run)
    except PortError as e:
        raise NotPlanned("cannot confirm a plan for %s: %s" % (run, e)) from e
    if not r.ok:
        raise NotPlanned("no committed plan for %s (%s %s)" % (run, r.status, client.mode))
    props = ((r.body.get("entity") or {}).get("properties") or {})
    if not props.get("plan_sha"):
        raise NotPlanned("run %s exists but carries no plan hash" % run)
    return props


def spawn(client: PortClient, run: str, lane: str, work, *args, **kwargs):
    """Start one lane's agent — and only if Port says a plan exists.

    Five lines, and they are the load-bearing five in the whole package. The
    check happens before `work` is named, let alone called.
    """
    require_plan(client, run)
    client.call("entity_update", blueprint=BP_LANE, entity=lane_id(run, lane),
                body={"properties": {"status": "running", "started_at": now_iso()}})
    return work(*args, **kwargs)


# ---------------------------------------------------------------------------
# (b) gate results
# ---------------------------------------------------------------------------

def record_gate(client: PortClient, run: str, lane: str, name: str, passed: bool,
                value=None, allowable=None, margin=None, unit: str = "",
                formula: str = "", detail: str = "", deterministic: bool = True,
                duration_ms: float | None = None, repair: dict | None = None) -> dict:
    """Record one gate outcome, and its repair when the failure had one.

    The Repair entity is written first so the Gate can point at something that
    exists. A gate that failed and offers no repair is a legitimate state — it
    means the factory has no algebra for this one and a human is next.
    """
    ident = gate_id(run, lane, name)
    relations = {}
    if repair:
        rid = ident + "/repair"
        client.call("entity_create", blueprint=BP_REPAIR, params=UPSERT, body={
            "identifier": rid, "title": "Repair for %s" % name,
            "properties": {"parameter": repair.get("parameter", ""),
                           "from_value": str(repair.get("from", repair.get("from_value", ""))),
                           "to_value": str(repair.get("to", repair.get("to_value", ""))),
                           "derivation": repair.get("derivation", ""),
                           "kind": repair.get("kind", "algebra"),
                           "applied": bool(repair.get("applied", False)),
                           "precedent_run": repair.get("precedent_run", ""),
                           "precedent_score": repair.get("precedent_score", 0)}})
        relations["repair"] = rid

    props = {"name": name, "kind": name.split(".", 1)[0], "passed": bool(passed),
             "unit": unit, "formula": formula, "detail": detail,
             "deterministic": bool(deterministic), "ran_at": now_iso()}
    for k, v in (("value", value), ("allowable", allowable), ("margin", margin),
                 ("duration_ms", duration_ms)):
        if v is not None:
            props[k] = v

    r = client.call("entity_create", blueprint=BP_GATE, params=UPSERT, body={
        "identifier": ident, "title": name, "properties": props, "relations": relations})
    return {"identifier": ident, "status": r.status, "properties": props,
            "repair": relations.get("repair")}


def record_artifact(client: PortClient, run: str, lane: str, path: str, kind: str,
                    sha256: str = "", nbytes: int = 0, url: str = "") -> dict:
    ident = "%s/%s" % (run, path)
    r = client.call("entity_create", blueprint=BP_ARTIFACT, params=UPSERT, body={
        "identifier": ident, "title": path,
        "properties": {"path": path, "kind": kind, "sha256": sha256, "bytes": nbytes,
                       "url": url, "produced_at": now_iso()}})
    return {"identifier": ident, "status": r.status}


def gates_for(client: PortClient, run: str) -> list[dict]:
    """Every gate this run recorded, folded out of the spool."""
    prefix = "entity:%s:%s/" % (BP_GATE, run)
    keys = []
    for row in pclient.read(client.spool):
        key = row.get("resource", "")
        if key.startswith(prefix) and key not in keys:
            keys.append(key)
    out = []
    for key in keys:
        state = pclient.replay(client.spool, key)
        if state:
            out.append(state.get("properties") or {})
    return out


def summarise(gates: list[dict]) -> dict:
    """Reduce a gate stream to the four scalars the scorecard reads.

    The mapping is a real decision and is written out rather than buried:

        physics.*          the worst margin becomes min_physics_fos
        taste.t2           the worst measured ratio becomes min_contrast_ratio
        taste.t1           findings are the measured value, summed
        scrape.freshness   the measured value is the age of the scrape, seconds

    A scalar with no gate behind it is left absent, not zeroed. Zero would read
    as "perfect" to every one of these rules; absent fails closed instead.
    """
    out = {"gates_run": len(gates),
           "gates_failed": sum(1 for g in gates if not g.get("passed"))}
    physics = [g.get("margin") for g in gates
               if str(g.get("name", "")).startswith("physics.") and g.get("margin") is not None]
    contrast = [g.get("value") for g in gates
                if g.get("name") in ("taste.t2", "taste.contrast") and g.get("value") is not None]
    findings = [g.get("value") for g in gates
                if g.get("name") in ("taste.t1", "taste.lint") and g.get("value") is not None]
    fresh = [g.get("value") for g in gates
             if g.get("name") == "scrape.freshness" and g.get("value") is not None]
    if physics:
        out["min_physics_fos"] = min(physics)
    if contrast:
        out["min_contrast_ratio"] = min(contrast)
    if findings:
        out["taste_findings"] = sum(findings)
    if fresh:
        out["scrape_age_s"] = max(fresh)
    return out


def sync(client: PortClient, run: str) -> dict:
    """Push the gate summary onto the Run so the scorecard has something to read."""
    gates = gates_for(client, run)
    props = summarise(gates)
    props["status"] = "gated" if not props["gates_failed"] else "blocked"
    client.call("entity_update", blueprint=BP_RUN, entity=run, body={"properties": props})
    return props


# ---------------------------------------------------------------------------
# (c) approval — the part that blocks
# ---------------------------------------------------------------------------

def open_approval(client: PortClient, run: str, requested_by: str = "labctl",
                  summary: str = "", scope: str = "release") -> dict:
    """Open the gate and bind it to the plan hash the human is being shown."""
    props = require_plan(client, run)
    ident = approval_id(run)
    body = {"identifier": ident, "title": "Approval for %s" % run,
            "properties": {"state": "pending", "scope": scope,
                           "plan_sha": props.get("plan_sha", ""),
                           "summary": summary or _summary(client, run, props),
                           "requested_at": now_iso(), "requested_by": requested_by,
                           "decided_at": "", "decided_by": "", "reason": "",
                           "action_run_id": ""}}
    r = client.call("entity_create", blueprint=BP_APPROVAL, params=UPSERT, body=body)
    client.call("entity_update", blueprint=BP_RUN, entity=run, body={
        "properties": {"status": "awaiting_approval"},
        "relations": {"approval": ident}})
    return {"approval": ident, "state": "pending", "plan_sha": props.get("plan_sha", ""),
            "status": r.status, "mode": client.mode}


def read_approval(client: PortClient, run: str) -> dict:
    r = client.call("entity_get", blueprint=BP_APPROVAL, entity=approval_id(run))
    if not r.ok:
        return {}
    return ((r.body.get("entity") or {}).get("properties") or {})


def await_approval(client: PortClient, run: str, timeout_s: float = APPROVAL_TIMEOUT_S,
                   poll_s: float = POLL_S, sleep=time.sleep, clock=time.time) -> Decision:
    """BLOCK until a human grants or denies. Returns; never proceeds on its own.

    A timeout returns state "timeout" — deliberately not "granted" and
    deliberately not an exception that a caller might catch and shrug off.
    release() accepts exactly one state, so waiting is never a way through.
    """
    started = clock()
    polls = 0
    while True:
        polls += 1
        appr = read_approval(client, run)
        state = appr.get("state", "pending")
        if state in ("granted", "denied"):
            return Decision(state, appr.get("decided_by", ""), appr.get("reason", ""),
                            appr.get("plan_sha", ""), polls, clock() - started)
        if clock() - started >= timeout_s:
            return Decision("timeout", "", "no decision within %.0f s" % timeout_s,
                            appr.get("plan_sha", ""), polls, clock() - started)
        sleep(poll_s)


def decide(client: PortClient, run: str, state: str, by: str, reason: str = "",
           action_run: str = "") -> dict:
    """Record a human decision against the run.

    Refuses an unnamed decider and refuses the factory's own names. The Run is
    patched at the same time because the Gold rule reads `approved` off the Run,
    and a decision that does not move the scorecard is decoration.
    """
    if state not in ("granted", "denied"):
        raise ValueError("a decision is 'granted' or 'denied', not %r" % state)
    who = (by or "").strip()
    if not who:
        raise SelfApproval("an approval needs a named human")
    if who.lower() in NOT_A_HUMAN:
        raise SelfApproval("%r is the factory, not a reviewer — it cannot approve its own run"
                           % who)
    granted = state == "granted"
    props = {"state": state, "decided_at": now_iso(), "decided_by": who, "reason": reason}
    if action_run:
        props["action_run_id"] = action_run
    client.call("entity_update", blueprint=BP_APPROVAL, entity=approval_id(run),
                body={"properties": props})
    client.call("entity_update", blueprint=BP_RUN, entity=run, body={"properties": {
        "approved": granted, "approved_by": who if granted else "",
        "status": "gated" if granted else "denied"}})

    # In a live org the same decision is also the Port action run's own
    # approval, so the record exists where a reviewer expects to find it. The
    # gate above does not depend on it: the run-status vocabulary around
    # approvals is the one shape here that was not verified against a live org,
    # and a gate should not rest on an unverified shape.
    if action_run and client.mode == "live":
        client.call("action_run_approve", run=action_run,
                    body={"status": "APPROVE" if granted else "DECLINE",
                          "description": reason or ("approved by %s" % who)})
    return {"approval": approval_id(run), "state": state, "by": who, "mode": client.mode}


def grant(client: PortClient, run: str, by: str, reason: str = "", action_run: str = "") -> dict:
    return decide(client, run, "granted", by, reason, action_run)


def deny(client: PortClient, run: str, by: str, reason: str = "", action_run: str = "") -> dict:
    return decide(client, run, "denied", by, reason, action_run)


# ---------------------------------------------------------------------------
# (d) release and seal
# ---------------------------------------------------------------------------

def release(client: PortClient, run: str) -> dict:
    """Release the run, or refuse with the reason. Four checks, no overrides.

    There is no force flag. A release path with an escape hatch is an escape
    hatch with a release path attached.
    """
    props = require_plan(client, run)                       # 1. governed at all
    appr = read_approval(client, run)
    if appr.get("state") != "granted":                      # 2. a human said yes
        raise NotApproved("run %s is %s, not granted" % (run, appr.get("state") or "unapproved"))
    if appr.get("plan_sha") != props.get("plan_sha"):       # 3. yes to THIS plan
        raise PlanDrift("approved plan %s but the run now carries %s"
                        % (appr.get("plan_sha", "-")[:12], (props.get("plan_sha") or "-")[:12]))
    card = evaluate(props)
    if not card["gold"]:                                    # 4. the gates agree
        raise ScorecardRed("scorecard is %s; failing: %s" % (card["level"], ", ".join(card["failed"])))

    ts = now_iso()
    client.call("entity_update", blueprint=BP_RUN, entity=run, body={
        "properties": {"status": "released", "released_at": ts}})
    return {"run": run, "released_at": ts, "level": card["level"],
            "approved_by": appr.get("decided_by", ""), "plan_sha": props.get("plan_sha", ""),
            "mode": client.mode}


def seal(client: PortClient, run: str) -> dict:
    """Close the run by writing the audit chain head onto it.

    The head recorded is the head at the moment of sealing; the seal itself
    appends one more record, which is why audit() reports both. Pinning a hash
    that included itself would be a nice-sounding impossibility.
    """
    chain = pclient.verify(client.spool)
    client.call("entity_update", blueprint=BP_RUN, entity=run,
                body={"properties": {"audit_head": chain["head"]}})
    after = pclient.verify(client.spool)
    return {"run": run, "sealed_at": now_iso(), "head_at_seal": chain["head"],
            "head_now": after["head"], "records": after["records"],
            "intact": after["intact"], "spool": client.spool, "mode": client.mode}


def audit(client: PortClient, run: str) -> dict:
    """Replay the chain and say what it proves — and what it does not.

    It proves this process's own sequence of requests was not reordered or
    edited after the fact. It proves nothing about Port's copy: for that you
    read Port. Two records claiming different modes in one run is the thing to
    look for, and it is reported rather than smoothed over.
    """
    rows = pclient.read(client.spool)
    chain = pclient.verify(client.spool)
    modes, methods = {}, {}
    for r in rows:
        modes[r.get("mode", "?")] = modes.get(r.get("mode", "?"), 0) + 1
        methods[r.get("method", "?")] = methods.get(r.get("method", "?"), 0) + 1
    events = [{"seq": r["seq"], "ts": r.get("ts"), "mode": r.get("mode"),
               "method": r.get("method"), "resource": r.get("resource"),
               "status": r.get("status"), "hash": r.get("hash", "")[:12]}
              for r in rows if r.get("method") != "GET"]
    return {"run": run, "spool": client.spool, "chain": chain, "modes": modes,
            "methods": methods, "writes": len(events), "reads": methods.get("GET", 0),
            "events": events,
            "proves": "the order and content of this factory's own requests",
            "does_not_prove": "anything about Port's stored copy — read Port for that"}


def status(client: PortClient, run: str) -> dict:
    """Everything a reviewer needs about one run, in one JSON object."""
    r = client.call("entity_get", blueprint=BP_RUN, entity=run)
    props = ((r.body.get("entity") or {}).get("properties") or {}) if r.ok else {}
    gates = gates_for(client, run)
    card = evaluate(props) if props else None
    return {
        "run": run,
        "found": bool(props),
        "properties": props,
        "gates": [{"name": g.get("name"), "passed": g.get("passed"),
                   "value": g.get("value"), "margin": g.get("margin")} for g in gates],
        "approval": read_approval(client, run),
        "scorecard": card,
        "releasable": bool(card and card["gold"]),
        "port": client.describe(),
    }


# ---------------------------------------------------------------------------
# the whole loop, for the demo and for the test suite
# ---------------------------------------------------------------------------

def run_loop(client: PortClient, run: str, gates: list[dict], brief: dict | None = None,
             lanes: list[dict] | None = None, requested_by: str = "labctl",
             timeout_s: float = APPROVAL_TIMEOUT_S, poll_s: float = POLL_S,
             sleep=time.sleep, clock=time.time) -> dict:
    """brief -> plan -> build -> test -> approval -> release -> audit, in order.

    `gates` are supplied by the caller because this module does not evaluate
    anything; hardware/, taste/ and the contract checker do that, and their
    results arrive here already decided.
    """
    out = {"plan": commit_plan(client, run, brief, lanes)}
    for g in gates:
        record_gate(client, run, g.pop("lane", "web-frontend"), **g)
    out["summary"] = sync(client, run)
    out["approval"] = open_approval(client, run, requested_by)
    decision = await_approval(client, run, timeout_s, poll_s, sleep, clock)
    out["decision"] = asdict(decision)
    if not decision.granted:
        out["released"] = False
        out["blocked_because"] = "approval %s" % decision.state
        out["audit"] = audit(client, run)
        return out
    out["release"] = release(client, run)
    out["released"] = True
    out["seal"] = seal(client, run)
    out["audit"] = audit(client, run)
    return out


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _markdown(plan: dict) -> str:
    lines = ["## Plan for run %s" % plan["run"], "", plan["brief"], "",
             "### Lanes"]
    lines += [("- `%s` -> %s %s" % (l["name"], l["agent"], l.get("model", ""))).rstrip()
              for l in plan["lanes"]]
    lines += ["", "### Gates committed before any agent spawns"]
    lines += ["- `%s`" % g for g in plan["gates"]]
    lines += ["", "FoS target %.2f - retry cap %d" % (plan["fos_target"], plan["retry_cap"])]
    return "\n".join(lines)


def _summary(client: PortClient, run: str, props: dict) -> str:
    gates = gates_for(client, run)
    bad = [g.get("name") for g in gates if not g.get("passed")]
    return ("Run %s - %d gates, %d failing%s - plan %s"
            % (run, len(gates), len(bad), (" (%s)" % ", ".join(bad)) if bad else "",
               (props.get("plan_sha") or "")[:12]))
