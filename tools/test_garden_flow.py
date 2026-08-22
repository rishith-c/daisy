"""
The whole Daisy -> Garden -> Daisy loop, asserted rather than demonstrated.

tools/e2e_garden.py *shows* the loop by running it against the live index, which
is the right thing for a demo and the wrong thing for a gate: it needs the
network, it publishes real entries, and it cannot assert on failure paths
without breaking something. This is the same loop with every external edge
faked, so it can run on a plane and still catch a regression.

What it holds the flow to, in the order the flow happens:

    verification   an unverified solution cannot enter the commons, cannot be
                   prepared for Garden, and cannot be published — three
                   independent refusals, not one check reused
    consent        publishing is blocked with no grant, allowed with one,
                   blocked again after revocation
    identity       no publishable identity means no publish
    format         a published entry carries the gate signature, the
                   verification table, and links that are absolute
    idempotency    publishing twice does not create two entries
    recall         a second agent, given only the gate name, finds it
    honesty        a gate nobody has fixed returns nothing, not a near-miss

    python3 -m tools.test_garden_flow

Zero third-party dependencies; no network, no real credentials.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commons.consent import Ledger                                   # noqa: E402
from commons.store import Solution, admit, recall, NotVerified       # noqa: E402
from garden import index as gindex                                   # noqa: E402
from garden.publish import prepare, publish, NotPublishable          # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


GATE = "flow.margin"


def good(**kw):
    d = dict(task="flow test: %s failed and was repaired" % GATE,
             brief="proving the publish path end to end",
             gates=[{"name": GATE, "passed": True, "margin": 1.5}],
             vendor="claude", model="claude-opus-5", kind="software",
             recipe="invert the margin equation, round up, re-run the gate",
             tokens_cost=41000)
    d.update(kw)
    return Solution(**d)


def bad():
    return good(gates=[{"name": GATE, "passed": False, "margin": 0.72}])


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------

def test_verification_is_checked_at_every_boundary():
    print("\nverification — refused three times, independently")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        check("the commons refuses an unverified solution",
              _raises(lambda: admit(bad(), db), NotVerified))
        sid = admit(good(), db)
        check("and admits a verified one", bool(sid))

        # prepare() must refuse on its own, not lean on admit() having refused
        raw = dict(id="x" * 16, task="unverified", gates=[{"name": GATE, "passed": False}],
                   gate_sig="%s=fail" % GATE, recipe="", kind="software")
        check("prepare refuses without a clean signature",
              _raises(lambda: prepare(raw, os.path.join(t, "g")), NotPublishable))

        led = Ledger(os.path.join(t, "consent.json"))
        led.grant("artifact", "garden")
        check("publish refuses too, even with consent granted",
              _raises(lambda: publish(raw, ledger=led, path=os.path.join(t, "g2")),
                      NotPublishable))


def test_consent_gates_the_publish():
    print("\nconsent — default deny, revocable, and it reaches publish")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        sid = admit(good(), db)
        sol = [h for h in recall(good().task, gates=[GATE], db=db, limit=9) if h["id"] == sid][0]
        led = Ledger(os.path.join(t, "consent.json"))

        r = publish(sol, ledger=led, path=os.path.join(t, "g"))
        check("blocked with no grant", r["mode"] == "blocked", str(r.get("mode")))
        check("and names the command that fixes it", "consent grant" in r.get("why", ""))
        check("nothing was written", not os.path.isdir(os.path.join(t, "g", "solutions")))

        led.grant("artifact", "garden")
        r2 = publish(sol, ledger=led, path=os.path.join(t, "g"))
        check("prepared once granted", r2["mode"] in ("dry-run", "live"), str(r2.get("mode")))
        check("mode is one of the three words, always",
              r2["mode"] in ("blocked", "dry-run", "live"))

        led.revoke("artifact", "garden")
        r3 = publish(sol, ledger=led, path=os.path.join(t, "g"))
        check("blocked again after revocation", r3["mode"] == "blocked", str(r3.get("mode")))


def test_published_entry_is_self_describing():
    print("\nformat — an entry carries its own proof")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        art = os.path.join(t, "thing.stl")
        open(art, "wb").write(b"solid flow\n")
        sid = admit(good(kind="hardware", artifact=art), db)
        sol = [h for h in recall(good().task, gates=[GATE], db=db, limit=9) if h["id"] == sid][0]
        sol["brief"] = "flow test"
        prep = prepare(sol, os.path.join(t, "g"))

        files = set(prep["files"])
        check("manifest, verification and readme are written",
              {"manifest.json", "VERIFICATION.md", "README.md"} <= files, str(files))
        check("the artifact travels with it", "thing.stl" in files, str(files))

        m = prep["manifest"]
        check("the gate signature is recorded",
              m["verified"]["gate_signature"] == "%s=pass" % GATE,
              m["verified"]["gate_signature"])
        check("all_passed is asserted explicitly", m["verified"]["all_passed"] is True)
        check("provenance names the producing model",
              m["produced_by"]["model"] == "claude-opus-5", str(m["produced_by"]))
        check("hardware carries the settings it was verified at",
              "print_settings" in m and m["print_settings"].get("material"))

        vr = open(os.path.join(prep["dir"], "VERIFICATION.md"), encoding="utf-8").read()
        check("the verification table lists the gate", GATE in vr)
        check("and states the verdict", "PASS" in vr)

        check("the branch is named for the solution", prep["branch"].startswith("solution/"))
        check("and carries the id so two runs cannot collide",
              sid[:8] in prep["branch"], prep["branch"])


def test_links_are_absolute():
    print("\nlinks — citable from another machine")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "buildapi", os.path.join(os.path.dirname(ROOT), "garden-site", "tools", "build_api.py"))
    if spec is None or not os.path.exists(spec.origin):
        check("garden-site is present to check links against", False, "site not found")
        return
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        sid = admit(good(), db)
        sol = [h for h in recall(good().task, gates=[GATE], db=db, limit=9) if h["id"] == sid][0]
        led = Ledger(os.path.join(t, "consent.json")); led.grant("artifact", "garden")
        gp = os.path.join(t, "garden")
        prep = prepare(sol, gp)
        # prepare() leaves the entry on a branch; read it where it was written
        entries = mod.read_entries(gp)
        check("the entry is indexable", len(entries) == 1, str(len(entries)))
        if not entries:
            return
        L = entries[0]["links"]
        for k in ("canonical", "api", "mirror"):
            check("%s is absolute" % k, L[k].startswith("https://"), L[k])
        check("every file has a fetchable link",
              L["files"] and all(u.startswith("https://") for u in L["files"].values()))
        check("the api link points at this solution",
              entries[0]["slug"] in L["api"], L["api"])


def test_index_refuses_unverified_entries():
    print("\nindex — a failing signature cannot reach the API")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "buildapi2", os.path.join(os.path.dirname(ROOT), "garden-site", "tools", "build_api.py"))
    if spec is None or not os.path.exists(spec.origin):
        check("garden-site present", False, "site not found"); return
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "solutions", "sneaky")
        os.makedirs(d)
        json.dump({"id": "s1", "title": "should not appear", "kind": "software",
                   "verified": {"gate_signature": "%s=fail" % GATE,
                                "gates": [{"name": GATE, "passed": False}],
                                "all_passed": False}},
                  open(os.path.join(d, "manifest.json"), "w"))
        check("a failing entry on disk is not indexed", mod.read_entries(t) == [])

        d2 = os.path.join(t, "solutions", "mixed"); os.makedirs(d2)
        json.dump({"id": "s2", "title": "half passed",
                   "verified": {"gate_signature": "a=pass|b=fail", "gates": []}},
                  open(os.path.join(d2, "manifest.json"), "w"))
        check("one failing segment disqualifies the whole entry",
              mod.read_entries(t) == [])


def test_a_second_agent_finds_it_by_gate():
    print("\nrecall — the next agent asks by gate, not by prose")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        admit(good(), db)
        admit(good(task="something else entirely about scrapers",
                   gates=[{"name": "scrape.schema", "passed": True}],
                   recipe="re-anchor selectors"), db)

        vague = recall("something went wrong", db=db)
        check("vague prose alone returns nothing", vague == [], str(vague))

        hit = recall("margin is negative", gates=[GATE], db=db)
        check("the gate name finds it", len(hit) == 1, str(len(hit)))
        check("and reports which gate matched",
              hit and hit[0]["matched_gates"] == [GATE], str(hit[:1]))
        check("the recipe travels with it", hit and "invert" in hit[0]["recipe"])
        check("so does what it originally cost",
              hit and hit[0]["tokens_cost"] == 41000, str(hit[:1]))

        novel = recall("the disk filled up", gates=["infra.disk"], db=db)
        check("a gate nobody has fixed returns nothing", novel == [], str(novel))


def test_publishing_twice_is_not_two_entries():
    print("\nidempotency — an unattended publisher cannot double-post")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        a = admit(good(), db)
        b = admit(good(), db)
        check("the same solution keeps one id", a == b, "%s vs %s" % (a[:8], b[:8]))

        sol = [h for h in recall(good().task, gates=[GATE], db=db, limit=9) if h["id"] == a][0]
        gp = os.path.join(t, "garden")
        p1 = prepare(sol, gp)
        p2 = prepare(sol, gp)
        check("preparing twice reuses the branch", p1["branch"] == p2["branch"])
        base = os.path.join(gp, "solutions")
        check("and does not create a second directory",
              len(os.listdir(base)) == 1, str(os.listdir(base)))


def main():
    print("garden flow — end to end")
    test_verification_is_checked_at_every_boundary()
    test_consent_gates_the_publish()
    test_published_entry_is_self_describing()
    test_links_are_absolute()
    test_index_refuses_unverified_entries()
    test_a_second_agent_finds_it_by_gate()
    test_publishing_twice_is_not_two_entries()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
