"""Tests for the Port client, the blueprint model, and the governed loop.

    python3 -m port.test_port

No network, no credentials, no fixtures downloaded at import time. The HTTP
layer is exercised through an injected transport that obeys the same five
argument contract as the real one, so what runs here is the shipping retry
loop, the shipping header assembly and the shipping JSON encoding — not a
stand-in for them.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time

from . import blueprints as B
from . import client as C
from . import factory as F
from .client import AuthError, PortClient, PortError, TransportError

PASS, FAIL = 0, 0
TMP = ""


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# the fake transport — a transport, not a mock of the client
# ---------------------------------------------------------------------------

TOKEN = ""          # filled in below, once _jwt exists
DEFAULT_OK = None


class Fake:
    """Queued responses, plus a log of exactly what was sent.

    Each queued item is (status, headers, json_body) or an Exception to raise.
    When the queue runs dry it answers with `default`, which carries a token
    so that a refresh happening mid-test does not have to be scripted into
    every queue. A test only scripts the responses it actually cares about.
    """

    def __init__(self, *responses, default=None):
        self.queue = list(responses)
        self.default = default or DEFAULT_OK
        self.calls = []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body.decode()) if body else None})
        item = self.queue.pop(0) if self.queue else self.default
        if isinstance(item, Exception):
            raise item
        status, hdrs, payload = item
        return status, hdrs, json.dumps(payload).encode("utf-8")

    @property
    def paths(self):
        return [c["url"].split("/v1", 1)[-1] for c in self.calls]


class Clock:
    """Time that only moves when something sleeps. Makes backoff assertable."""

    def __init__(self, t=1700000000.0):
        self.t = t
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def _jwt(exp: float) -> str:
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return "%s.%s.notasignature" % (seg({"alg": "HS256"}), seg({"exp": exp}))


def _spool(name: str) -> str:
    return os.path.join(TMP, "%s-%d.jsonl" % (name, time.time_ns()))


def _live(fake, clock=None, name="live", **kw):
    clock = clock or Clock()
    return PortClient("cid", "csecret", transport=fake, base="https://port.test/v1",
                      spool=_spool(name), sleep=clock.sleep, clock=clock.now, **kw), clock


def _dry(name="dry", **kw):
    return PortClient(force_dry=True, spool=_spool(name), **kw)


def _raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


TOKEN = _jwt(1700003600.0)
TOKEN_OK = (200, {}, {"ok": True, "accessToken": TOKEN, "expiresIn": 3600})
DEFAULT_OK = TOKEN_OK


# ---------------------------------------------------------------------------

def test_auth():
    print("\nauth — one token, cached until it is nearly stale")
    f = Fake(TOKEN_OK)
    c, clock = _live(f)
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("the token is exchanged before anything else", f.paths[0] == "/auth/access_token")
    check("credentials go in the body, camelCase, as Port documents",
          f.calls[0]["body"] == {"clientId": "cid", "clientSecret": "csecret"},
          str(f.calls[0]["body"]))
    check("the JWT comes back under accessToken", c._token == TOKEN)
    check("later calls carry the bearer header",
          f.calls[1]["headers"].get("Authorization") == "Bearer " + TOKEN)

    before = len(f.calls)
    for _ in range(3):
        c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("the token is cached, not re-exchanged", len(f.calls) == before + 3,
          "%d calls" % (len(f.calls) - before))

    clock.sleep(3600)                     # past expiry - skew
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("an expiring token is refreshed early", "/auth/access_token" in f.paths[-2:])

    f2 = Fake((200, {}, {"ok": True, "accessToken": _jwt(1700000120.0)}))
    c2, k2 = _live(f2, name="jwtexp")
    c2.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("with no expiresIn the JWT's own exp claim is used",
          abs(c2._token_exp - 1700000120.0) < 1.0, str(c2._token_exp))
    k2.sleep(70)                          # inside the 60 s skew of a 120 s token
    c2.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("and it refreshes on that claim", f2.paths.count("/auth/access_token") == 2)

    bad = Fake((401, {}, {"ok": False, "error": "invalid_credentials"}))
    cb, _ = _live(bad, name="bad")
    check("bad credentials raise AuthError, not a generic PortError",
          _raises(lambda: cb.token(), AuthError))
    check("a token response with no accessToken is an auth failure, not an empty string",
          _raises(lambda: _live(Fake(default=(200, {}, {"ok": True})),
                                name="empty")[0].token(), AuthError))
    check("dry mode never mints a token", _raises(lambda: _dry().token(), PortError))


def test_retry():
    print("\nretry — 5xx and 429 only, with a schedule you can assert")
    f = Fake(TOKEN_OK, (503, {}, {}), (200, {}, {"ok": True}))
    c, clock = _live(f, name="retry")
    r = c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("a 503 is retried and the retry wins", r.ok and r.attempts == 2, str(r.status))
    check("it backed off once, by the base delay", clock.slept == [C.BACKOFF_BASE_S],
          str(clock.slept))

    f = Fake(TOKEN_OK, (500, {}, {}), (502, {}, {}), (504, {}, {}), (200, {}, {"ok": True}))
    c, clock = _live(f, name="backoff")
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("the backoff doubles", clock.slept == [0.5, 1.0, 2.0], str(clock.slept))

    f = Fake(TOKEN_OK, (429, {"x-ratelimit-reset": "7"}, {}), (200, {}, {"ok": True}))
    c, clock = _live(f, name="ratelimit")
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("429 waits for x-ratelimit-reset — Port documents no Retry-After",
          clock.slept == [7.0], str(clock.slept))

    f = Fake(TOKEN_OK, (429, {"retry-after": "3", "x-ratelimit-reset": "7"}, {}),
             (200, {}, {"ok": True}))
    c, clock = _live(f, name="retryafter")
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("a proxy's Retry-After is preferred when both are present",
          clock.slept == [3.0], str(clock.slept))

    f = Fake(TOKEN_OK, (429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, {}),
             (200, {}, {"ok": True}))
    c, clock = _live(f, name="httpdate")
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("an HTTP-date Retry-After falls back to the schedule rather than crashing",
          clock.slept == [C.BACKOFF_BASE_S], str(clock.slept))

    f = Fake(TOKEN_OK, (429, {"x-ratelimit-reset": "9999"}, {}), (200, {}, {"ok": True}))
    c, clock = _live(f, name="cap")
    c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("a server asking for hours is capped", clock.slept == [C.RETRY_AFTER_CAP_S],
          str(clock.slept))

    f = Fake(TOKEN_OK, (404, {}, {"ok": False}))
    c, clock = _live(f, name="notfound")
    r = c.call("entity_get", blueprint=B.BP_RUN, entity="nope")
    check("a 404 is never retried — the request is wrong, not unlucky",
          r.status == 404 and r.attempts == 1 and clock.slept == [])

    f = Fake(TOKEN_OK, *[(503, {}, {})] * 8)
    c, clock = _live(f, name="giveup")
    r = c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("it gives up after MAX_ATTEMPTS and returns the failure",
          r.status == 503 and r.attempts == C.MAX_ATTEMPTS, str(r.attempts))

    boom = TransportError(0, "https://port.test", "connection reset")
    f = Fake(TOKEN_OK, boom, (200, {}, {"ok": True}))
    c, clock = _live(f, name="flaky")
    r = c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("a dead socket is retried — venue wifi is the expected case", r.ok)
    f = Fake(TOKEN_OK, boom, boom, boom, boom)
    c, _ = _live(f, name="dead")
    check("a permanently dead socket surfaces as TransportError",
          _raises(lambda: c.call("entity_get", blueprint=B.BP_RUN, entity="1042"),
                  TransportError))


def test_dry_mode():
    print("\ndry mode — a replayable spool, never a fabricated success")
    f = Fake()
    c = PortClient("", "", transport=f, spool=_spool("drymode"))
    check("no credentials means dry", c.mode == "dry")
    c.call("entity_create", blueprint=B.BP_RUN, params=C.UPSERT,
           body={"identifier": "1042", "properties": {"status": "planned"}})
    check("dry mode touches no transport at all", f.calls == [] and c.sent == 0)
    check("describe() states plainly that nothing was sent",
          "NO network call" in c.describe()["note"] and c.describe()["requests_sent"] == 0)

    rows = C.read(c.spool)
    check("the request is spooled with its real method and path",
          rows[0]["method"] == "POST"
          and rows[0]["path"] == "/blueprints/daisy_run/entities", str(rows[0]["path"]))
    check("and with the body it would have sent",
          rows[0]["body"]["properties"]["status"] == "planned")

    r = c.call("entity_get", blueprint=B.BP_RUN, entity="1042")
    check("a read replays the write", r.ok and r.body["entity"]["properties"]["status"] == "planned")
    check("and says it came from the spool, not the network", r.mode == "dry" and r.served_from)

    miss = c.call("entity_get", blueprint=B.BP_RUN, entity="never-written")
    check("an unwritten resource is 404, not an empty success", miss.status == 404)
    check("the 404 explains itself", "no record of" in miss.body.get("message", ""))

    c.call("entity_update", blueprint=B.BP_RUN, entity="1042",
           body={"properties": {"approved": True}})
    merged = c.call("entity_get", blueprint=B.BP_RUN, entity="1042").body["entity"]["properties"]
    check("PATCH merges into POST the way Port's upsert does",
          merged == {"status": "planned", "approved": True}, str(merged))

    forced = PortClient("cid", "csecret", transport=Fake(), spool=_spool("forced"), force_dry=True)
    check("credentials can be overridden to dry — bad wifi is a decision, not a crash",
          forced.mode == "dry")
    half = PortClient("cid", "", transport=Fake(), spool=_spool("half"))
    check("half a credential is dry, never a half-live claim", half.mode == "dry")


def test_secrets():
    print("\nsecrets — the spool ends up on a projector")
    f = Fake(TOKEN_OK)
    c, _ = _live(f, name="secret")
    c.token()
    blob = open(c.spool).read()
    check("the client secret never reaches the spool", "csecret" not in blob)
    check("the placeholder says what happened", C.MASK in blob)
    check("but the request itself was really sent with it",
          f.calls[0]["body"]["clientSecret"] == "csecret")
    nested = C._redact({"a": {"authorization": "Bearer x", "b": [{"api_key": "k"}]}})
    check("redaction recurses into nested bodies and lists",
          nested["a"]["authorization"] == C.MASK and nested["a"]["b"][0]["api_key"] == C.MASK)


def test_chain():
    print("\nspool — an append-only chain that reports tampering")
    p = _spool("chain")
    for i in range(5):
        C.append(p, {"method": "POST", "path": "/x", "resource": "r%d" % i, "status": 201})
    v = C.verify(p)
    check("a fresh chain verifies", v["intact"] and v["records"] == 5, str(v))
    check("sequence numbers are dense and ordered",
          [r["seq"] for r in C.read(p)] == [1, 2, 3, 4, 5])
    check("the first record chains to genesis", C.read(p)[0]["prev"] == C.GENESIS)

    rows = C.read(p)
    rows[2]["status"] = 500                       # edit history
    open(p, "w").write("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    v = C.verify(p)
    check("editing a record breaks the chain there", not v["intact"] and v["broken_at"] == 3,
          str(v))

    rows = C.read(p)
    del rows[1]                                   # remove history
    open(p, "w").write("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    check("deleting a record breaks it too", not C.verify(p)["intact"])
    check("an absent spool is an empty chain, not a crash",
          C.verify(os.path.join(TMP, "nothing.jsonl"))["records"] == 0)


def test_blueprint_model():
    print("\nblueprints — the model, checked against Port's rules and its own")
    ids = [b["identifier"] for b in B.BLUEPRINTS.values()]
    check("eight blueprints, all uniquely identified", len(ids) == len(set(ids)) == 8, str(ids))
    check("every identifier is namespaced so a shared org cannot collide",
          all(i.startswith("daisy_") for i in ids))

    ok_types = {"string", "number", "boolean", "object", "array"}
    for bp in B.BLUEPRINTS.values():
        i = bp["identifier"]
        check("%s declares identifier, title and schema" % i,
              all(k in bp for k in ("identifier", "title", "schema")))
        props = bp["schema"]["properties"]
        check("%s uses only Port's five property types" % i,
              all(p["type"] in ok_types for p in props.values()),
              str([p.get("type") for p in props.values()]))
        check("%s expresses enums as typed strings, not a made-up enum type" % i,
              all(p["type"] == "string" for p in props.values() if "enum" in p))
        check("%s only requires properties it declares" % i,
              set(bp["schema"].get("required", [])) <= set(props))
        for rel, spec in bp.get("relations", {}).items():
            check("%s.%s targets a blueprint that exists" % (i, rel),
                  spec["target"] in B.BLUEPRINTS, spec["target"])
            check("%s.%s declares target, many and required" % (i, rel),
                  all(k in spec for k in ("target", "many", "required")))

    check("ORDER covers every blueprint exactly once",
          sorted(B.ORDER) == sorted(ids), str(B.ORDER))
    for bp in B.BLUEPRINTS.values():
        for rel, spec in bp.get("relations", {}).items():
            check("%s is created after %s, which it points at"
                  % (bp["identifier"], spec["target"]),
                  B.ORDER.index(spec["target"]) < B.ORDER.index(bp["identifier"]))

    run = B.BLUEPRINTS[B.BP_RUN]
    check("Run has many Lanes", run["relations"]["lanes"]["many"] is True)
    check("Lane has many Gates", B.BLUEPRINTS[B.BP_LANE]["relations"]["gates"]["many"] is True)
    check("a Gate may have one Repair, and may not",
          B.BLUEPRINTS[B.BP_GATE]["relations"]["repair"]["many"] is False
          and B.BLUEPRINTS[B.BP_GATE]["relations"]["repair"]["required"] is False)
    check("the approval relation is optional in the schema, because the plan is "
          "committed before the approval exists",
          run["relations"]["approval"]["required"] is False)
    check("a Run requires a Brief", run["relations"]["brief"]["required"] is True)

    svc = B.BLUEPRINTS[B.BP_SERVICE]["schema"]
    check("every catalogued service must state a criticality and a failure mode",
          "criticality" in svc["required"] and "failure_mode" in svc["properties"])
    check("six services are catalogued", len(B.SERVICES) == 6)
    check("each carries a failure mode and a fallback, not just a name",
          all(s["properties"].get("failure_mode") and s["properties"].get("fallback")
              for s in B.SERVICES))
    check("the brief template carries goals, choices and risks",
          all(B.PROJECT.get(k) for k in ("goals", "technical_choices", "risks", "non_goals")))


def test_scorecard_definition():
    print("\nscorecard — the thresholds, in Port's language")
    levels = {lv["title"] for lv in B.SCORECARD["levels"]}
    run_props = set(B.BLUEPRINTS[B.BP_RUN]["schema"]["properties"])
    ids = [r["identifier"] for r in B.SCORECARD["rules"]]
    check("rule identifiers are unique", len(ids) == len(set(ids)))
    for rule in B.SCORECARD["rules"]:
        check("rule %s sits at a declared level" % rule["identifier"], rule["level"] in levels)
        for cond in rule["query"]["conditions"]:
            check("rule %s reads a property Run actually declares" % rule["identifier"],
                  cond["property"] in run_props, cond["property"])
            check("rule %s uses an operator the evaluator implements" % rule["identifier"],
                  _supported(cond["operator"]), cond["operator"])

    def val(rid):
        return B.SCORECARD["rules"][ids.index(rid)]["query"]["conditions"][0]["value"]

    check("the physics rule is the factor of safety the margins module enforces",
          val("physics_fos") == B.FOS_MIN == 1.5)
    check("the contrast rule is WCAG AA for normal text", val("wcag_contrast") == B.CONTRAST_MIN == 4.5)
    check("the taste rule is zero findings, not a score", val("taste_clean") == 0)
    check("the freshness rule is the 15 minute TTL from CLAUDE.md",
          val("scrape_fresh") == B.SCRAPE_TTL_S == 900)
    check("the human approval rule is the only Gold rule",
          [r["identifier"] for r in B.SCORECARD["rules"] if r["level"] == "Gold"]
          == ["human_approved"])


def _supported(op):
    try:
        B._cmp(op, 1, 1)
        return True
    except ValueError:
        return False
    except Exception:
        return True


GREEN = {"plan_sha": "abc", "gates_run": 6, "gates_failed": 0, "min_physics_fos": 1.5,
         "min_contrast_ratio": 4.5, "taste_findings": 0, "scrape_age_s": 900,
         "approved": True, "approved_by": "rishith"}


def test_scorecard_evaluation():
    print("\nscorecard — evaluated, at and either side of every threshold")
    check("a clean, approved run reaches Gold", B.evaluate(GREEN)["level"] == "Gold")
    check("the thresholds are inclusive at exactly the limit", B.evaluate(GREEN)["passed"])

    for prop, bad, rule in (("min_physics_fos", 1.49, "physics_fos"),
                            ("min_contrast_ratio", 4.49, "wcag_contrast"),
                            ("taste_findings", 1, "taste_clean"),
                            ("scrape_age_s", 901, "scrape_fresh")):
        out = B.evaluate(dict(GREEN, **{prop: bad}))
        check("%s=%s fails %s and forfeits Gold" % (prop, bad, rule),
              rule in out["failed"] and not out["gold"], str(out["failed"]))
        check("...and %s at that value drops the run to Bronze" % prop,
              out["level"] == "Bronze", out["level"])

    out = B.evaluate(dict(GREEN, approved=False, approved_by=""))
    check("an unapproved run stops at Silver", out["level"] == "Silver", out["level"])
    check("and it is the human rule that failed", out["failed"] == ["human_approved"])
    check("an approval with no name is not an approval",
          not B.evaluate(dict(GREEN, approved_by=""))["gold"])

    out = B.evaluate(dict(GREEN, gates_failed=1))
    check("one failed gate keeps the run off Bronze", out["level"] == "Basic", out["level"])

    missing = dict(GREEN)
    del missing["min_physics_fos"]
    out = B.evaluate(missing)
    check("a missing scalar fails closed — absent must never read as passing",
          "physics_fos" in out["failed"])
    check("zero would have passed, which is exactly why absent is not zero",
          B.evaluate(dict(GREEN, min_physics_fos=0))["failed"] == ["physics_fos"])
    check("an empty run scores nothing at all", B.evaluate({})["level"] == "Basic")

    check("an operator the evaluator does not implement raises rather than passing",
          _raises(lambda: B._cmp("approximately", 1, 1), ValueError))
    check("levels are cumulative: Gold needs Bronze and Silver too",
          B.evaluate(dict(GREEN, gates_run=0))["level"] == "Basic")


def test_plan_before_agents():
    print("\nthe plan gate — delete Port and no agent ever starts")
    c = _dry("plan")
    started = []
    check("with no plan, spawn raises rather than starting an agent",
          _raises(lambda: F.spawn(c, "1042", "hardware", lambda: started.append("agent")),
                  F.NotPlanned))
    check("and the agent callable was never invoked", started == [], str(started))

    out = F.commit_plan(c, "1042")
    check("committing the plan returns its hash", len(out["plan_sha"]) == 32)
    check("the plan is written before anything can run", out["status"] == 201)

    order = [r["resource"] for r in C.read(c.spool) if r["method"] == "POST"]
    check("the brief is written first", order[0].startswith("entity:daisy_brief:"))
    check("then the lanes", all(o.startswith("entity:daisy_lane:") for o in order[1:4]))
    check("and the Run last, once the things it points at exist",
          order[4] == "entity:daisy_run:1042", str(order[4:5]))

    check("with a plan committed, spawn runs the agent and returns its result",
          F.spawn(c, "1042", "hardware", lambda: "built") == "built")

    # Port itself removed: the transport never answers.
    boom = TransportError(0, "https://port.test", "name resolution failed")
    dead, _ = _live(Fake(TOKEN_OK, boom, boom, boom, boom, boom, boom), name="dead2")
    ran = []
    check("Port unreachable is NOT permission to proceed",
          _raises(lambda: F.spawn(dead, "1042", "hardware", lambda: ran.append(1)),
                  F.NotPlanned))
    check("no agent started against an unreachable governance plane", ran == [])

    gone, _ = _live(Fake(TOKEN_OK, (404, {}, {"ok": False})), name="gone")
    check("a deleted Run entity blocks the spawn just as hard",
          _raises(lambda: F.spawn(gone, "1042", "hardware", lambda: ran.append(1)),
                  F.NotPlanned))
    check("still nothing started", ran == [])

    hollow = _dry("hollow")
    hollow.call("entity_create", blueprint=B.BP_RUN, body={"identifier": "1042",
                                                           "properties": {"status": "planned"}})
    check("a Run with no plan hash is not a plan",
          _raises(lambda: F.require_plan(hollow, "1042"), F.NotPlanned))


def test_plan_hash():
    print("\nthe plan hash — what the human is actually approving")
    p1 = {"run": "1042", "gates": ["a", "b"], "lanes": []}
    check("the hash is stable across calls", F.plan_hash(p1) == F.plan_hash(p1))
    check("key order does not change it",
          F.plan_hash({"gates": ["a", "b"], "lanes": [], "run": "1042"}) == F.plan_hash(p1))
    check("changing a gate changes it",
          F.plan_hash({"run": "1042", "gates": ["a", "c"], "lanes": []}) != F.plan_hash(p1))
    a = F.commit_plan(_dry("h1"), "1042")["plan_sha"]
    b = F.commit_plan(_dry("h2"), "1042")["plan_sha"]
    check("the same plan hashes the same in two separate runs", a == b)
    c = F.commit_plan(_dry("h3"), "1042", gates=["taste.t1"])["plan_sha"]
    check("a different gate list is a different plan", c != a)


def test_gates():
    print("\ngates — recorded, repaired, and reduced to the scorecard's scalars")
    c = _dry("gates")
    F.commit_plan(c, "1042")
    out = F.record_gate(c, "1042", "hardware", "physics.bend", False, value=69.0,
                        allowable=50.0, margin=0.72, unit="MPa",
                        formula="sigma = 6M / (b*t^2)",
                        repair={"parameter": "web_thickness", "from": 3.2, "to": 4.61,
                                "derivation": "t = sqrt(6M / (b*sigma/FoS))", "kind": "algebra"})
    check("the gate is one entity per result", out["identifier"] == "1042/hardware/physics.bend")
    check("the kind is derived from the gate name", out["properties"]["kind"] == "physics")
    check("the formula is stored so a reviewer can redo the arithmetic",
          "6M" in out["properties"]["formula"])
    check("a repair becomes its own entity, linked from the gate",
          out["repair"] == "1042/hardware/physics.bend/repair")
    res = [r["resource"] for r in C.read(c.spool) if r["method"] == "POST"]
    check("the repair is written before the gate that points at it",
          res.index("entity:daisy_repair:1042/hardware/physics.bend/repair")
          < res.index("entity:daisy_gate:1042/hardware/physics.bend"))

    plain = F.record_gate(c, "1042", "web-frontend", "taste.t1", True, value=0)
    check("a gate with no repair links to none", plain["repair"] is None)

    F.record_gate(c, "1042", "hardware", "physics.bend", True, value=33.2, margin=1.51)
    names = [g["name"] for g in F.gates_for(c, "1042")]
    check("re-running a gate replaces its result rather than appending a second",
          names.count("physics.bend") == 1, str(names))
    check("and the replacement is the one that counts",
          [g for g in F.gates_for(c, "1042") if g["name"] == "physics.bend"][0]["passed"] is True)

    gates = [{"name": "physics.bend", "margin": 1.9, "passed": True},
             {"name": "physics.shear", "margin": 1.51, "passed": True},
             {"name": "taste.t2", "value": 4.9, "passed": True},
             {"name": "taste.t2", "value": 3.1, "passed": True},
             {"name": "taste.t1", "value": 0, "passed": True},
             {"name": "scrape.freshness", "value": 360, "passed": True},
             {"name": "contract.conformance", "passed": False}]
    s = F.summarise(gates)
    check("the worst physics margin is the one that counts", s["min_physics_fos"] == 1.51)
    check("the worst contrast pair is the one that counts", s["min_contrast_ratio"] == 3.1)
    check("taste findings are summed, not averaged", s["taste_findings"] == 0)
    check("the oldest scrape is the one that counts", s["scrape_age_s"] == 360)
    check("failures are counted", s["gates_failed"] == 1 and s["gates_run"] == 7)
    bare = F.summarise([{"name": "contract.conformance", "passed": True}])
    check("a scalar with no gate behind it is absent, not zero",
          "min_physics_fos" not in bare and "taste_findings" not in bare, str(bare))

    F.record_gate(c, "1042", "web-api", "contract.conformance", False)
    check("a failed gate marks the run blocked", F.sync(c, "1042")["status"] == "blocked")


def test_approval_blocks():
    print("\napproval — it blocks, and a timeout is not consent")
    c = _dry("approve")
    F.commit_plan(c, "1042")
    opened = F.open_approval(c, "1042", "labctl")
    check("the approval opens pending", opened["state"] == "pending")
    check("and is bound to the plan hash the human is shown",
          opened["plan_sha"] == F.require_plan(c, "1042")["plan_sha"])
    check("the run says it is waiting",
          F.require_plan(c, "1042")["status"] == "awaiting_approval")

    clock = Clock()
    d = F.await_approval(c, "1042", timeout_s=5.0, poll_s=1.0,
                         sleep=clock.sleep, clock=clock.now)
    check("with nobody deciding, it blocks until the timeout", d.state == "timeout")
    check("it really waited rather than returning on the first look", d.polls > 1, str(d.polls))
    check("a timeout is never a grant", not d.granted)
    check("and the release path refuses it", _raises(lambda: F.release(c, "1042"), F.NotApproved))

    # A sleeper that grants on the second poll: the decision arrives from
    # outside the loop, exactly as a second terminal would deliver it.
    class Granter:
        def __init__(self, after):
            self.n, self.after = 0, after

        def __call__(self, s):
            clock.sleep(s)
            self.n += 1
            if self.n == self.after:
                F.grant(c, "1042", by="rishith", reason="gates green")

    d = F.await_approval(c, "1042", timeout_s=60.0, poll_s=1.0,
                         sleep=Granter(2), clock=clock.now)
    check("it returns as soon as a human decides", d.granted and d.by == "rishith")
    check("it did not return before the decision existed", d.polls == 3, str(d.polls))
    check("the reason is carried through", d.reason == "gates green")

    c2 = _dry("deny")
    F.commit_plan(c2, "1042")
    F.open_approval(c2, "1042")
    F.deny(c2, "1042", by="rishith", reason="hardware margin too thin")
    k2 = Clock()
    d = F.await_approval(c2, "1042", timeout_s=5.0, poll_s=1.0, sleep=k2.sleep, clock=k2.now)
    check("a denial returns immediately", d.state == "denied" and d.polls == 1)
    check("a denied run does not release", _raises(lambda: F.release(c2, "1042"), F.NotApproved))
    check("and the run is marked denied", F.require_plan(c2, "1042")["status"] == "denied")


def test_self_approval():
    print("\napproval — the factory does not sign its own work")
    c = _dry("selfapprove")
    F.commit_plan(c, "1042")
    F.open_approval(c, "1042")
    check("an unnamed decider is refused",
          _raises(lambda: F.grant(c, "1042", by=""), F.SelfApproval))
    check("whitespace is not a name",
          _raises(lambda: F.grant(c, "1042", by="   "), F.SelfApproval))
    for who in ("labctl", "Claude", "CODEX", "daisy", "system", "ci"):
        check("%r cannot approve its own run" % who,
              _raises(lambda w=who: F.grant(c, "1042", by=w), F.SelfApproval))
    check("a decision must be granted or denied, not 'maybe'",
          _raises(lambda: F.decide(c, "1042", "maybe", "rishith"), ValueError))
    check("a named human can", F.grant(c, "1042", by="rishith")["state"] == "granted")


def test_release_refuses():
    print("\nrelease — four checks, and no force flag")
    c = _dry("release")
    F.commit_plan(c, "1042")
    for g in ({"name": "physics.bend", "margin": 1.51}, {"name": "taste.t1", "value": 0},
              {"name": "taste.t2", "value": 4.9}, {"name": "scrape.freshness", "value": 300}):
        F.record_gate(c, "1042", "hardware", g["name"], True, **{k: v for k, v in g.items()
                                                                 if k != "name"})
    F.sync(c, "1042")
    F.open_approval(c, "1042")
    check("pending is not approved", _raises(lambda: F.release(c, "1042"), F.NotApproved))
    F.grant(c, "1042", by="rishith")
    rel = F.release(c, "1042")
    check("granted, green and unchanged: it releases", rel["level"] == "Gold")
    check("and the run records when", F.require_plan(c, "1042")["status"] == "released")

    # plan drift — approve one plan, then change it
    d = _dry("drift")
    F.commit_plan(d, "1042")
    F.open_approval(d, "1042")
    F.grant(d, "1042", by="rishith")
    F.commit_plan(d, "1042", brief={"text": "actually, also ship the mobile app"})
    check("a plan edited after approval cannot be released",
          _raises(lambda: F.release(d, "1042"), F.PlanDrift))

    # red scorecard, despite a human saying yes
    r = _dry("red")
    F.commit_plan(r, "1042")
    F.record_gate(r, "1042", "hardware", "physics.bend", False, margin=0.72)
    F.sync(r, "1042")
    F.open_approval(r, "1042")
    F.grant(r, "1042", by="rishith")
    check("a human cannot approve past a red scorecard",
          _raises(lambda: F.release(r, "1042"), F.ScorecardRed))
    check("an unplanned run cannot be released at all",
          _raises(lambda: F.release(_dry("norun"), "1042"), F.NotPlanned))


def test_audit():
    print("\naudit — what the chain proves, and what it does not")
    c = _dry("audit")
    out = F.run_loop(c, "1042", [
        {"lane": "hardware", "name": "physics.bend", "passed": True, "margin": 1.51},
        {"lane": "web-frontend", "name": "taste.t1", "passed": True, "value": 0},
        {"lane": "web-frontend", "name": "taste.t2", "passed": True, "value": 4.9},
        {"lane": "hardware", "name": "scrape.freshness", "passed": True, "value": 300},
    ], sleep=_granter(c, "1042"), timeout_s=30.0, poll_s=1.0, clock=Clock().now)
    check("the whole loop runs: plan, gates, approval, release, audit", out["released"])
    check("the release is Gold", out["release"]["level"] == "Gold")
    check("the seal pins the chain head onto the run", len(out["seal"]["head_at_seal"]) == 32)
    check("the head moves after sealing, because the seal is itself a record",
          out["seal"]["head_now"] != out["seal"]["head_at_seal"])
    a = out["audit"]
    check("the chain verifies", a["chain"]["intact"])
    check("it counts writes and reads separately", a["writes"] > 0 and a["reads"] > 0)
    check("it reports which mode every record ran in", a["modes"] == {"dry": a["chain"]["records"]},
          str(a["modes"]))
    check("it says what it does not prove", "Port" in a["does_not_prove"])

    rows = C.read(c.spool)
    rows[3]["body"] = {"properties": {"status": "released"}}
    open(c.spool, "w").write("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    a2 = F.audit(c, "1042")
    check("a forged record is caught, and located",
          not a2["chain"]["intact"] and a2["chain"]["broken_at"] == 4, str(a2["chain"]))

    blocked = _dry("blocked")
    k = Clock()          # one clock: a sleeper that advances a different clock never times out
    out = F.run_loop(blocked, "1043", [{"name": "taste.t1", "passed": True, "value": 0}],
                     sleep=k.sleep, timeout_s=3.0, poll_s=1.0, clock=k.now)
    check("a run nobody approves ends blocked, not released", out["released"] is False)
    check("and says why", out["blocked_because"] == "approval timeout")
    check("an unreleased run still leaves an audit trail", out["audit"]["chain"]["intact"])


def _granter(client, run, after=2):
    state = {"n": 0}

    def sleeper(_s):
        state["n"] += 1
        if state["n"] == after:
            F.grant(client, run, by="rishith")
    return sleeper


def test_bootstrap():
    print("\nbootstrap — the model itself, pushed in an order Port accepts")
    c = _dry("bootstrap")
    out = F.bootstrap(c)
    check("every blueprint is pushed", len(out["blueprints"]) == 8)
    check("in dependency order", [b["identifier"] for b in out["blueprints"]] == B.ORDER)
    check("the scorecard lands on Run", out["scorecard"]["rules"] == len(B.SCORECARD["rules"]))
    check("the service catalog is seeded", len(out["services"]) == 6)
    paths = [r["path"] for r in C.read(c.spool)]
    check("the scorecard is created under its blueprint",
          "/blueprints/daisy_run/scorecards" in paths)
    check("the approval action is created", "/actions" in paths)

    f = Fake(TOKEN_OK, (409, {}, {"ok": False, "error": "already_exists"}),
             default=(201, {}, {"ok": True}))
    live, _ = _live(f, name="rebootstrap")
    out = F.bootstrap(live)
    check("a blueprint that already exists is updated, not abandoned",
          out["blueprints"][0]["action"] == "updated", str(out["blueprints"][0]))
    check("and the update is a PATCH to that blueprint",
          f.calls[2]["method"] == "PATCH" and f.calls[2]["url"].endswith("/blueprints/daisy_brief"),
          f.calls[2]["url"])
    ents = [c["url"] for c in f.calls if c["url"].endswith("entities?upsert=true&merge=true")]
    check("service entities are upserted, so a second bootstrap is safe",
          len(ents) == 6, str(len(ents)))


def test_routes():
    print("\nroutes — every path in one place, and every one of them used correctly")
    check("no module outside client.py spells a URL",
          all("api.getport.io" not in open(os.path.join(os.path.dirname(__file__), m)).read()
              for m in ("factory.py", "blueprints.py", "cli.py")))
    check("the token route is the one Port documents",
          C.ROUTES["token"] == ("POST", "/auth/access_token"))
    check("blueprints are created at the collection",
          C.ROUTES["blueprint_create"] == ("POST", "/blueprints"))
    check("entities are created under their blueprint",
          C.ROUTES["entity_create"] == ("POST", "/blueprints/{blueprint}/entities"))
    check("scorecards are blueprint-scoped",
          C.ROUTES["scorecard_create"][1] == "/blueprints/{blueprint}/scorecards")
    check("the approval endpoint is the action run's own",
          C.ROUTES["action_run_approve"] == ("PATCH", "/actions/runs/{run}/approval"))
    check("every route template formats without leftovers",
          all(_formats(t) for _, t in C.ROUTES.values()))
    c = _dry("routes")
    c.call("entity_create", blueprint="bp", params=C.UPSERT, body={"identifier": "e"})
    row = C.read(c.spool)[0]
    check("upsert is sent explicitly, because Port marks it required",
          C.UPSERT["upsert"] == "true" and C.UPSERT["merge"] == "true")
    check("the recorded path is the real one", row["path"] == "/blueprints/bp/entities")


def _formats(template):
    names = {"blueprint": "b", "entity": "e", "run": "r", "action": "a"}
    try:
        out = template.format(**names)
        return "{" not in out
    except KeyError:
        return False


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="daisy-port-test-")
    print("port — governance client, blueprints, and the governed loop")
    try:
        test_auth()
        test_retry()
        test_dry_mode()
        test_secrets()
        test_chain()
        test_blueprint_model()
        test_scorecard_definition()
        test_scorecard_evaluation()
        test_plan_before_agents()
        test_plan_hash()
        test_gates()
        test_approval_blocks()
        test_self_approval()
        test_release_refuses()
        test_audit()
        test_bootstrap()
        test_routes()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
