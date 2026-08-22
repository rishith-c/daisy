"""
The four rounds, and the prompts that make them different from each other.

Two models answering the same question and having their answers stapled
together is duplication, not collaboration. One drafting and the other saying
"looks good" is worse: it has the shape of review with none of the content. So
each round here has a job the other three cannot do, and the prompts are the
feature — the orchestration around them is twenty lines of plumbing.

    1  DRAFT      both models get the brief, neither sees the other. Blind
                  because a model shown a draft edits it instead of disagreeing
                  with it; anchoring destroys the only signal we are after.
    2  CRITIQUE   each model gets the OTHER's draft with the authorship
                  scrubbed. Attribution changes behaviour — "here is GPT-5's
                  plan" is a different prompt from "here is a plan" — so the
                  scrub is a real step, not a courtesy.
    3  CONVERGE   each model sees the critique of its OWN draft, answers every
                  point ACCEPT or REJECT with a reason, then rewrites. The
                  ledger is what makes round 2 measurable instead of decorative,
                  and the rejections are the output we actually want.
    4  SYNTHESIS  one model merges the two revisions and is told, in the
                  prompt, that every surviving disagreement must land in Open
                  Questions unresolved.

    IS      prompt construction, round sequencing, tolerant parsing of what
            came back, and a partial result when a round fails
    IS NOT  a judge of who was right, a consensus engine, or a retry loop. A
            round that fails is reported as failed; asking the same model the
            same question again is not error handling.

Every model call is bounded by a timeout and every failure degrades to a
reported partial. Nothing here raises on a model behaving badly.

Zero third-party dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lab import executors

ROUND_TIMEOUT = 420

DRAFT_SECTIONS = ["PROBLEM", "NON-GOALS", "APPROACH", "KEY DECISION",
                  "REJECTED ALTERNATIVE", "RISKS", "ACCEPTANCE"]


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

DRAFT_PROMPT = """You are one of two independent designers answering the same brief. You are \
writing FIRST and ALONE. You will not see the other designer's work until you have committed to \
your own, and they will not see yours. That is deliberate: whatever you hedge on now becomes a \
hole someone else gets to point at.

BRIEF
{{BRIEF}}

Write a design proposal.

- Commit. Pick one approach and defend it. A proposal that lists three options and declines to \
choose cannot be disagreed with, and disagreement is the entire point of this exercise.
- Be specific enough to be wrong. Name files, data shapes, commands, limits, numbers. "Handle \
errors gracefully" is unfalsifiable. "On a 429, retry twice at 2s and 8s, then fail the run" can \
be argued with.
- No preamble, no restatement of the brief, no closing pleasantries.
- Do not name yourself, your vendor, or your model anywhere in the document. The next reader must \
not be able to tell who wrote it.
- Under 450 words.

Use exactly these headings, each on its own line, in this order:

## PROBLEM
One paragraph on what is actually broken or missing — not on what we are building.

## NON-GOALS
Three bullets: things a reasonable person would expect in scope that you are deliberately \
excluding, and why.

## APPROACH
The design. Concrete. This is most of the document.

## KEY DECISION
One sentence naming the single most consequential choice you made.

## REJECTED ALTERNATIVE
The strongest approach you considered and did not take, and the specific reason it loses to \
yours. Not a strawman.

## RISKS
Two or three bullets, each stated as a condition under which this design breaks.

## ACCEPTANCE
Four bullets. Each must be checkable by someone who never read this document: a number, a named \
command, a named file, or a binary outcome.
"""


CRITIQUE_PROMPT = """Below is a design proposal answering the brief that follows. You did not \
write it. You are not being told who did, and you should not guess — treating it as a colleague's \
work or as a machine's changes how hard you push, and neither is the right amount. Judge the \
document.

Your job is not to improve it, approve it, or restate it. Your job is to find what is wrong with \
it, what it is missing, and what it should cut.

BRIEF
{{BRIEF}}

PROPOSAL
{{DRAFT}}

Rules:

