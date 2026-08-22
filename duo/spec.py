"""
The document the two models are for, and the gate that decides it is one.

A PRD that reads well and hides the thing its authors could not agree on is
worse than one that names it: the hidden disagreement does not go away, it just
resurfaces during implementation with nobody's name on it. So the structure
here is fixed, Alternatives Considered is populated from what the drafts
REJECTED (which is the only part of a two-model run a one-model run cannot
produce), and every disagreement that survived round 3 lands in Open Questions
whether or not the synthesising model remembered to put it there. When it did
not, the bullet says so — a spec quietly completed by its own tooling is
exactly the theatre this feature is trying not to be.

`spec_complete` is the gate. It is a real check, not a section-name grep:
acceptance criteria have to be statements someone could fail, and a run whose
disagreements survived has to show at least that many open questions.

    IS      a fixed seven-section Markdown document, and a structural gate over
            any document claiming to be one
    IS NOT  a judge of whether the design is correct, feasible, or worth doing.
            A perfectly-structured spec for a bad idea passes this gate, and
            should — that is a different reviewer's job.

    python3 -m duo.spec spec.md [--open N]

Exit code is the finding count, so it drops into a gate runner unchanged.
Zero third-party dependencies.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

from .rounds import sections

SECTIONS = ["PROBLEM", "NON-GOALS", "PROPOSED APPROACH", "ALTERNATIVES CONSIDERED",
            "OPEN QUESTIONS", "ACCEPTANCE CRITERIA", "RISKS"]

# A section shorter than this is a heading with an apology under it.
MIN_BODY = 24
MIN_CRITERIA = 3
MIN_ALTERNATIVES = 2

_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+(.*\S)\s*$")
_NONE = re.compile(r"^\s*(?:[-*+]\s*)?none\b", re.I)

# Words that describe a feeling about software rather than an observable fact
# about it. None of these can be failed by a test, which is what disqualifies
# them from a section whose whole job is to be failable.
_ASPIRATION = re.compile(
    r"\b(intuitive|user[\s-]?friendly|seamless(?:ly)?|robust(?:ness)?|delightful|elegant|"
    r"modern|best[\s-]practices?|easy to use|high[\s-]quality|world[\s-]class|"
    r"as (?:needed|appropriate)|where appropriate|if necessary|reasonably|"
    r"nice(?:ly)?|polished|production[\s-]ready)\b", re.I)

# Vaguer still, but occasionally rescued by a number: "fast" alone is a wish,
# "fast enough to finish 100 rows in 2s" is a test.
_SOFT = re.compile(r"\b(fast|quick(?:ly)?|efficient(?:ly)?|simple|scalable|performant|"
                   r"clean(?:ly)?|better|improved|minimal|lightweight)\b", re.I)

# What makes a line checkable: a quantity, a code span, a path or command, or a
# verb that names a binary outcome.
_ANCHOR = re.compile(r"\d|`[^`]+`|\b\w+\.(?:py|sh|md|json|html|toml|db)\b|"
                     r"\b(returns?|exits?|rejects?|refuses?|emits?|writes?|logs?|raises?|"
                     r"fails?|passes|matches|contains?|equals?|produces?|appends?|"
                     r"must not|never|no more than|at (?:most|least)|within|non-empty|"
                     r"zero|identical|byte[\s-]for[\s-]byte)\b", re.I)


@dataclass(frozen=True)
class Finding:
    check: str
    detail: str


def items(body: str) -> list:
    """Bullets if the model wrote bullets, else paragraphs. Models do both.

    A bullet owns the wrapped lines under it. Taking only the first line looked
    fine on a 70-column bullet and silently truncated every longer one, which
    for an open question meant losing the half that names the two positions.
    """
    out, cur, bulleted = [], None, False
    for ln in (body or "").split("\n"):
        m = _BULLET.match(ln)
        if m:
            bulleted = True
            if cur is not None:
                out.append(cur)
            cur = m.group(1).strip()
        elif cur is not None:
            if ln.strip():
                cur += " " + ln.strip()
            else:
                out.append(cur)
                cur = None
    if cur is not None:
        out.append(cur)
    if bulleted:
        return out
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", body or "") if p.strip()]


def testable(criterion: str) -> str:
    """'' when the criterion could be failed by a test, else why it could not."""
    c = criterion.strip()
    if len(c) < MIN_BODY:
        return "too short to state a condition"
    a = _ASPIRATION.search(c)
    if a:
        return "%r is a judgement, not an outcome" % a.group(0)
    s = _SOFT.search(c)
    if s and not re.search(r"\d", c):
        return "%r with no number attached is a wish" % s.group(0)
    if not _ANCHOR.search(c):
        return "no number, command, file or binary outcome to check against"
    return ""


def spec_complete(md: str, expected_open: int = 0) -> list:
    """The gate. Empty list means the document is a spec."""
    out, secs = [], sections(md or "")

    for name in SECTIONS:
        if name not in secs:
            out.append(Finding("section missing", name))
        elif len(secs[name].strip()) < MIN_BODY:
            out.append(Finding("section empty", "%s has %d characters of body"
                               % (name, len(secs[name].strip()))))

    alts = items(secs.get("ALTERNATIVES CONSIDERED", ""))
    if len(alts) < MIN_ALTERNATIVES:
        out.append(Finding("alternatives thin",
                           "%d listed, %d required — this section is where a two-model "
                           "run earns its keep" % (len(alts), MIN_ALTERNATIVES)))

    oq_body = secs.get("OPEN QUESTIONS", "")
    oq = [q for q in items(oq_body) if not _NONE.match(q)]
    if expected_open and len(oq) < expected_open:
        out.append(Finding("disagreement dropped",
                           "%d disagreement%s survived round 3 but open questions lists %d"
                           % (expected_open, "" if expected_open == 1 else "s", len(oq))))
    if not expected_open and oq_body.strip() and not oq and not _NONE.search(oq_body):
        out.append(Finding("open questions unreadable",
                           "no bullets and no explicit 'None' — a reader cannot tell "
                           "whether anything is open"))

    crit = items(secs.get("ACCEPTANCE CRITERIA", ""))
    if len(crit) < MIN_CRITERIA:
        out.append(Finding("too few criteria",
                           "%d listed, %d required" % (len(crit), MIN_CRITERIA)))
    for c in crit:
        why = testable(c)
        if why:
            out.append(Finding("criterion not testable", "%s  <-  %s" % (why, c[:80])))
    return out


# ---------------------------------------------------------------------------
# building the document
# ---------------------------------------------------------------------------

_STOP = set("the a an and or of to in on for with that this it is are be by as at from "
            "not no but if then than into over under should would could must will can "
            "we you they there their its our".split())


def _content(text: str) -> set:
    return set(w for w in re.findall(r"[a-z][a-z0-9-]{3,}", (text or "").lower())
               if w not in _STOP)


def _mentioned(point: str, text: str) -> bool:
    """Is this disagreement the one that open question is about?

    A third of the point's content words is a deliberately loose bar. Tight
    matching would restore bullets the model already wrote in its own words,
    and a spec that lists the same open question twice is a worse failure than
    one that occasionally trusts a paraphrase.
    """
    want = _content(point)
    if not want:
        return False
    return len(want & _content(text)) / float(len(want)) >= 0.34


def _carry(body: str, survived: list) -> tuple:
    """Normalise open questions to one bullet each, restoring anything dropped.

    Matching is greedy and per-bullet rather than against the whole section: a
    synthesis that folded two disagreements into one sentence would otherwise
    look like it had carried both, and the count would silently come up short.
    """
    kept = [" ".join(i.split()) for i in items(body) if not _NONE.match(i)]
    claimed, restored = set(), []
    for d in survived:
        hit = next((i for i in range(len(kept))
                    if i not in claimed and _mentioned(d.get("point", ""), kept[i])), None)
        if hit is None:
            restored.append(d)
        else:
            claimed.add(hit)
    for d in restored:
        kept.append("%s? Position A (%s): %s Position B (%s): %s Decides: whichever "
                    "position the first implementation falsifies. "
                    "[restored — the synthesis dropped this]"
                    % (d.get("point", "").rstrip(".?"), d.get("by", "?"),
                       d.get("why", "") or "raised in round 2.",
                       d.get("against", "?"),
                       d.get("rebuttal", "") or "rejected in round 3."))
    return "\n".join("- " + k for k in kept), restored


@dataclass
class Spec:
    brief: str = ""
    participants: list = field(default_factory=list)
    synthesiser: str = ""
    sections: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    restored: list = field(default_factory=list)
    expected_open: int = 0

    def render(self) -> str:
        from .metrics import summary
        out = ["# Spec — %s" % _title(self.brief), "",
               "> %s" % self.brief.strip().replace("\n", " "), "",
               "Produced by Daisy Duo: %s, four rounds, synthesised by %s."
               % (" and ".join(self.participants), self.synthesiser or "—"), ""]
        if self.metrics:
            out += ["## Collaboration", "", "```", summary(self.metrics), "```", ""]
        for name in SECTIONS:
            out += ["## %s" % name, "", self.sections.get(name, "").strip() or "_not produced_", ""]
        if self.restored:
            out += ["---", "",
                    "%d disagreement%s below %s raised by the synthesis and %s restored "
                    "from the round 3 ledger. A merged spec that drops the thing the two "
                    "models could not agree on is the failure mode this run exists to avoid."
                    % (len(self.restored), "" if len(self.restored) == 1 else "s",
                       "was not" if len(self.restored) == 1 else "were not",
                       "was" if len(self.restored) == 1 else "were"), ""]
        return "\n".join(out).rstrip() + "\n"

    def gate(self) -> list:
        return spec_complete(self.render(), self.expected_open)


def _title(brief: str) -> str:
    t = " ".join((brief or "untitled").split())
    return t[:68] + ("…" if len(t) > 68 else "")


def build(synthesis: str, brief: str, participants: list, synthesiser: str,
          metrics: dict) -> Spec:
    """Assemble the spec, and put back any disagreement the synthesis lost."""
    secs = sections(synthesis or "")
    survived = (metrics or {}).get("survived", [])
    sp = Spec(brief=brief, participants=list(participants), synthesiser=synthesiser,
              sections={k: secs.get(k, "") for k in SECTIONS},
              metrics=metrics or {}, expected_open=len(survived))

    if survived:
        sp.sections["OPEN QUESTIONS"], sp.restored = _carry(
            sp.sections.get("OPEN QUESTIONS", ""), survived)
    return sp


# ---------------------------------------------------------------------------
# the gate as a command
# ---------------------------------------------------------------------------

def report(findings: list, path: str) -> str:
    if not findings:
        return "duo.spec_complete  PASS  %s  —  7 sections, criteria testable" % path
    rows = ["duo.spec_complete  FAIL  %s  —  %d finding%s"
            % (path, len(findings), "" if len(findings) == 1 else "s")]
    w = max(len(f.check) for f in findings)
    for f in findings:
        rows.append("  %-*s  %s" % (w, f.check, f.detail))
    return "\n".join(rows)


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 2
    expected = 0
    if "--open" in argv:
        i = argv.index("--open")
        expected = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    total = 0
    for path in argv:
        try:
            md = open(path, encoding="utf-8").read()
        except OSError as e:
            print("cannot read %s: %s" % (path, e))
            return 2
        f = spec_complete(md, expected)
        print(report(f, path))
        total += len(f)
    return total


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
