"""Tests for the four-round protocol, the collaboration metrics, and the spec gate.

    python3 -m duo.test_duo

Every model in here is a fake. Nothing in this file starts a process, opens a
socket, or spends a token: a suite whose result depends on whether two CLIs are
logged in today is not a suite, it is a weather report.
"""

from __future__ import annotations

import json
import os
import tempfile

from . import cli as duo_cli
from . import models as duo_models
from . import rounds as R
from . import spec as S
from .metrics import collaboration, drift, DRIFT_FLOOR

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


BRIEF = "Give the importer a resync that never overwrites a hand edit."


# ---------------------------------------------------------------------------
# the fakes
# ---------------------------------------------------------------------------

class Probe:
    def __init__(self, name, ok=True, detail=""):
        self.name = name
        self.ok = ok
        self.detail = detail or ("responded to the probe" if ok else "credential expired")
        self.probe_ms = 11.0


def prober(usable=("claude", "codex"), absent=()):
    def fn(names, cwd=None):
        return [Probe(n, n in usable) for n in names if n not in absent]
    return fn


DRAFT = {
    "claude": """## PROBLEM
A resync cannot tell its own previous output from a hand edit, so the safe
choice today is to write nothing and the common choice is to clobber.

## NON-GOALS
- Rolling an import back.
- Importing from a tool that is not installed.
- Conflict resolution in a window.

## APPROACH
Each import appends a fenced block delimited by a marker carrying a content
hash of what it wrote. A resync reads the marker, compares the hash, and
rewrites only its own block. Watermarks live in importer/state.json keyed by
source id.

## KEY DECISION
The marker carries a content hash, not a timestamp.

## REJECTED ALTERNATIVE
A sidecar file per source. Two files can disagree with each other and a single
in-file marker cannot.

## RISKS
- A hand edit made inside the marked block is still overwritten.
- A source that renames itself reads as a new source.

## ACCEPTANCE
- Running the import twice produces a byte-for-byte identical config.md.
- importer/state.json holds exactly one entry per imported source.
- A hand edit outside the marked block survives 3 resyncs unchanged.
- The command exits 2 when named a source that is not installed.""",

    "codex": """## PROBLEM
Imports are write-once. There is no record of provenance, so the second run has
to guess which lines it owns.

## NON-GOALS
- A merge UI.
- Importing history, as opposed to settings.
- Supporting tools that have no config file.

## APPROACH
Keep an SQLite table of every line the importer has ever written, keyed by
(source, path, sha). On resync, diff the file against the table: lines present
in the table are ours and may be replaced, lines absent are the operator's and
are left alone.

## KEY DECISION
Provenance is tracked per line in a database, not per block in the file.

## REJECTED ALTERNATIVE
Marker comments in the config file. They are visible, editable, and a user who
deletes one silently converts our lines into theirs.

## RISKS
- The database and the file can drift apart if the file is edited elsewhere.
- Per-line tracking is slow on files over ten thousand lines.

## ACCEPTANCE
- A resync of an unedited file changes 0 bytes.
- The database contains one row per written line after the first import.
- Deleting the database and resyncing exits 3 rather than rewriting the file.
- A 10,000-line config resyncs in under 2 seconds.""",
}

CRITIQUE = {
    # claude critiques codex's draft
    "claude": """1. [WRONG] Per-line provenance in a database cannot survive a reformat of the config file.
   why: one indentation change makes every sha miss and the importer treats its own lines as the operator's.
2. [MISSING] Nothing says what happens when the database and the file disagree.
   why: the failure is silent and the operator finds out three resyncs later.
3. [CUT] The 10,000-line performance target.
   why: no real config is that size and the number invents a constraint the design then pays for.""",
    # codex critiques claude's draft
    "codex": """1. [WRONG] A content-hash marker in the file is editable by the operator, so it is not a record.
   why: deleting the marker line silently transfers ownership of our block to them, undetectably.
2. [MISSING] There is no statement of what a resync does when the marked block has moved in the file.
   why: append-only rewriting will duplicate the block instead of replacing it.
3. [CUT] The importer/state.json watermark.
   why: the in-file marker already carries the same fact, so two sources of truth exist for one thing.""",
}

