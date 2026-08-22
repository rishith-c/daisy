"""Tests for the precedent engine and the compaction ladder.

    python3 -m precedent.test_precedent

No test framework; stdlib only, like everything else here.
"""

from __future__ import annotations

import os
import tempfile
import time

from .engine import (Precedent, Case, GateResult, fingerprint, signature,
                     jaccard, embed, pack_bits, hamming, cosine, BYTES, DIM)
from .compact import compact, deterministic_clean, semantic_dedup, validate, build_probes

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------

def test_fingerprint():
    print("\nfingerprint — volatile tokens must collapse")
    a = "physics gate failed: sigma 212.4 MPa exceeds 172 MPa at /Users/x/runs/1042/bracket.py:88"
    b = "physics gate failed: sigma 198.1 MPa exceeds 172 MPa at /tmp/build/runs/0007/bracket.py:12"
    check("same defect, different magnitudes and paths -> same hash", fingerprint(a) == fingerprint(b))
    c = "contract conformance failed: extra route /api/parts/bulk not in contract"
    check("different defect -> different hash", fingerprint(a) != fingerprint(c))


def test_signature():
    print("\ngate signature — severity bucketing")
    g1 = [GateResult("physics.bend", False, 0.82)]
    g2 = [GateResult("physics.bend", False, 0.79)]
    g3 = [GateResult("physics.bend", False, 0.11)]
    check("nearby margins share a bucket", signature(g1) == signature(g2))
    check("a gross failure is a different bucket", signature(g1) != signature(g3))
    check("passing gates are not in the signature", signature([GateResult("a", True, 2.0)]) == frozenset())
    check("jaccard of identical signatures is 1", jaccard(signature(g1), signature(g2)) == 1.0)


