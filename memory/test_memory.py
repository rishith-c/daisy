"""Tests for the tiered store and its forgetting boundary.

    python3 -m memory.test_memory

Fixtures and tempdirs only. Nothing here reads the developer's real sessions,
touches the network, or writes outside a TemporaryDirectory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time

from . import boundary, ingest, recall as rc, store

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def ev(seq, kind, text="", run="r1", src="fix:1", **body):
    return store.Event(run_id=run, source=src, seq=seq, kind=kind, text=text,
                       ts=1_760_000_000.0 + seq, body=body)


# A run that reads like a real one: prose that competes for the six-sentence
# prose budget, structured facts that must survive it, and one escalation whose
# wording deliberately avoids every keyword `_squeeze` rewards. That last event
# is the whole experiment — a fact nobody thought to protect.
ESCALATION = ("budget overrun on vendor XZ-4419: the quoted unit price moved from "
              "0.09 to 0.41 USD overnight and the purchase needs a person to sign it off "
              "before anything is ordered from that supplier again")

NOISY_PROSE = [
    "the bending gate failed because the web section is too thin for the tip load",
    "taste tier 1 rejected the frontend so the palette had to be tokenised",
    "the repair solved for thickness rather than guessing a margin of 1.5",
    "a second gate failed on mass, 74 g against the 60 g allowable",
    "the scraper refused to certify because the vendor table had drifted",
    "we escalated the selector break to a human after two failed repairs",
    "the contract conformance gate rejected three endpoints",
    "margin recovered to 1.52 once the web went to 4.61 mm",
    "the freshness gate failed on a scrape that was nine hours stale",
    "the thermal gate passed with 18 degrees of headroom",
]


def demo_events(src="fix:1", run="r1"):
    out, i = [], 0
    for t in NOISY_PROSE:
        i += 1; out.append(ev(i, "prose", t, run=run, src=src))
    i += 1; out.append(ev(i, "escalation", ESCALATION, run=run, src=src,
                          what="vendor XZ-4419 price move", to="purchasing"))
    i += 1; out.append(ev(i, "diff", "wrote hardware/bracket.py", run=run, src=src,
                          files=["hardware/bracket.py"], verb="modified"))
    i += 1; out.append(ev(i, "gate", "physics.bend FAILED", run=run, src=src,
                          name="physics.bend", passed=False, margin=0.73))
    i += 1; out.append(ev(i, "gate", "physics.mass passed", run=run, src=src,
                          name="physics.mass", passed=True, margin=1.2))
    i += 1; out.append(ev(i, "approval", "merge approved", run=run, src=src,
                          what="merge to main", who="rishith"))
    i += 1; out.append(ev(i, "repair", "repaired by algebra", run=run, src=src,
                          fixes="physics.bend", by="algebra"))
    return out


SESSION_LINES = [
    {"type": "user", "timestamp": "2026-08-22T14:00:01.000Z",
     "message": {"role": "user", "content": "add a status badge to the parts dashboard"}},
    {"type": "assistant", "timestamp": "2026-08-22T14:00:04.000Z", "message": {
        "role": "assistant", "content": [
            {"type": "thinking", "thinking": "this should never be ingested"},
            {"type": "text", "text": "Reading the existing dashboard first."},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/repo/app/dashboard.tsx"}}]}},
    {"type": "user", "timestamp": "2026-08-22T14:00:06.000Z", "message": {
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "export function Dash() {}"}]}},
    {"type": "assistant", "timestamp": "2026-08-22T14:00:09.000Z", "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Write",
             "input": {"file_path": "/repo/app/StatusBadge.tsx", "content": "export const B = 1"}}]}},
    {"type": "assistant", "timestamp": "2026-08-22T14:00:12.000Z", "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "t3", "name": "Bash",
             "input": {"command": "npm test", "description": "run the suite"}}]}},
    {"type": "user", "timestamp": "2026-08-22T14:00:20.000Z", "message": {
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t3", "is_error": True,
             "content": "1 test failed: badge has no accessible name"}]}},
    {"type": "assistant", "timestamp": "2026-08-22T14:00:25.000Z", "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "t4", "name": "Edit",
             "input": {"file_path": "/repo/app/StatusBadge.tsx",
                       "old_string": "a", "new_string": "b"}}]}},
    {"type": "queue-operation", "operation": "enqueue", "content": "ignored"},
    "{ this line is not json at all",
    {"type": "summary", "summary": "no message payload here"},
]


def write_session(d, name="c0ffee00-1111-2222-3333-444444444444.jsonl"):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        for rec in SESSION_LINES:
            fh.write(rec if isinstance(rec, str) else json.dumps(rec))
            fh.write("\n")
    return p


def fresh(d, name="m.db"):
    return store.connect(os.path.join(d, name))


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------

def test_empty_store():
    print("\nan empty store — every path must survive having nothing to say")
    with tempfile.TemporaryDirectory() as d:
        con = fresh(d)
        s = store.stats(con)
        check("no events, facts, summaries or residue",
              (s["events"], s["facts"], s["summaries"], s["residue"]) == (0, 0, 0, 0), str(s))
        r = rc.recall(con, "anything at all")
        check("recall on an empty store returns nothing rather than raising", r.empty())
        check("forgotten on an empty store returns nothing",
              not rc.forgotten(con, "anything").forgotten)
        check("audit_all reports zero compactions and still reconciles",
              boundary.audit_all(con)["totals"]["compactions"] == 0
              and boundary.audit_all(con)["totals"]["reconciles"])
        check("compacting an empty span is a no-op, not an error",
              boundary.compact_span(con, "r1", "nothing:here")["events"] == 0)
        check("a pointer to a nonexistent event resolves to nothing",
              store.event(con, "deadbeef") == {})
        con.close()


def test_single_event():
    print("\none event — the smallest thing the store can hold")
    with tempfile.TemporaryDirectory() as d:
        con = fresh(d)
        e = ev(1, "diff", "wrote index.html", files=["index.html"])
        res = store.append(con, [e])
        check("one event landed in tier 0", res["events"] == 1, str(res))
        check("it produced exactly one tier-1 fact", res["facts"] == 1, str(res))
        row = store.event(con, e.ident())
        check("the content address round-trips", row.get("text") == "wrote index.html")
        check("the body came back as parsed json", row.get("body", {}).get("files") == ["index.html"])
        f = store.facts_for(con)[0]
        check("the fact is typed as a write", f["kind"] == "write", f["kind"])
        check("the fact's subject is the path, not a blob", f["subject"] == "index.html")
        check("the fact points back at its tier-0 event", f["event_id"] == e.ident())
        r = rc.recall(con, "did I touch index.html")
        check("a single event is recallable by exact path",
              r.held and r.held[0].subject == "index.html",
              str([h.subject for h in r.held]))
        check("the exact leg, not the vector, carried it",
              r.held and r.held[0].parts["exact"] == 1.0, str(r.held[0].parts))
        con.close()


def test_idempotent_ingestion():
    print("\ningesting twice — content addressing, not a dedup pass")
    with tempfile.TemporaryDirectory() as d:
        con = fresh(d)
        first = store.append(con, demo_events())
        second = store.append(con, demo_events())
        check("the second ingest adds no events", second["events"] == 0, str(second))
        check("the second ingest adds no facts", second["facts"] == 0, str(second))
        check("every event of the first ingest was a duplicate the second time",
              second["duplicates"] == first["events"],
              "%d vs %d" % (second["duplicates"], first["events"]))
        n = store.stats(con)["events"]
        store.append(con, demo_events())
        check("a third ingest still leaves the log the same size",
              store.stats(con)["events"] == n)
        # A changed payload at the same (source, seq) is a different event and
        # must not be silently swallowed by the unique index.
        changed = ev(1, "prose", NOISY_PROSE[0] + " and then something new happened")
        before = store.stats(con)["events"]
        store.append(con, [changed])
        check("an edited record at the same seq is refused, not overwritten",
              store.stats(con)["events"] == before)
        check("the original text at that seq is intact",
              con.execute("SELECT text FROM event WHERE source=? AND seq=1",
                          ("fix:1",)).fetchone()[0] == NOISY_PROSE[0])
        con.close()


def test_session_ingest():
    print("\na real-shaped Claude Code session")
    with tempfile.TemporaryDirectory() as d:
        p = write_session(d)
        con = fresh(d)
        res = ingest.ingest_session(con, p)
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM event GROUP BY kind").fetchall())
        check("the session produced events", res["events"] > 0, str(res))
        check("a Write tool call became a diff event", kinds.get("diff") == 2, str(kinds))
        check("a Read tool call became a read event", kinds.get("read") == 1, str(kinds))
        check("a Bash tool call became a tool event", kinds.get("tool") == 1, str(kinds))
        check("an is_error tool_result became an error event", kinds.get("error") == 1, str(kinds))
        check("thinking blocks were not ingested",
              not con.execute("SELECT 1 FROM event WHERE text LIKE '%never be ingested%'").fetchone())
        check("a malformed line did not stop the stream", kinds.get("prose", 0) >= 2, str(kinds))
        w = store.facts_for(con, kind="write")
        check("both tool calls resolved to the same file",
              {f["subject"] for f in w} == {"/repo/app/StatusBadge.tsx"},
              str([f["subject"] for f in w]))
        check("Write and Edit are kept apart as created and modified",
              sorted(f["value"] for f in w) == ["created", "modified"],
              str([f["value"] for f in w]))
        check("the ISO timestamp survived ingestion",
              con.execute("SELECT MIN(ts) FROM event").fetchone()[0] > 1_700_000_000)
        check("re-ingesting the same file changes nothing",
              ingest.ingest_session(con, p)["events"] == 0)

        r = rc.recall(con, "what happened to /repo/app/StatusBadge.tsx")
        check("the written file is recallable by path",
              any(h.subject.endswith("StatusBadge.tsx") for h in r.held),
              str([h.subject for h in r.held]))
        con.close()


def test_run_ingest():
    print("\na Daisy run directory — gates, repairs, approvals, escalations")
    with tempfile.TemporaryDirectory() as d:
        rd = os.path.join(d, "run77")
        os.makedirs(rd)
        json.dump({"run": "run77", "brief": "a mounting bracket",
                   "lanes": ["hardware"], "load_case": {"kg": 2.4, "arm_mm": 90.0,
                                                        "fos": 1.5, "material": "PETG"}},
                  open(os.path.join(rd, "plan.json"), "w"))
        json.dump({"run": "run77", "lanes": {"hardware": {
            "why": "bending failed on the first attempt", "attempts": 2,
            "gates": [{"name": "physics.bend", "passed": False, "margin": 0.73},
                      {"name": "physics.mass", "passed": True, "margin": 5.0}],
            "artifacts": [{"path": "/x/bracket.stl", "bytes": 1284}]}},
            "blocked_lanes": ["scrape"],
            "admitted_to_commons": [{"lane": "hardware"}]},
            open(os.path.join(rd, "summary.json"), "w"))
        con = fresh(d)
        ingest.ingest_run(con, rd)
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM fact GROUP BY kind").fetchall())
        check("gate verdicts became facts", kinds.get("gate") == 2, str(kinds))
        check("the artifact path became a write fact, not a dict repr",
              [f["subject"] for f in store.facts_for(con, kind="write")] == ["/x/bracket.stl"],
              str([f["subject"] for f in store.facts_for(con, kind="write")]))
        check("the blocked lane became an escalation", kinds.get("escalation") == 1, str(kinds))
        check("the commons admission became an approval", kinds.get("approval") == 1, str(kinds))
        check("the retry became a repair", kinds.get("repair") == 1, str(kinds))
        check("the brief and the load case became decisions", kinds.get("decision") == 2, str(kinds))
        g = [f for f in store.facts_for(con, kind="gate") if f["subject"] == "physics.bend"][0]
        check("a failed gate records its verdict", g["value"] == "fail", g["value"])
        check("a failed gate records its margin", "0.73" in g["detail"], g["detail"])
        con.close()


# ---------------------------------------------------------------------------
# the boundary
# ---------------------------------------------------------------------------

def _compacted(d):
    con = fresh(d)
    store.append(con, demo_events())
    b = boundary.compact_span(con, "r1", "fix:1")
    return con, b


def test_facts_survive_compaction():
    print("\nwhat compaction is not allowed to take")
    with tempfile.TemporaryDirectory() as d:
        con, b = _compacted(d)
        a = boundary.audit(con, b["summary_id"])
        check("compaction actually compacted", b["dropped"] > 0, str(b))
        check("the file that was modified is still in context",
              _in_context(con, a, "hardware/bracket.py"), str(a["tier0_only_subjects"]))
        check("the failed gate is still in context", _in_context(con, a, "physics.bend"))
        check("the approver is still in context",
              "rishith" in json.loads(con.execute(
                  "SELECT essence FROM summary WHERE id=?", (b["summary_id"],)).fetchone()[0]
                  ).get("approvals", [""])[0])
        check("the repair is still in context", _in_context(con, a, "physics.bend"))
        check("no tier-1 fact was deleted by compaction",
              len(store.facts_for(con)) == a["facts"], "%d vs %d" %
              (len(store.facts_for(con)), a["facts"]))
        check("probe validation passed", a["probe_score"] >= 0.85, str(a["probe_score"]))
        check("compression is real", a["ratio"] > 1.0, str(a["ratio"]))
        con.close()


def _in_context(con, a, subject):
    return subject not in a["tier0_only_subjects"] and any(
        f["subject"] == subject for f in store.facts_for(con))


def test_the_forgotten_fact():
    print("\nthe headline — a fact that did NOT survive, and knows it")
    with tempfile.TemporaryDirectory() as d:
        con, b = _compacted(d)
        a = boundary.audit(con, b["summary_id"])

        check("at least one fact is reachable only through tier 0",
              a["facts_tier0_only"] >= 1, str(a))
        check("the escalation is the one that fell out of context",
              any("XZ-4419" in s for s in a["tier0_only_subjects"]),
              str(a["tier0_only_subjects"]))
        check("context coverage is honestly below 1.0",
              a["context_coverage"] < 1.0, str(a["context_coverage"]))
        check("total coverage is still 1.0 — nothing is unreachable",
              a["total_coverage"] == 1.0, str(a))

        f = rc.forgotten(con, "vendor XZ-4419 price")
        check("asking what I forgot returns the residue", bool(f.forgotten),
              "%d residue rows scanned" % f.scanned)
        hit = f.forgotten[0] if f.forgotten else None
        check("the answer is tagged tier 3", hit and hit.tier == 3)
        check("it reports WHEN it was compacted", hit and hit.dropped_at > 0)
        check("it reports WHY it was dropped", hit and hit.reason in ("squeezed", "deterministic"),
              hit.reason if hit else "-")
        check("it carries a tier-0 pointer, not a reconstruction",
              hit and hit.event_id and hit.value == "")
        original = rc.verbatim(con, hit) if hit else {}
        check("the pointer resolves to the original event", bool(original))
        check("the original still holds the full text",
              original.get("text") == ESCALATION,
              (original.get("text") or "")[:60])
        check("the residue claim is strictly shorter than the original",
              hit and len(hit.subject) < len(ESCALATION),
              "%d vs %d" % (len(hit.subject) if hit else 0, len(ESCALATION)))
        check("the claim is an excerpt of the original, never a paraphrase",
              hit and ESCALATION.startswith(hit.subject.rstrip("…")),
              hit.subject if hit else "-")
        both = rc.recall(con, "vendor XZ-4419 price")
        check("recall() surfaces it under .forgotten too, not only .held",
              any("XZ-4419" in x.subject for x in both.forgotten))
        # Tier 1 keeps the headline — that is its job, and it is why the audit
        # can still name the escalation. What left context is the narration: the
        # prices, the supplier condition, the reason a person has to sign.
        check("tier 1 still holds the typed escalation",
              any(x.tier == 1 and "XZ-4419" in x.subject for x in both.held),
              str([(x.tier, x.subject[:30]) for x in both.held]))
        check("but the detail is not in anything still held",
              not any("0.41" in (x.subject + x.value) for x in both.held),
              str([x.subject[:40] for x in both.held]))
        check("and the detail IS in the tier-0 row the residue points at",
              "0.41" in (original.get("text") or ""))
        check("every residue row has a live pointer",
              a["residue_rows"] == a["residue_live_pointers"], str(a))
        con.close()


def test_probe_rejection():
    print("\nprobe validation — a compaction that cannot answer for itself")
    from precedent.compact import (MIN_PROBE_SCORE, build_probes, compact,
                                   deterministic_clean, distill, validate)
    good = [{"kind": "prose", "text": "we sized the web and it held"},
            {"kind": "diff", "files": ["hardware/bracket.py"]},
            {"kind": "approval", "what": "merge to main", "who": "rishith"}]
    e = compact("ok", good)
    check("a well-formed run passes its own probes", e.probe_score >= MIN_PROBE_SCORE,
          str(e.probe_score))

    # An approval with no approver cannot be answered at any budget, so the
    # ladder backs off to its gentlest setting and then reports an honest
    # sub-threshold score instead of claiming a clean compaction.
    bad = [dict(good[0]), dict(good[1]), {"kind": "approval", "what": "merge", "who": ""}]
    eb = compact("bad", bad)
    check("an unanswerable probe drives the score below the gate",
          eb.probe_score < MIN_PROBE_SCORE, str(eb.probe_score))
    check("the failure is named, not swallowed",
          any(not p["passed"] for p in eb.probes), str(eb.probes))
    first, _ = validate(distill("bad", deterministic_clean(list(bad))[0]), build_probes(bad))
    check("the first attempt is what failed, so the ladder retried",
          first < MIN_PROBE_SCORE, str(first))

    # And the mechanism itself: a summary with the artifact removed is rejected.
    from precedent.compact import Essence
    lossy = Essence(run_id="x", prose="we changed some files")
    score, _ = validate(lossy, build_probes(good))
    check("a compaction that lost the artifact scores 0", score == 0.0, str(score))

    with tempfile.TemporaryDirectory() as d:
        con = fresh(d)
        store.append(con, [ev(1, "prose", "we sized the web and it held"),
                           ev(2, "diff", "wrote it", files=["hardware/bracket.py"]),
                           ev(3, "approval", "merged", what="merge", who="")])
        b = boundary.compact_span(con, "r1", "fix:1")
        check("the store records that the boundary was retried", b["retried"] is True, str(b))
        a = boundary.audit(con, b["summary_id"])
        check("the audit carries the honest probe score",
              a["probe_score"] < MIN_PROBE_SCORE and a["retried"], str(a["probe_score"]))
        con.close()


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------

def test_evidence_floor():
    print("\nthe evidence floor — it is allowed to say no")
    with tempfile.TemporaryDirectory() as d:
        con, _ = _compacted(d)
        novel = rc.recall(con, "the kubernetes pod was evicted under memory pressure "
                               "while pulling a container image from the registry")
        check("a genuinely novel query returns nothing at all", novel.empty(),
              str([(h.subject[:30], h.score) for h in novel.held + novel.forgotten]))
        check("and it says how much it looked at", novel.scanned > 0, str(novel.scanned))
        known = rc.recall(con, "hardware/bracket.py")
        check("a query it does hold still returns", bool(known.held))
        check("the floor is absolute, not a normalised rank",
              all(h.parts["evidence"] >= rc.EVIDENCE_FLOOR for h in known.held))
        check("dropping the floor to zero surfaces the weak matches the floor hid",
              len(rc.recall(con, "the kubernetes pod was evicted under memory pressure",
                            floor=0.0).held) > 0)
        check("a novel query cannot manufacture a forgetting claim either",
              not rc.forgotten(con, "the kubernetes pod was evicted under memory "
                                    "pressure while pulling an image").forgotten)
        true_hit = rc.forgotten(con, "budget overrun on the supplier quote", floor=0.0)
        novel_hit = rc.forgotten(con, "quarterly revenue forecast for the sales team",
                                 floor=0.0)
        check("the residue floor sits inside a measured gap, not a guessed one",
              true_hit.forgotten[0].score > rc.RESIDUE_FLOOR > novel_hit.forgotten[0].score,
              "%.3f > %.2f > %.3f" % (true_hit.forgotten[0].score, rc.RESIDUE_FLOOR,
                                      novel_hit.forgotten[0].score))
        con.close()


def test_fusion():
    print("\nthe three legs of the fused score")
    check("a path token is extracted for exact matching",
          "hardware/bracket.py" in rc.tokens("please check hardware/bracket.py again"))
    check("a gate name is extracted", "physics.bend" in rc.tokens("physics.bend went red"))
    check("a quoted span is extracted",
          "merge to main" in rc.tokens('who approved "merge to main"'))
    check("bare words are not exact tokens", rc.tokens("the thing broke") == [],
          str(rc.tokens("the thing broke")))
    check("exact equality is proof", rc._exact(["index.html"], "index.html") == 1.0)
    check("a basename match is nearly proof",
          rc._exact(["bracket.py"], "hardware/bracket.py") == 0.85)
    check("containment is weaker than a basename match",
          rc._exact(["bracket"], "hardware/bracket.py") == 0.60)
    check("an unrelated token scores nothing", rc._exact(["kubernetes"], "index.html") == 0.0)
    check("gate containment fires on the gate name",
          rc._cover({"physics.bend"}, "physics.bend", "gate") == 1.0)
    check("containment does not punish a run for checking more gates",
          rc._cover({"physics.bend"}, "physics.bend", "gate") ==
          rc._cover({"physics.bend"}, "physics.bend", "gate", "mass thermal bend"))
    check("no gates asked means no gate signal", rc._cover(set(), "physics.bend", "gate") == 0.0)
    check("with a deterministic signal the vector is only 0.38 of the score",
          abs(rc._evidence(1.0, 0.0, 1.0) - (0.62 * 0.6 + 0.38)) < 1e-9,
          str(rc._evidence(1.0, 0.0, 1.0)))
    check("with no deterministic signal the vector is judged on its own scale",
          rc._evidence(0.0, 0.0, 0.44) == 0.44)

    with tempfile.TemporaryDirectory() as d:
        con, _ = _compacted(d)
        gated = rc.recall(con, "something is off with the part", gates=["physics.bend"])
        check("a text-poor query still lands when the gate signature matches",
              any(h.subject == "physics.bend" for h in gated.held),
              str([h.subject for h in gated.held]))
        check("the gate leg, not the text, carried it",
              all(h.parts["cover"] > 0 for h in gated.held if h.subject == "physics.bend"))
        ungated = rc.recall(con, "something is off with the part")
        check("without the signature the same query finds less",
              len(ungated.held) < len(gated.held) or not ungated.held,
              "%d vs %d" % (len(ungated.held), len(gated.held)))
        con.close()


# ---------------------------------------------------------------------------
# durability
# ---------------------------------------------------------------------------

def test_corruption():
    print("\na corrupted row must not sink a query")
    with tempfile.TemporaryDirectory() as d:
        con, _ = _compacted(d)
        good = len(rc.recall(con, "hardware/bracket.py", floor=0.0).held)
        con.execute("INSERT INTO fact (id, event_id, run_id, ts, kind, subject, value,"
                    " detail, vec, bits) VALUES ('bad1','nope','r1',1,'write',"
                    "'hardware/bracket.py','modified','', X'00', X'DEAD')")
        con.execute("INSERT INTO residue (id, summary_id, event_id, run_id, ts, dropped_at,"
                    " kind, claim, reason, vec, bits) VALUES ('bad2','s','nope','r1',1,1,"
                    "'prose','vendor XZ-4419 truncated','squeezed', X'', X'')")
        con.execute("UPDATE event SET body='{not json' WHERE seq=1")
        con.commit()
        r = rc.recall(con, "hardware/bracket.py", floor=0.0)
        check("the query still runs with a short vector blob in the table", bool(r.held))
        check("the corrupt row is skipped rather than scored",
              all(h.id != "bad1" for h in r.held), str([h.id for h in r.held]))
        check("the good rows are all still there", len(r.held) == good,
              "%d vs %d" % (len(r.held), good))
        f = rc.forgotten(con, "vendor XZ-4419 price")
        check("a residue row with a dead pointer is never offered as recoverable",
              all(x.id != "bad2" for x in f.forgotten))
        row = store.event(con, con.execute(
            "SELECT id FROM event WHERE seq=1").fetchone()[0])
        check("an unparseable body still resolves as a pointer", bool(row))
        check("and it is flagged rather than faked", row.get("body_unparsed") is True)
        check("stats still computes over a damaged table", store.stats(con)["facts"] > 0)
        check("the audit reports the dangling pointer instead of hiding it",
              store.stats(con)["dangling_pointers"] == 1,
              str(store.stats(con)["dangling_pointers"]))
        con.close()


def test_concurrency():
    print("\nconcurrent writers — WAL, a busy timeout, and content addressing")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "conc.db")
        store.connect(path).close()
        errors, threads = [], []

        def writer(n):
            try:
                c = store.connect(path)
                store.append(c, [ev(i, "prose", "worker %d line %d" % (n, i),
                                    src="w%d" % n) for i in range(1, 21)])
                c.close()
            except Exception as exc:                     # noqa: BLE001 — the test IS the check
                errors.append("%s: %s" % (type(exc).__name__, exc))

        for n in range(6):
            t = threading.Thread(target=writer, args=(n,))
            threads.append(t); t.start()
        for t in threads:
            t.join()

        con = store.connect(path)
        check("no writer raised", not errors, "; ".join(errors[:3]))
        check("every writer's events landed",
              store.stats(con)["events"] == 120, str(store.stats(con)["events"]))
        check("each source kept its own sequence",
              con.execute("SELECT COUNT(DISTINCT source) FROM event").fetchone()[0] == 6)

        # Two writers racing on the SAME source and seq: content addressing has
        # to make that a no-op, not a duplicate and not an integrity error.
        errors.clear()

        def racer():
            try:
                c = store.connect(path)
                store.append(c, demo_events(src="shared"))
                c.close()
            except Exception as exc:                     # noqa: BLE001
                errors.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=racer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("racing writers on the same source do not raise", not errors,
              "; ".join(errors[:3]))
        check("and the identical events collapse to one copy each",
              con.execute("SELECT COUNT(*) FROM event WHERE source='shared'").fetchone()[0]
              == len(demo_events()),
              str(con.execute("SELECT COUNT(*) FROM event WHERE source='shared'").fetchone()[0]))
        check("a reader sees a consistent store afterwards",
              store.stats(con)["dangling_pointers"] == 0)
        con.close()


def test_unicode_and_size():
    print("\nunicode, emoji-free but not ASCII, and values nobody sized for")
    with tempfile.TemporaryDirectory() as d:
        con = fresh(d)
        weird = "ネジの締め付けトルク — 1.2 N·m ± 0.1 · Ω · «approuvé» · \u0000 · tab\ttrail"
        huge = ("the vendor table drifted " * 4000)
        path = "src/компоненты/Кнопка.tsx"
        store.append(con, [
            ev(1, "prose", weird),
            ev(2, "diff", "wrote it", files=[path]),
            ev(3, "prose", huge),
            ev(4, "decision", "x" * 5000),
        ])
        check("a unicode event round-trips byte for byte",
              con.execute("SELECT text FROM event WHERE seq=1").fetchone()[0] == weird)
        check("a 100 KB event is stored whole",
              len(con.execute("SELECT text FROM event WHERE seq=3").fetchone()[0]) == len(huge))
        subs = [f["subject"] for f in store.facts_for(con, kind="write")]
        check("a non-ASCII path becomes a fact subject unchanged", subs == [path], str(subs))
        check("a 5000-character decision is truncated to a usable subject",
              len(store.facts_for(con, kind="decision")[0]["subject"]) == 120)
        r = rc.recall(con, "src/компоненты/Кнопка.tsx")
        check("a non-ASCII path is recallable exactly",
              r.held and r.held[0].parts["exact"] == 1.0, str([h.subject for h in r.held]))
        b = boundary.compact_span(con, "r1", "fix:1")
        check("compaction survives the oversized event", b["events"] == 4, str(b))
        res = con.execute("SELECT claim FROM residue").fetchall()
        check("every residue claim is capped at the excerpt length",
              all(len(x[0]) <= store.CLAIM_CHARS for x in res),
              str([len(x[0]) for x in res]))
        check("the huge event's residue is a stub, and tier 0 still has the whole thing",
              all(len(x[0]) < len(huge) for x in res) if res else True)
        con.close()


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------

def test_audit_reconciles():
    print("\nthe audit — numbers that have to agree with the store")
    with tempfile.TemporaryDirectory() as d:
        con, b = _compacted(d)
        store.append(con, demo_events(src="fix:2", run="r2"))
        boundary.compact_span(con, "r2", "fix:2")
        rep = boundary.audit_all(con)
        t, each = rep["totals"], rep["compactions"]

        check("every compaction reconciles", t["reconciles"], str(t))
        check("two compactions were audited", t["compactions"] == 2, str(t["compactions"]))
        check("retained plus dropped equals the events in the span",
              all(a["events_retained"] + a["events_dropped"] == a["events"] for a in each))
        check("residue rows equal the events dropped",
              all(a["residue_rows"] == a["events_dropped"] for a in each),
              str([(a["residue_rows"], a["events_dropped"]) for a in each]))
        check("the audit's event count matches the store",
              t["events"] == store.stats(con)["events"], "%d vs %d" %
              (t["events"], store.stats(con)["events"]))
        check("the audit's residue count matches the store",
              t["residue_rows"] == store.stats(con)["residue"])
        check("the audit's fact count matches the store",
              t["facts"] == store.stats(con)["facts"], "%d vs %d" %
              (t["facts"], store.stats(con)["facts"]))
        check("in-context plus tier-0-only plus unreachable equals every fact",
              all(a["facts_in_context"] + a["facts_tier0_only"] + a["facts_unreachable"]
                  == a["facts"] for a in each))
        check("nothing is unreachable", t["facts_unreachable"] == 0)
        check("total coverage is exactly 1.0", t["total_coverage"] == 1.0)
        check("context coverage is below total coverage — that gap is the point",
              t["context_coverage"] < t["total_coverage"],
              "%s vs %s" % (t["context_coverage"], t["total_coverage"]))
        check("every residue pointer is live",
              t["residue_rows"] == t["residue_live_pointers"])
        check("compression is reported from measured bytes",
              t["bytes_before"] > t["bytes_after"] > 0, str((t["bytes_before"], t["bytes_after"])))

        # Reconciliation has to be a real check, not a tautology: break the
        # store behind the audit's back and it must notice.
        con.execute("DELETE FROM residue WHERE summary_id = ? AND rowid IN"
                    " (SELECT rowid FROM residue WHERE summary_id = ? LIMIT 1)",
                    (b["summary_id"], b["summary_id"]))
        con.commit()
        check("deleting one residue row breaks reconciliation",
              not boundary.audit(con, b["summary_id"])["reconciles"])
        con.execute("DELETE FROM event WHERE id IN (SELECT event_id FROM residue"
                    " WHERE summary_id = ? LIMIT 1)", (b["summary_id"],))
        con.commit()
        a2 = boundary.audit(con, b["summary_id"])
        check("a deleted tier-0 event shows up as a dead pointer",
              a2["residue_live_pointers"] < a2["residue_rows"], str(a2))
        check("and the audit refuses to reconcile", not a2["reconciles"])
        con.close()


def test_audit_on_ingested_runs():
    print("\nthe audit over an ingested run tree, end to end")
    with tempfile.TemporaryDirectory() as d:
        rd = os.path.join(d, "runs", "r99")
        os.makedirs(rd)
        json.dump({"run": "r99", "brief": "a status badge", "lanes": ["software"]},
                  open(os.path.join(rd, "plan.json"), "w"))
        json.dump({"run": "r99", "lanes": {"software": {
            "why": "2 findings after 3 attempts", "attempts": 3,
            "gates": [{"name": "taste.t1", "passed": False, "margin": 3.0},
                      {"name": "taste.t1", "passed": False, "margin": 2.0}],
            "artifacts": ["/x/badge.html"]}}, "blocked_lanes": [],
            "admitted_to_commons": []},
            open(os.path.join(rd, "summary.json"), "w"))
        con = fresh(d)
        got = ingest.ingest_runs(con, os.path.join(d, "runs"))
        check("the run tree was walked", len(got) == 1, str(got))
        boundary.compact_span(con, "r99", "daisy:r99")
        rep = boundary.audit_all(con)
        check("the ingested run audits clean", rep["totals"]["reconciles"], str(rep["totals"]))
        check("its gate verdicts survived into context",
              rep["totals"]["facts_in_context"] >= 1, str(rep["totals"]))
        s = store.stats(con)
        check("the index is 64 bytes a row, not a float array",
              s["index_bytes"] == store.BYTES * (s["facts"] + s["summaries"] + s["residue"]))
        check("and the float rescore side is 32x that",
              s["float_bytes"] == 32 * s["index_bytes"], str((s["float_bytes"], s["index_bytes"])))
        con.close()


def main():
    print("super memory — test suite")
    test_empty_store()
    test_single_event()
    test_idempotent_ingestion()
    test_session_ingest()
    test_run_ingest()
    test_facts_survive_compaction()
    test_the_forgotten_fact()
    test_probe_rejection()
    test_evidence_floor()
    test_fusion()
    test_corruption()
    test_concurrency()
    test_unicode_and_size()
    test_audit_reconciles()
    test_audit_on_ingested_runs()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