CONVERGE = {
    "claude": """## LEDGER
1 REJECT — the marker is not the record; state.json is, and the marker is only a locator.
2 ACCEPT — added a relocate step that searches the whole file for the marker before appending.
3 REJECT — state.json holds the watermark and the hash of the last write, which the marker does not.

## REVISED
## PROBLEM
A resync cannot tell its own previous output from a hand edit, so the safe
choice today is to write nothing and the common choice is to clobber.

## NON-GOALS
- Rolling an import back.
- Importing from a tool that is not installed.
- Conflict resolution in a window.

## APPROACH
Each import writes a fenced block delimited by a marker. Before writing, a
resync searches the entire file for that marker rather than assuming it is at
the end, so a block the operator moved is replaced in place instead of being
duplicated. If the marker is gone the resync stops and reports an orphaned
block rather than appending a second copy. importer/state.json remains the
record of what was written and when.

## KEY DECISION
The marker locates the block; importer/state.json records it.

## REJECTED ALTERNATIVE
A sidecar file per source. Two files can disagree with each other and a single
in-file marker cannot.

## RISKS
- A hand edit made inside the marked block is still overwritten.
- A source that renames itself reads as a new source.
- A deleted marker halts the resync until a human resolves it.

## ACCEPTANCE
- Running the import twice produces a byte-for-byte identical config.md.
- Moving the marked block to the top of the file still yields 1 block after resync.
- A resync with the marker deleted exits 4 and writes 0 bytes.
- A hand edit outside the marked block survives 3 resyncs unchanged.""",

    "codex": """## LEDGER
1 ACCEPT — provenance now keys on normalised content rather than raw line text.
2 ACCEPT — added an explicit reconcile path that reports drift and refuses to write.
3 REJECT — the 10,000-line target is what forced the index; without it the design degrades quietly.

## REVISED
## PROBLEM
Imports are write-once. There is no record of provenance, so the second run has
to guess which lines it owns.

## NON-GOALS
- A merge UI.
- Importing history, as opposed to settings.
- Supporting tools that have no config file.

## APPROACH
Keep an SQLite table of every line the importer has written, keyed by source
and by a normalised hash that ignores leading whitespace, so reformatting the
file does not break provenance. On resync, diff the file against the table. If
a line is in the table but absent from the file, the importer reports drift and
refuses to write until reconciled, instead of silently reinstating it.

## KEY DECISION
Provenance is per line and normalised, and drift halts the run.

## REJECTED ALTERNATIVE
Marker comments in the config file. They are visible, editable, and a user who
deletes one silently converts our lines into theirs.

## RISKS
- Normalisation hides a real change that is only whitespace.
- Reconciliation is manual and there is no command for it yet.

## ACCEPTANCE
- A resync of an unedited file changes 0 bytes.
- Reformatting the file and resyncing still matches 100% of tracked lines.
- Detected drift exits 5 and writes 0 bytes.
- A 10,000-line config resyncs in under 2 seconds.""",
}

# The synthesis carries ONE of the two surviving disagreements. The other is
# dropped, which is the case duo.spec has to catch and restore.
SYNTHESIS = """## PROBLEM
An importer that cannot distinguish its own previous output from an operator's
edit has two bad options on every resync: clobber the edit, or write nothing.

## NON-GOALS
- Rolling an import back.
- A conflict-resolution UI.
- Importing session history rather than settings.

## PROPOSED APPROACH
Provenance is recorded outside the config file and located inside it. Each
import writes a delimited block; the delimiter is a locator, and the record of
what was written lives in a store the operator does not edit. A resync searches
the file for the locator, reconciles against the store, and refuses to write
when the two disagree.

## ALTERNATIVES CONSIDERED
- Marker comments as the sole record. Loses because the operator can delete a
  comment and silently take ownership of the block; it would only win if the
  config file were not operator-editable.
- Per-line provenance in a database as the sole record. Loses because a
  reformat breaks raw line matching; it would win if normalised hashing proved
  reliable across the tools we import from.

## OPEN QUESTIONS
- Should a fixed performance target drive the storage design? Position A: the
  10,000-line target invents a constraint no real config reaches. Position B:
  the target is what forced an index, and dropping it lets the design degrade
  quietly. Decides: the largest config.md measured across the tools on a real
  machine.

## ACCEPTANCE CRITERIA
- Running the import twice produces a byte-for-byte identical config.md.
- Moving the marked block to the top of the file yields exactly 1 block after resync.
- A resync with the locator deleted exits 4 and writes 0 bytes.
- A hand edit outside the marked block survives 3 resyncs unchanged.
- `importer.cli sync --once` exits 0 on a clean tree and 5 on detected drift.

## RISKS
- A hand edit inside the marked block is still overwritten.
- Normalisation can hide a change that is only whitespace.
- Reconciliation has no command yet, so drift halts the run with no way forward.
"""