- Between three and six points. No more. Rank them: the one you would fight hardest for goes first.
- Every point must be specific to THIS document and actionable. "Needs more detail" is not a \
point. "The retry policy is unbounded, so a failing call loops forever" is.
- At least one [CUT]. Every proposal carries something that is not paying for itself, and the \
author is the last person able to see it.
- Do not compliment. Do not summarise. Do not write a preamble or a conclusion.
- If you think the whole approach is wrong, that is point 1, and name what you would do instead.

Format every point exactly like this, one point per line pair:

1. [WRONG] the claim that is incorrect, in one sentence
   why: why it is incorrect, and what believing it costs
2. [MISSING] the thing that is absent, in one sentence
   why: what breaks without it
3. [CUT] the thing to remove, in one sentence
   why: what keeping it costs

The only tags are [WRONG], [MISSING], [CUT]. Nothing before point 1. Nothing after the last point.
"""


CONVERGE_PROMPT = """This is your own proposal and the critique it drew. The critic did not know \
who wrote it, and you do not know who they are.

YOUR PROPOSAL
{{DRAFT}}

CRITIQUE
{{CRITIQUE}}

Answer every numbered point, then revise.

Rejecting is expected. Do not accept a point you think is wrong — a critique waved through \
produces a document that looks reviewed and was not. If the critic misread the design, say so and \
say exactly what they misread. A reasoned REJECT is worth more here than a silent edit, because \
the disagreements that survive this round get published as open questions instead of being \
quietly averaged away.

Equally, do not defend a point out of authorship. If they are right, change it, and say what \
changed.

Write two parts and nothing else.

## LEDGER
One line per critique point, in the critic's numbering, using exactly this shape:

1 ACCEPT — what you changed in the revision, concretely
2 REJECT — why the point is wrong, in one sentence

Every number in the critique appears here exactly once.

## REVISED
The full revised proposal under the same headings as your original: ## PROBLEM, ## NON-GOALS, \
## APPROACH, ## KEY DECISION, ## REJECTED ALTERNATIVE, ## RISKS, ## ACCEPTANCE. Rewrite it in \
full — do not write "unchanged", do not write a diff. Under 450 words. Do not name yourself or \
your vendor.
"""


SYNTHESIS_PROMPT = """Two designers answered the same brief independently, critiqued each other \
blind, and revised. You are merging their revisions into one specification.

You are not a judge picking a winner and you are not an averager. Where they agree, write it \
once. Where one is more specific, take the specific one. Where they genuinely disagree, that \
disagreement is the most valuable thing on the page and it must survive into the output intact.

BRIEF
{{BRIEF}}

DRAFT A
{{A}}

DRAFT B
{{B}}

UNRESOLVED DISAGREEMENTS
Each of these is a point one designer raised and the other rejected with a reason. They are still \
open. Every one must appear in ## OPEN QUESTIONS, phrased as the question that has to be \
answered, with both positions named. Do not resolve them. Do not pick a side. Do not soften them \
into "we should consider".
{{DISAGREEMENTS}}

Write the specification under exactly these headings, in this order:

## PROBLEM
## NON-GOALS
## PROPOSED APPROACH
## ALTERNATIVES CONSIDERED
## OPEN QUESTIONS
## ACCEPTANCE CRITERIA
## RISKS

Per section:

ALTERNATIVES CONSIDERED — at least two, populated from what the drafts rejected. Each draft named \
an approach it did not take, and where the drafts differ, one is an alternative to the other. \
Name the approach, name who would have to be wrong for it to win, and name why it lost.

OPEN QUESTIONS — one bullet per unresolved disagreement above, in this shape:
- <the question>? Position A: <one clause>. Position B: <one clause>. Decides: <the evidence or \
decision that would close it>.
Nothing else goes in this section. If the list above is empty, write exactly: None survived — \
both designers converged.

ACCEPTANCE CRITERIA — exactly five bullets. Each must be checkable by someone who never read this \
document, which in practice means each carries a number, a named command, a named file, or a \
binary outcome.
  good: `spec_complete` exits 0 on a document containing all seven sections
  good: a run with one usable model exits 2 and writes no spec file
  bad:  the tool should be robust and easy to use
  bad:  performance should be acceptable