def test_binary_quantization():
    print("\nbinary quantization — 32x smaller, similarity preserved")
    v = embed("the bracket web is too thin and fails the bending check")
    w = embed("bracket web too thin, bending check fails")
    x = embed("the nightly scrape is stale and the schema drifted")
    check("vector is %d-d" % DIM, len(v) == DIM)
    check("packs to %d bytes (%dx smaller than float32)" % (BYTES, DIM * 4 // BYTES),
          len(pack_bits(v)) == BYTES)
    hv_w = hamming(pack_bits(v), pack_bits(w))
    hv_x = hamming(pack_bits(v), pack_bits(x))
    check("related text is closer in Hamming space than unrelated",
          hv_w < hv_x, "related=%d unrelated=%d" % (hv_w, hv_x))
    check("cosine agrees with Hamming ordering",
          cosine(v, w) > cosine(v, x))


def _fixture(db: str) -> Precedent:
    p = Precedent(db)
    now = time.time()
    for i in range(24):
        p.record(Case(
            run_id="%04d" % i, lane="hardware",
            narrative="physics gate failed on web bending: sigma %d MPa against an allowable of 172 MPa, FoS 0.%d" % (200 + i, 70 + i % 20),
            gates=[GateResult("physics.bend", False, 0.8), GateResult("physics.shear", True, 3.9)],
            fix="invert sigma = 6M/(b t^2) for t at FoS 1.5 and patch web_thickness",
            fix_kind="algebra", resolved=True, ts=now - i * 3600, importance=0.7,
            family="F-11 thin-web bending"))
    for i in range(12):
        p.record(Case(
            run_id="1%03d" % i, lane="data",
            narrative="bolt table scrape is %d minutes old, past the freshness ttl" % (30 + i),
            gates=[GateResult("scrape.freshness", False, 0.3)],
            fix="re-run the collector and confirm the schema key-diff is clean",
            fix_kind="resume-findings", resolved=True, ts=now - i * 7200, importance=0.55,
            family="F-24 stale-scrape"))
    return p


def test_recall():
    print("\nrecall — the four tiers")
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        p = _fixture(db)
        check("archive populated", p.count() == 36)

        # tier 0
        exact = "physics gate failed on web bending: sigma 205 MPa against an allowable of 172 MPa, FoS 0.75"
        hits = p.recall(exact, [GateResult("physics.bend", False, 0.8)], k=3)
        check("exact structural repeat resolves at tier 0",
              hits and hits[0].tier == "fingerprint", hits[0].tier if hits else "none")
        check("tier 0 returns the stored fix", hits and "web_thickness" in hits[0].case.fix)

        # paraphrase -> hybrid
        para = "my bracket bends way too much under the tip load, way over what the material takes"
        hits = p.recall(para, [GateResult("physics.bend", False, 0.78)], k=3)
        check("paraphrase still finds the family",
              hits and hits[0].case.family == "F-11 thin-web bending")
        check("paraphrase resolves via hybrid, not fingerprint",
              hits and hits[0].tier == "hybrid")

        # the thesis: signature carries a match that text alone would miss
        vague = "something is off with the part"
        hits = p.recall(vague, [GateResult("physics.bend", False, 0.8)], k=1)
        check("deterministic gate signature carries a text-poor query",
              hits and hits[0].case.family == "F-11 thin-web bending")
        check("and it is the signature doing the work (jaccard == 1.0)",
              hits and hits[0].parts["jaccard"] == 1.0)

        # honesty
        novel = "the ci runner ran out of disk space installing browser binaries"
        hits = p.recall(novel, [GateResult("infra.disk", False, 0.0)], k=3)
        check("a genuinely novel failure returns NO precedent rather than guessing",
              len(hits) == 0, "got %d hits" % len(hits))

        # families
        fams = {f["family"]: f for f in p.families()}
        check("families cluster by signature", set(fams) == {"F-11 thin-web bending", "F-24 stale-scrape"})
        check("family counts are right", fams["F-11 thin-web bending"]["seen"] == 24)

        # sql tool is read-only
        rows = p.sql("SELECT COUNT(*) n FROM cases")
        check("sql tool reads", rows[0]["n"] == 36)
        check("sql tool allows read-only CTEs",
              p.sql("WITH t AS (SELECT family FROM cases) SELECT COUNT(*) n FROM t")[0]["n"] == 36)
        for bad in ("DELETE FROM cases", "DROP TABLE cases", "INSERT INTO cases VALUES (1)",
                    "SELECT 1; DROP TABLE cases", "WITH x AS (SELECT 1) DELETE FROM cases",
                    "VACUUM", "PRAGMA table_info(cases)"):
            try:
                p.sql(bad); check("sql tool refuses %r" % bad[:18], False, "not refused")
            except ValueError:
                check("sql tool refuses %r" % bad[:18], True)


def test_compaction():
    print("\ncompaction — ratio is reported, probes are the gate")
    events = []
    for i in range(120):
        events.append({"kind": "tool", "text": "ran vitest — 18 passed, 0 failed"})
        events.append({"kind": "read", "path": "lib/guard.ts", "text": "read guard.ts"})
        events.append({"kind": "text", "text": ""})
    events += [
        {"kind": "diff", "files": ["app/api/parts/route.ts", "styles/tokens.css"]},
        {"kind": "gate", "name": "physics.bend", "passed": False, "margin": 0.82},
        {"kind": "repair", "fixes": "physics.bend", "by": "algebra"},
        {"kind": "approval", "what": "merge run/1042", "who": "rishith"},
        {"kind": "next", "text": "record the F-11 precedent"},
    ]
    cleaned, dropped = deterministic_clean(list(events))
    check("deterministic cleanup removes blanks and repeat reads", sum(dropped.values()) > 0)
    check("cleanup is lossless for structured facts",
          sum(1 for e in cleaned if e.get("kind") in ("diff", "gate", "repair", "approval", "next")) == 5)

    deduped, removed = semantic_dedup(cleaned)
    check("semantic dedup removes near-duplicate prose", removed > 0)

    e = compact("1042", events)
    check("compression achieved (%.0fx)" % e.ratio, e.ratio > 3)
    check("probe score is 100%%", e.probe_score == 1.0, "%.2f" % e.probe_score)
    check("files survived compaction", len(e.files_modified) == 2)
    check("approval survived compaction", len(e.approvals) == 1)
    check("failure survived compaction", len(e.failures) == 1)
    check("next step survived compaction", len(e.next_steps) == 1)

    # the guarantee: a compaction that loses an artifact must FAIL its probes
    broken = compact("1042", events)
    broken.files_modified = []
    score, results = validate(broken, build_probes(events))
    check("a compaction that drops files FAILS validation", score < 0.85, "%.2f" % score)


def main() -> int:
    print("precedent — test suite")
    test_fingerprint()
    test_signature()
    test_binary_quantization()
    test_recall()
    test_compaction()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