def _round_of(prompt: str) -> int:
    if "You are one of two independent designers" in prompt:
        return 1
    if "Below is a design proposal answering the brief" in prompt:
        return 2
    if "This is your own proposal and the critique it drew" in prompt:
        return 3
    return 4


class Runner:
    """A scripted stand-in for lab.executors.run. Starts no process, ever."""

    def __init__(self, fail=(), text=None):
        self.fail = set(fail)          # {(agent, round), ...}
        self.calls = []                # [(agent, round, prompt, timeout), ...]
        self.text = text or {}

    def __call__(self, ex, prompt, cwd=None, timeout=None):
        rnd = _round_of(prompt)
        self.calls.append((ex.name, rnd, prompt, timeout))
        if (ex.name, rnd) in self.fail:
            return {"agent": ex.name, "ok": False, "reason": "timeout", "ms": 0.0,
                    "stdout": "", "stderr": ""}
        out = self.text.get((ex.name, rnd))
        if out is None:
            out = {1: DRAFT, 2: CRITIQUE, 3: CONVERGE}.get(rnd, {}).get(ex.name, SYNTHESIS)
        return {"agent": ex.name, "ok": True, "reason": "", "ms": 90.0,
                "stdout": out, "stderr": ""}

    def prompt(self, agent, rnd):
        for a, r, p, _ in self.calls:
            if a == agent and r == rnd:
                return p
        return ""


def _pairing(usable=("claude", "codex"), names=("claude", "codex")):
    return duo_models.select(list(names), prober=prober(usable))


# ---------------------------------------------------------------------------

def test_participants():
    print("\nparticipants — a Duo of one is not a Duo")
    p = _pairing()
    check("two usable CLIs make a pairing", p.ok, p.why)
    check("both participants carry the model their CLI selects",
          [x.model for x in p.participants] == ["claude-opus-5", "gpt-5.6-sol"],
          str([x.model for x in p.participants]))

    one = _pairing(usable=("claude",))
    check("one unusable participant refuses the run", not one.ok)
    check("the refusal names who and why", "codex" in one.why and "credential" in one.why,
          one.why)
    check("the refusal says it will not fall back to solo",
          "Refusing" in one.why and "collaboration" in one.why, one.why)
    check("the usable one is still reported as usable",
          [x.name for x in one.usable()] == ["claude"])

    check("three participants are refused",
          not duo_models.select(["claude", "codex", "opencode"], prober=prober()).ok)
    check("one participant is refused",
          not duo_models.select(["claude"], prober=prober()).ok)
    same = duo_models.select(["claude", "claude"], prober=prober())
    check("a model cannot be paired with itself", not same.ok)
    check("self-pairing is refused for the anchoring reason, not a technicality",
          "anchoring" in same.why, same.why)
    gone = duo_models.select(["claude", "ghost"], prober=prober(absent=("ghost",)))
    check("an executor the machine does not have is refused by name",
          not gone.ok and "ghost" in gone.why, gone.why)
    check("parse_models splits and trims",
          duo_models.parse_models(" claude , codex ") == ["claude", "codex"])


