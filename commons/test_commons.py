"""Tests for the Verified Commons.

    python3 -m commons.test_commons

Every case builds its own database and ledger in a tempdir. Nothing here reads
or writes the developer's real commons, and nothing touches the network.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from .consent import Ledger, SCOPES
from .store import (Solution, admit, recall, record_reuse, stats, withdraw_all,
                    NotVerified, EVIDENCE_FLOOR, connect)
from .publish import bundle, publish, ConsentRequired, BAMBU_DEFAULTS

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


def bracket(**kw):
    d = dict(task="size a cantilever bracket web for FoS 1.5 under a 2.4 kg tip load",
             gates=[{"name": "physics.bend", "passed": True, "margin": 1.5},
                    {"name": "physics.mass", "passed": True}],
             vendor="claude", model="claude-opus-5", kind="hardware",
             recipe="invert sigma = 6M/(b t^2) for t at FoS; round up", tokens_cost=48000)
    d.update(kw)
    return Solution(**d)


# ---------------------------------------------------------------------------

def test_consent_defaults():
    print("\nconsent — default deny")
    with tempfile.TemporaryDirectory() as t:
        led = Ledger(os.path.join(t, "c.json"))
        check("nothing is granted before anyone agrees",
              all(not led.allows(s) for s in SCOPES), str(led.state()))
        check("an absent ledger file is not an error", led.grants == [])
        led.grant("local")
        check("a grant takes effect", led.allows("local"))
        check("and does not leak into other scopes", not led.allows("artifact"))

        led2 = Ledger(os.path.join(t, "c.json"))
        check("grants survive a reload", led2.allows("local"))

        with open(os.path.join(t, "bad.json"), "w") as fh:
            fh.write("{ not json")
        check("an unreadable ledger grants nothing rather than everything",
              not Ledger(os.path.join(t, "bad.json")).allows("local"))


def test_consent_targets_and_revocation():
    print("\nconsent — targets and revocation")
    with tempfile.TemporaryDirectory() as t:
        led = Ledger(os.path.join(t, "c.json"))
        led.grant("artifact", target="makerworld")
        check("a targeted grant covers its target", led.allows("artifact", "makerworld"))
        check("and no other target", not led.allows("artifact", "thingiverse"))
        led.revoke("artifact", target="makerworld")
        check("revocation takes effect", not led.allows("artifact", "makerworld"))
        check("revocation is recorded with a time", led.revoked_since("artifact") > 0)
        led.grant("artifact")
        check("a later untargeted grant re-enables every target",
              led.allows("artifact", "thingiverse"))


def test_admission_requires_passing_gates():
    print("\nadmission — verification is the membership test")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        sid = admit(bracket(), db)
        check("a fully-passing solution is admitted", bool(sid))

        failing = bracket(gates=[{"name": "physics.bend", "passed": False, "margin": 0.72}])
        check("a failing gate is refused",
              _raises(lambda: admit(failing, db), NotVerified))
        mixed = bracket(gates=[{"name": "physics.bend", "passed": True},
                               {"name": "taste.t1", "passed": False}])
        check("one failure among passes is still a refusal",
              _raises(lambda: admit(mixed, db), NotVerified))
        check("no gate results at all is a refusal",
              _raises(lambda: admit(bracket(gates=[]), db), NotVerified))
        check("only the admitted one is stored", stats(db)["solutions"] == 1,
              str(stats(db)["solutions"]))


def test_signature_and_identity():
    print("\ngate signature")
    s = bracket()
    check("signature is sorted by gate name",
          s.signature() == "physics.bend=pass|physics.mass=pass", s.signature())
    flipped = bracket(gates=list(reversed(s.gates)))
    check("gate order does not change the signature", flipped.signature() == s.signature())
    check("a failure changes the signature",
          bracket(gates=[{"name": "physics.bend", "passed": False}]).signature() != s.signature())
    check("identity is stable across runs", bracket().ident() == bracket().ident())
    check("a different recipe is a different solution",
          bracket(recipe="something else").ident() != s.ident())


def test_recall_uses_gate_names():
    print("\nrecall — gate names carry the weak-text case")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        admit(bracket(), db)
        admit(Solution(task="vendor table restructured, selectors returned fewer keys per row",
                       gates=[{"name": "scrape.schema", "passed": True}],
                       vendor="codex", model="gpt-5.6-sol",
                       recipe="re-anchor selectors on last-good values",
                       tokens_cost=31000), db)

        vague = recall("my bracket bends too much", db=db)
        check("vague text alone returns nothing rather than guessing",
              vague == [], str(vague))

        withgate = recall("my bracket bends too much", gates=["physics.bend"], db=db)
        check("the same query with a gate name finds it", len(withgate) == 1, str(len(withgate)))
        check("and names which gate matched",
              withgate and withgate[0]["matched_gates"] == ["physics.bend"])
        check("the score clears the evidence floor",
              withgate and withgate[0]["score"] >= EVIDENCE_FLOOR)

        other = recall("scraper broke", gates=["scrape.schema"], db=db)
        check("a different gate retrieves the other solution",
              other and other[0]["vendor"] == "codex", str(other[:1]))

        novel = recall("the ci runner ran out of disk space", gates=["infra.disk"], db=db)
        check("a genuinely novel failure returns nothing", novel == [], str(novel))

        check("kind filters", recall("bracket", gates=["physics.bend"], kind="software", db=db) == [])


def test_reuse_accounting():
    print("\nreuse — savings are measured, not estimated")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        sid = admit(bracket(tokens_cost=48000), db)
        check("a fresh solution has no reuses", stats(db)["reuses"] == 0)
        record_reuse(sid, 48000, db)
        record_reuse(sid, 48000, db)
        st = stats(db)
        check("reuses accumulate", st["reuses"] == 2, str(st["reuses"]))
        check("tokens saved is the real cost of the original run, twice",
              st["tokens_saved"] == 96000, str(st["tokens_saved"]))
        check("investment is reported alongside the saving",
              st["tokens_invested"] == 48000, str(st["tokens_invested"]))
        check("vendors are counted", st["vendors"] == {"claude": 1}, str(st["vendors"]))


def test_publish_is_consent_gated():
    print("\npublish — two independent gates")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        led = Ledger(os.path.join(t, "c.json"))
        sid = admit(bracket(), db)
        sol = recall("bracket", gates=["physics.bend"], db=db)[0]
        sol["brief"] = "SR-11 mount"

        res = publish(sol, os.path.join(t, "out"), ledger=led)
        check("without consent the publish is blocked", res["mode"] == "blocked", str(res))
        check("and nothing was written", not os.path.exists(os.path.join(t, "out")))
        check("the block explains how to grant", "consent grant" in res["reason"])

        led.grant("artifact", target="makerworld")
        res2 = publish(sol, os.path.join(t, "out"), ledger=led)
        check("with consent it bundles", res2["mode"] == "dry-run", str(res2["mode"]))
        check("but posts nothing without credentials", res2["reason"] == "no credentials")
        check("even asking for live stays dry with no token",
              publish(sol, os.path.join(t, "o2"), ledger=led, live=True)["mode"] == "dry-run")

        led.revoke("artifact", target="makerworld")
        check("revoking blocks it again",
              publish(sol, os.path.join(t, "o3"), ledger=led)["mode"] == "blocked")

        unverified = dict(sol, gates=[{"name": "physics.bend", "passed": False}])
        check("unverified work is refused even with consent granted",
              _raises(lambda: publish(unverified, os.path.join(t, "o4"), ledger=led),
                      ConsentRequired))


def test_bundle_contents():
    print("\nbundle — what a reprint needs")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        art = os.path.join(t, "bracket.stl")
        open(art, "wb").write(b"solid\n")
        admit(bracket(artifact=art), db)
        sol = recall("bracket", gates=["physics.bend"], db=db)[0]
        out = bundle(sol, os.path.join(t, "b"))
        files = set(os.listdir(out))
        check("manifest, verification and readme are written",
              {"manifest.json", "VERIFICATION.md", "README.md"} <= files, str(files))
        check("the artifact is copied in", "bracket.stl" in files, str(files))
        man = json.load(open(os.path.join(out, "manifest.json")))
        check("the manifest records that every gate passed", man["verified"]["all_passed"])
        check("it carries the gate signature",
              man["verified"]["gate_signature"] == "physics.bend=pass|physics.mass=pass",
              man["verified"]["gate_signature"])
        check("hardware bundles carry the print settings they were verified at",
              man["print_settings"]["material"] == BAMBU_DEFAULTS["material"])
        check("including orientation, which the bending gate depends on",
              "orientation" in man["print_settings"])
        check("provenance names the producing agent",
              man["produced_by"]["model"] == "claude-opus-5")
        vr = open(os.path.join(out, "VERIFICATION.md")).read()
        check("the verification table lists every gate",
              "physics.bend" in vr and "physics.mass" in vr)


def test_withdrawal():
    print("\nwithdrawal — revocation reaches published work")
    with tempfile.TemporaryDirectory() as t:
        db = os.path.join(t, "c.db")
        sid = admit(bracket(), db)
        con = connect(db)
        con.execute("UPDATE solution SET published = 'makerworld' WHERE id = ?", (sid,))
        con.commit(); con.close()
        check("a published entry is recallable", len(recall("bracket", gates=["physics.bend"], db=db)) == 1)
        n = withdraw_all(time.time(), db)
        check("revoking withdraws it", n == 1, str(n))
        check("withdrawn work leaves the commons",
              recall("bracket", gates=["physics.bend"], db=db) == [])
        check("and stops being counted", stats(db)["solutions"] == 0)


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


def main():
    print("verified commons — test suite")
    test_consent_defaults()
    test_consent_targets_and_revocation()
    test_admission_requires_passing_gates()
    test_signature_and_identity()
    test_recall_uses_gate_names()
    test_reuse_accounting()
    test_publish_is_consent_gated()
    test_bundle_contents()
    test_withdrawal()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