Words like robust, seamless, intuitive, user-friendly, elegant, modern and best-practice are not \
acceptance criteria and will fail the gate that reads this document.

No preamble. No closing summary. Start with "## PROBLEM".
"""


def fill(template: str, **kw) -> str:
    """{{KEY}} substitution. Not str.format: these prompts are full of braces."""
    for k, v in kw.items():
        template = template.replace("{{%s}}" % k, str(v))
    return template


# ---------------------------------------------------------------------------
# anonymisation
# ---------------------------------------------------------------------------

# Vendor and model tokens that identify an author. A name the BRIEF already
# uses is subject matter, not a signature, so it is left alone — scrubbing
# "Codex" out of a brief about wrapping the Codex CLI would destroy the
# document to protect an anonymity nobody asked for.
_VENDOR = re.compile(
    r"\b(claude|anthropic|opus|sonnet|haiku|fable|openai|chatgpt|codex|opencode|"
    r"gemini|llama|mistral|gpt-?[0-9][\w.\-]*|gpt)\b", re.I)

# First-person self-identification survives even when the vendor word does not.
_SELF_ID = re.compile(r"(?im)^.*\b(as an ai|as a language model|i am an? (ai|assistant|model))\b.*$")


def deattribute(text: str, brief: str = "") -> str:
    """Strip authorship tells from a draft before the other model reads it."""
    allowed = set(m.group(0).lower() for m in _VENDOR.finditer(brief or ""))

    def sub(m):
        return m.group(0) if m.group(0).lower() in allowed else "[the author]"

    return _SELF_ID.sub("", _VENDOR.sub(sub, text or "")).strip()


# ---------------------------------------------------------------------------
# parsing what came back
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```[\w-]*\s*\n(.*)\n\s*```\s*$", re.S)

# Three ways a model marks a heading, in the order of how much it committed to
# the instruction: a hash heading (any case, because half of them title-case
# it), a bolded caps line, and a bare caps line.
_HEAD = re.compile(r"^\s*(?:"
                   r"#{1,4}\s*\**\s*([A-Za-z][A-Za-z0-9 /&'\-]{2,34}?)\**"
                   r"|\*\*\s*([A-Z][A-Z0-9 /&'\-]{2,34}?)\s*\*\*"
                   r"|([A-Z][A-Z0-9 /&'\-]{2,34}?)"
                   r")\s*:?\s*$")
_POINT = re.compile(r"^\s*(?:[-*]\s*)?\**\s*(\d{1,2})\s*[.)]\s*\**\s*"
                    r"(?:\[(WRONG|MISSING|CUT)\]\s*)?(.+?)\s*\**\s*$", re.I)
_WHY = re.compile(r"^\s*\**\s*why\s*\**\s*:\s*(.+?)\s*$", re.I)
_LEDGER = re.compile(r"^\s*(?:[-*]\s*)?\**\s*(\d{1,2})\s*[.):]?\s*\**\s*"
                     r"(ACCEPT|REJECT)\b\**[\s—:.\-]*(.*)$", re.I)


def unfence(text: str) -> str:
    """Some CLIs wrap the whole answer in a code fence. Take the inside."""
    m = _FENCE.match((text or "").strip())
    return m.group(1) if m else (text or "").strip()


def sections(text: str) -> dict:
    """Split on ALL-CAPS headings, however the model chose to mark them up."""
    out, name, buf = {}, None, []
    for ln in unfence(text).split("\n"):
        m = _HEAD.match(ln)
        if m:
            if name:
                out[name] = "\n".join(buf).strip()
            name = (m.group(1) or m.group(2) or m.group(3)).strip().upper()
            buf = []
        elif name:
            buf.append(ln)
    if name:
        out[name] = "\n".join(buf).strip()
    return out


@dataclass
class Critique:
    n: int
    kind: str
    point: str
    why: str = ""
    by: str = ""
    against: str = ""
    verdict: str = ""       # ACCEPT | REJECT | "" when the ledger never answered it
    response: str = ""

    def survived(self) -> bool:
        return self.verdict.upper() == "REJECT"


def parse_critique(text: str, by: str = "", against: str = "") -> list:
    """Numbered points, tolerant of a model that dropped the tag."""
    out, cur = [], None
    for ln in unfence(text).split("\n"):
        m = _POINT.match(ln)
        if m and len(m.group(3)) > 8:
            cur = Critique(int(m.group(1)), (m.group(2) or "POINT").upper(),
                           m.group(3).strip(), by=by, against=against)
            out.append(cur)
            continue
        w = _WHY.match(ln)
        if w and cur is not None and not cur.why:
            cur.why = w.group(1).strip()
    # A model that numbers 1,2,3 then restarts at 1 has written two lists; keep
    # the first occurrence of each number so the ledger can address them 1:1.
    seen, uniq = set(), []
    for c in out:
        if c.n in seen:
            continue
        seen.add(c.n)
        uniq.append(c)
    return uniq


def apply_ledger(critiques: list, text: str) -> int:
    """Attach ACCEPT/REJECT to each point. Returns how many were answered."""
    verdicts = {}
    body = sections(text).get("LEDGER", unfence(text))
    for ln in body.split("\n"):
        m = _LEDGER.match(ln)
        if m and int(m.group(1)) not in verdicts:
            verdicts[int(m.group(1))] = (m.group(2).upper(), m.group(3).strip())
    n = 0
    for c in critiques:
        if c.n in verdicts:
            c.verdict, c.response = verdicts[c.n]
            n += 1
    return n


def revised_body(text: str, fallback: str) -> str:
    """The rewritten proposal, or the original when the model did not produce one."""
    secs = sections(text)
    if "REVISED" in secs and len(secs["REVISED"]) > 120:
        return secs["REVISED"]
    # Some models drop the ## REVISED wrapper and go straight back to the draft
    # headings. That is fine — reassemble from the headings we asked for.
    kept = [(k, v) for k, v in secs.items() if k in DRAFT_SECTIONS]
    if len(kept) >= 4:
        return "\n\n".join("## %s\n%s" % (k, v) for k, v in kept)
    return fallback


# ---------------------------------------------------------------------------
# the transcript
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    round: int
    role: str               # draft | critique | converge | synthesis
    author: str
    subject: str            # whose work this turn is about
    ok: bool = False
    ms: float = 0.0
    text: str = ""
    why: str = ""
    prompt: str = ""


@dataclass
class Transcript:
    brief: str = ""
    participants: list = field(default_factory=list)
    rounds_requested: int = 4
    rounds_completed: int = 0
    turns: list = field(default_factory=list)
    drafts: dict = field(default_factory=dict)
    revised: dict = field(default_factory=dict)
    critiques: list = field(default_factory=list)
    synthesis: str = ""
    synthesiser: str = ""
    partial: bool = False
    notes: list = field(default_factory=list)

    def note(self, msg: str):
        self.partial = True
        self.notes.append(msg)

    def survived(self) -> list:
        return [c for c in self.critiques if c.survived()]


# ---------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------

def _call(tr, runner, ex, prompt, rnd, role, author, subject, cwd, timeout, log):
    log("  round %d  %-9s %s" % (rnd, role, author))
    res = runner(ex, prompt, cwd=cwd, timeout=timeout)
    t = Turn(rnd, role, author, subject, bool(res.get("ok")), res.get("ms", 0.0),
             unfence(res.get("stdout", "")), res.get("reason", ""), prompt)
    if t.ok and not t.text.strip():
        t.ok, t.why = False, "empty reply"
    tr.turns.append(t)
    if not t.ok:
        log("           failed: %s" % (t.why or "no output"))
    return t


def run_duo(pairing, brief: str, rounds: int = 4, timeout: int = ROUND_TIMEOUT,
            cwd: str = None, runner=None, log=None) -> Transcript:
    """Drive the protocol. Returns what happened, partial or whole; never raises."""
    runner = runner or executors.run
    log = log or (lambda s: None)
    names = [p.name for p in pairing.participants]
    ex = {p.name: p.executor for p in pairing.participants}
    tr = Transcript(brief=brief, participants=names, rounds_requested=rounds)

    # --- round 1: blind drafts -------------------------------------------
    for n in names:
        t = _call(tr, runner, ex[n], fill(DRAFT_PROMPT, BRIEF=brief),
                  1, "draft", n, n, cwd, timeout, log)
        if t.ok:
            tr.drafts[n] = t.text
    if len(tr.drafts) < 2:
        missing = [n for n in names if n not in tr.drafts]
        tr.note("round 1 produced no draft from %s — the protocol cannot continue "
                "without two independent drafts" % ", ".join(missing))
        return tr
    tr.rounds_completed = 1
    if rounds < 2:
        return tr

    # --- round 2: blind critique of the other's draft ---------------------
    other = {names[0]: names[1], names[1]: names[0]}
    for n in names:
        subj = other[n]
        anon = deattribute(tr.drafts[subj], brief)
        t = _call(tr, runner, ex[n], fill(CRITIQUE_PROMPT, BRIEF=brief, DRAFT=anon),
                  2, "critique", n, subj, cwd, timeout, log)
        if t.ok:
            tr.critiques.extend(parse_critique(t.text, by=n, against=subj))
        else:
            tr.note("round 2: %s could not critique %s's draft (%s) — that draft "
                    "goes into round 3 unchallenged" % (n, subj, t.why or "no output"))
    tr.rounds_completed = 2
    if rounds < 3:
        return tr

    # --- round 3: answer the critique, then revise ------------------------
    for n in names:
        mine = [c for c in tr.critiques if c.against == n]
        if not mine:
            tr.revised[n] = tr.drafts[n]
            continue
        t = _call(tr, runner, ex[n],
                  fill(CONVERGE_PROMPT, DRAFT=tr.drafts[n], CRITIQUE=_render_points(mine)),
                  3, "converge", n, n, cwd, timeout, log)
        if t.ok:
            answered = apply_ledger(mine, t.text)
            tr.revised[n] = revised_body(t.text, tr.drafts[n])
            if answered < len(mine):
                tr.note("round 3: %s left %d of %d critique points unanswered"
                        % (n, len(mine) - answered, len(mine)))
        else:
            tr.revised[n] = tr.drafts[n]
            tr.note("round 3: %s did not revise (%s) — its round 1 draft is carried "
                    "forward unchanged" % (n, t.why or "no output"))
    tr.rounds_completed = 3
    if rounds < 4:
        return tr

    # --- round 4: synthesis ------------------------------------------------
    # The synthesiser is the second participant, so the model that drafted
    # first is not also the one holding the pen at the end.
    tr.synthesiser = names[1]
    surv = tr.survived()
    t = _call(tr, runner, ex[tr.synthesiser],
              fill(SYNTHESIS_PROMPT, BRIEF=brief,
                   A=deattribute(tr.revised.get(names[0], ""), brief),
                   B=deattribute(tr.revised.get(names[1], ""), brief),
                   DISAGREEMENTS=_render_disagreements(surv)),
              4, "synthesis", tr.synthesiser, "both", cwd, timeout, log)
    if t.ok:
        tr.synthesis = t.text
        tr.rounds_completed = 4
    else:
        tr.note("round 4: %s could not synthesise (%s) — both revised drafts are "
                "in the transcript, but there is no merged spec"
                % (tr.synthesiser, t.why or "no output"))
    return tr


def _render_points(cs: list) -> str:
    out = []
    for c in cs:
        out.append("%d. [%s] %s" % (c.n, c.kind, c.point))
        if c.why:
            out.append("   why: %s" % c.why)
    return "\n".join(out)


def _render_disagreements(cs: list) -> str:
    if not cs:
        return "(none — every critique point was accepted)"
    out = []
    for i, c in enumerate(cs, 1):
        out.append("%d. One designer said: %s" % (i, c.point))
        if c.why:
            out.append("   their reason: %s" % c.why)
        out.append("   the other rejected it: %s" % (c.response or "no reason recorded"))
    return "\n".join(out)