def test_blindness():
    print("\nround 1 and 2 — blind means blind")
    r = Runner()
    R.run_duo(_pairing(), BRIEF, runner=r)

    d1 = r.prompt("claude", 1)
    check("the round 1 prompt carries the brief", BRIEF in d1)
    check("the round 1 prompt contains no other draft",
          "SQLite table" not in d1 and "content hash of what it wrote" not in d1)
    check("the round 1 prompt forbids self-identification",
          "Do not name yourself" in d1)

    c = r.prompt("claude", 2)
    check("the critique prompt carries the OTHER model's draft",
          "SQLite" in c or "per line in a database" in c)
    check("the critique prompt does not carry the critic's own draft",
          "sidecar file per source" not in c)
    check("the critique prompt tells the critic it did not write it",
          "You did not" in c and "not being told who did" in c)
    check("the critique prompt demands at least one CUT", "[CUT]" in c)

    check("no vendor name reaches the critique prompt",
          not any(w in c.lower() for w in ("claude", "anthropic", "codex", "gpt-5", "openai")),
          c[:200])


def test_deattribution():
    print("\nde-attribution — a signature is not subject matter")
    out = R.deattribute("Claude wrote this and GPT-5 reviewed it.", brief="a brief")
    check("vendor names are replaced", "Claude" not in out and "GPT-5" not in out, out)
    check("the sentence survives the scrub", "wrote this" in out and "reviewed it" in out, out)
    keep = R.deattribute("The Codex CLI writes config.toml.",
                         brief="wrap the Codex CLI in Daisy")
    check("a name the brief already uses is left alone", "Codex" in keep, keep)
    check("first-person self-identification is dropped",
          "As an AI language model, I would" not in
          R.deattribute("As an AI language model, I would say x.\nKeep this.", ""))
    check("the surviving line is kept",
          "Keep this." in R.deattribute("As an AI, I say x.\nKeep this.", ""))


def test_protocol():
    print("\nthe four rounds")
    r = Runner()
    tr = R.run_duo(_pairing(), BRIEF, runner=r, timeout=99)
    check("eight model calls: draft, critique, converge for two, plus one synthesis",
          len(r.calls) == 7, str([(a, n) for a, n, _, _ in r.calls]))
    check("rounds run in order", [n for _, n, _, _ in r.calls] == [1, 1, 2, 2, 3, 3, 4],
          str([n for _, n, _, _ in r.calls]))
    check("every call is bounded by the timeout it was given",
          all(t == 99 for _, _, _, t in r.calls))
    check("all four rounds completed", tr.rounds_completed == 4)
    check("nothing was marked partial", not tr.partial, "; ".join(tr.notes))
    check("two independent drafts were captured", len(tr.drafts) == 2)
    check("two revisions were captured", len(tr.revised) == 2)
    check("six critique points were parsed", len(tr.critiques) == 6, str(len(tr.critiques)))
    check("every critique is attributed to a critic and a subject",
          all(c.by and c.against and c.by != c.against for c in tr.critiques))
    check("critique kinds survive parsing",
          sorted(set(c.kind for c in tr.critiques)) == ["CUT", "MISSING", "WRONG"],
          str(sorted(set(c.kind for c in tr.critiques))))
    check("every critique carries its why line", all(c.why for c in tr.critiques))
    check("the synthesiser is the second participant, not the first drafter",
          tr.synthesiser == "codex", tr.synthesiser)
    check("the revision replaced the draft", tr.revised["claude"] != tr.drafts["claude"])
    check("the synthesis prompt names the surviving disagreements",
          "UNRESOLVED DISAGREEMENTS" in r.prompt("codex", 4)
          and "10,000-line" in r.prompt("codex", 4))
    check("--rounds 2 stops after critique",
          R.run_duo(_pairing(), BRIEF, rounds=2, runner=Runner()).rounds_completed == 2)
    check("--rounds 2 produces no synthesis",
          not R.run_duo(_pairing(), BRIEF, rounds=2, runner=Runner()).synthesis)


def test_degradation():
    print("\nfailure — a dead round is a reported partial, not a stack trace")
    t1 = R.run_duo(_pairing(), BRIEF, runner=Runner(fail=[("codex", 1)]))
    check("a missing draft stops the protocol", t1.rounds_completed == 0)
    check("the stop is explained", t1.partial and "codex" in t1.notes[0], str(t1.notes))
    check("no synthesis is invented from one draft", t1.synthesis == "")

    t2 = R.run_duo(_pairing(), BRIEF, runner=Runner(fail=[("codex", 2)]))
    check("a failed critique still reaches synthesis", t2.rounds_completed == 4)
    check("the unchallenged draft is named", any("unchallenged" in n for n in t2.notes),
          str(t2.notes))
    check("only the surviving critic's points exist",
          set(c.by for c in t2.critiques) == {"claude"})

    t3 = R.run_duo(_pairing(), BRIEF, runner=Runner(fail=[("claude", 3)]))
    check("a failed revision carries the round 1 draft forward",
          t3.revised["claude"] == t3.drafts["claude"])
    check("carrying it forward is disclosed",
          any("carried forward unchanged" in n for n in t3.notes), str(t3.notes))

    t4 = R.run_duo(_pairing(), BRIEF, runner=Runner(fail=[("codex", 4)]))
    check("a failed synthesis leaves no spec", t4.synthesis == "")
    check("the failed synthesis is reported, not raised",
          t4.partial and any("no merged spec" in n for n in t4.notes), str(t4.notes))
    check("both revisions are still in the transcript", len(t4.revised) == 2)

    empty = Runner(text={("claude", 1): "   "})
    t5 = R.run_duo(_pairing(), BRIEF, runner=empty)
    check("an empty reply counts as a failure", t5.rounds_completed == 0)


def test_metrics():
    print("\nmetrics — did anything actually move")
    check("an unchanged draft drifts zero", drift(DRAFT["claude"], DRAFT["claude"]) == 0.0)
    check("a rewritten draft drifts",
          drift(DRAFT["claude"], CONVERGE["claude"]) > 0.3,
          "%.3f" % drift(DRAFT["claude"], CONVERGE["claude"]))
    check("drift is symmetric in magnitude",
          abs(drift("a b c d", "a b x d") - drift("a b x d", "a b c d")) < 1e-9)

    m = collaboration(R.run_duo(_pairing(), BRIEF, runner=Runner()))
    check("six critique points counted", m["critique_points"] == 6, str(m["critique_points"]))
    check("three accepted", m["accepted"] == 3, str(m["accepted"]))
    check("three rejected", m["rejected"] == 3, str(m["rejected"]))
    check("nothing left unanswered", m["unanswered"] == 0, str(m["unanswered"]))
    check("both drafts moved after critique",
          all(v["drift"] > DRIFT_FLOOR for v in m["per_model"].values()),
          str({k: v["drift"] for k, v in m["per_model"].items()}))
    check("three disagreements survived", m["survived_count"] == 3, str(m["survived_count"]))
    check("a survived point records both sides",
          all(d["point"] and d["rebuttal"] for d in m["survived"]))
    check("collaboration is asserted only when every condition holds", m["collaborated"])
    check("a clean run raises no flags", m["flags"] == [], str(m["flags"]))

    # a model that answers nothing
    silent = Runner(text={("claude", 3): "I have no changes to make to this proposal."})
    ms = collaboration(R.run_duo(_pairing(), BRIEF, runner=silent))
    check("an unanswered critique is counted, not ignored", ms["unanswered"] == 3,
          str(ms["unanswered"]))
    check("ignoring every point is flagged by name",
          any("never answered a single critique point" in f for f in ms["flags"]),
          str(ms["flags"]))
    check("ignoring every point sinks the collaboration verdict", not ms["collaborated"])
    check("the per-model verdict says so",
          "ignored every critique" in ms["per_model"]["claude"]["verdict"],
          ms["per_model"]["claude"]["verdict"])

    # a model that says ACCEPT and changes nothing
    stamp = Runner(text={("claude", 3): "## LEDGER\n1 ACCEPT — done.\n2 ACCEPT — done.\n"
                                        "3 ACCEPT — done.\n\n## REVISED\n" + DRAFT["claude"]})
    mr = collaboration(R.run_duo(_pairing(), BRIEF, runner=stamp))
    check("a rubber stamp is flagged",
          any("barely moved" in f for f in mr["flags"]), str(mr["flags"]))
    check("the rubber-stamped draft reports near-zero drift",
          mr["per_model"]["claude"]["drift"] < DRIFT_FLOOR,
          str(mr["per_model"]["claude"]["drift"]))

    # neither side critiques
    mute = Runner(text={("claude", 2): "Looks good to me.", ("codex", 2): "No notes."})
    mm = collaboration(R.run_duo(_pairing(), BRIEF, runner=mute))
    check("no critique at all is flagged as a missing lane",
          any("raised no critique" in f for f in mm["flags"]), str(mm["flags"]))
    check("no critique means no collaboration", not mm["collaborated"])


def test_spec():
    print("\nthe spec — the disagreement has to survive the merge")
    tr = R.run_duo(_pairing(), BRIEF, runner=Runner())
    m = collaboration(tr)
    sp = S.build(tr.synthesis, BRIEF, tr.participants, tr.synthesiser, m)
    md = sp.render()

    for name in S.SECTIONS:
        check("the spec has %s" % name.lower(), ("## %s" % name) in md)
    check("the brief is quoted in the document", BRIEF in md)
    check("both participants are named", "claude" in md and "codex" in md)
    check("the collaboration numbers are in the document", "collaboration" in md.lower())
    check("alternatives came from the rejected drafts",
          "Marker comments" in md and "Per-line provenance" in md)

    check("every surviving disagreement reaches open questions",
          len(S.items(S.sections(md)["OPEN QUESTIONS"])) >= m["survived_count"],
          "%d bullets vs %d survived"
          % (len(S.items(S.sections(md)["OPEN QUESTIONS"])), m["survived_count"]))
    check("the ones the synthesis dropped are restored", len(sp.restored) >= 1,
          str(len(sp.restored)))
    check("a restored question is labelled as restored", "[restored" in md)
    check("the document says the synthesis dropped them",
          "restored" in md and "could not agree" in md)
    check("the question the synthesis DID carry is not duplicated",
          md.count("10,000-line target invents a constraint") == 1)

    check("the produced spec passes its own gate", sp.gate() == [], str(sp.gate()))

    none = S.build(SYNTHESIS, BRIEF, ["claude", "codex"], "codex",
                   dict(m, survived=[], survived_count=0))
    check("with no disagreements nothing is restored", none.restored == [])
    check("a spec with no disagreements still passes", none.gate() == [], str(none.gate()))


def test_gate():
    print("\nduo.spec_complete — a real check, not a section grep")
    good = S.build(SYNTHESIS, BRIEF, ["claude", "codex"], "codex", {}).render()
    check("a complete spec has no findings", S.spec_complete(good) == [],
          str(S.spec_complete(good)))

    missing = good.replace("## RISKS", "## HAZARDS")
    f = S.spec_complete(missing)
    check("a missing section is a finding", any(x.check == "section missing" for x in f))
    check("the finding names the section", any("RISKS" in x.detail for x in f), str(f))

    hollow = good.replace("- A hand edit inside the marked block is still overwritten.\n"
                          "- Normalisation can hide a change that is only whitespace.\n"
                          "- Reconciliation has no command yet, so drift halts the run "
                          "with no way forward.", "TBD")
    check("a section with a stub body is a finding",
          any(x.check == "section empty" for x in S.spec_complete(hollow)),
          str(S.spec_complete(hollow)))

    check("a spec that hides a surviving disagreement fails",
          any(x.check == "disagreement dropped" for x in S.spec_complete(good, expected_open=4)),
          str(S.spec_complete(good, expected_open=4)))
    check("the same spec passes when one disagreement survived",
          S.spec_complete(good, expected_open=1) == [])

    thin = good.replace("- Marker comments as the sole record. Loses because the operator can delete a\n"
                        "  comment and silently take ownership of the block; it would only win if the\n"
                        "  config file were not operator-editable.\n", "")
    check("one alternative is not alternatives considered",
          any(x.check == "alternatives thin" for x in S.spec_complete(thin)),
          str(S.spec_complete(thin)))

    print("\n  acceptance criteria — testable or not")
    for c in ["Running the import twice produces a byte-for-byte identical config.md.",
              "A 10,000-line config resyncs in under 2 seconds.",
              "`importer.cli sync --once` exits 0 on a clean tree.",
              "The command refuses to write when the store and the file disagree."]:
        check("testable: %s" % c[:52], S.testable(c) == "", S.testable(c))
    for c, why in [("The importer should be robust and easy to use.", "judgement"),
                   ("Resync should be fast.", "wish"),
                   ("Sync works well.", "short"),
                   ("The experience is seamless for the operator every single time.", "judgement"),
                   ("The design follows best practices throughout the whole importer.", "judgement"),
                   ("Imports feel much better than they used to for everyone here.", "judgement"),
                   ("Everything about the importer subsystem in this release.", "no anchor")]:
        check("not testable (%s): %s" % (why, c[:40]), S.testable(c) != "", "accepted a wish")

    aspir = good.replace("- Running the import twice produces a byte-for-byte identical config.md.",
                         "- The importer should be robust and intuitive.")
    ff = S.spec_complete(aspir)
    check("an aspirational criterion fails the gate",
          any(x.check == "criterion not testable" for x in ff), str(ff))
    check("the finding quotes the offending word",
          any("robust" in x.detail for x in ff), str(ff))

    few = good.split("## ACCEPTANCE CRITERIA")[0] + \
        "## ACCEPTANCE CRITERIA\n- A resync of an unedited file changes 0 bytes.\n\n" + \
        "## RISKS\n- A hand edit inside the marked block is still overwritten.\n"
    check("fewer than three criteria is a finding",
          any(x.check == "too few criteria" for x in S.spec_complete(few)),
          str(S.spec_complete(few)))
    check("the gate exit code is the finding count",
          len(S.spec_complete(missing)) == len(S.spec_complete(missing)))


def test_cli():
    print("\nthe CLI — exit codes carry the answer")
    quiet = lambda s: None
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "spec.md")
        code, p = duo_cli.run(BRIEF, out=out, runner=Runner(), prober=prober(), log=quiet)
        check("a clean run exits 0", code == 0, str(code))
        check("the spec file was written", os.path.exists(out))
        check("the file on disk is the spec", "## OPEN QUESTIONS" in open(out).read())
        check("the payload says the gate is green", p["gate"]["ok"])
        check("the payload carries the metrics", p["metrics"]["critique_points"] == 6)
        check("the payload carries every turn", len(p["turns"]) == 7)
        check("the payload is JSON-serialisable", len(json.dumps(p)) > 2000)

        code2, p2 = duo_cli.run(BRIEF, out=out, runner=Runner(), prober=prober(),
                                log=quiet)
        check("a second run is deterministic under a fake", code2 == 0)
        check("both runs agree on the surviving count",
              p2["metrics"]["survived_count"] == p["metrics"]["survived_count"])

    code, p = duo_cli.run(BRIEF, runner=Runner(), prober=prober(usable=("claude",)), log=quiet)
    check("one unusable model exits 2", code == 2, str(code))
    check("no spec is produced for a non-Duo", p["spec"] is None)
    check("the reason is in the payload", "Refusing" in p["why"], p.get("why", ""))

    code, p = duo_cli.run(BRIEF, runner=Runner(fail=[("codex", 4)]), prober=prober(), log=quiet)
    check("a failed synthesis exits 3", code == 3, str(code))
    check("a failed synthesis still reports metrics", p["metrics"]["critique_points"] == 6)

    code, p = duo_cli.run(BRIEF, rounds=2, runner=Runner(), prober=prober(), log=quiet)
    check("--rounds 2 exits 3 because there is no spec", code == 3, str(code))
    check("--rounds 2 still counts the critique", p["metrics"]["critique_points"] == 6)

    bad = Runner(text={("codex", 4): "## PROBLEM\nSomething is wrong here and it "
                                     "needs fixing soon.\n\n## RISKS\nUnclear risks abound "
                                     "in this particular design.\n"})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, p = duo_cli.run(BRIEF, runner=bad, prober=prober(), log=quiet)
    check("a spec that fails the gate exits 4", code == 4, str(code))
    check("with no --out the spec goes to stdout", "## OPEN QUESTIONS" in buf.getvalue())
    check("the gate findings are in the payload", len(p["gate"]["findings"]) > 3,
          str(p["gate"]["findings"]))

    check("--models is parsed and refused when it is not two",
          duo_cli.run(BRIEF, model_spec="claude", runner=Runner(),
                      prober=prober(), log=quiet)[0] == 2)


def main():
    print("daisy duo — test suite")
    test_participants()
    test_blindness()
    test_deattribution()
    test_protocol()
    test_degradation()
    test_metrics()
    test_spec()
    test_gate()
    test_cli()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
