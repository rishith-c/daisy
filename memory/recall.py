"""
Recall across the boundary — what I know, and what I know I no longer hold.

Three signals are fused, and the order of the weights is the argument:

    exact   a Tier-1 fact whose SUBJECT is literally the thing being asked
            about. This is not similarity, it is identity. "Did I write
            index.html" against a `write` fact with subject `index.html` is a
            lookup, and treating it as a nearest-neighbour problem is how
            retrieval systems end up 0.34-confident about facts they hold
            exactly.

    cover   deterministic verification state — the gate names the caller is
            currently failing, matched against the gate verdicts on file. This
            is precedent/engine.py's argument reused: the sparse half of the
            score should be something the factory proved, not something it
            phrased. An agent that just watched taste.t1 go red is asking a far
            more specific question than its prose suggests.

    cos     the 512-d signed-hash vector, binary-quantised, Hamming-shortlisted
            and exactly rescored. Weakest leg, and it is meant to be: it is the
            only one that can be confidently wrong.

They are combined as ABSOLUTE evidence with a floor, not by reciprocal rank
fusion. precedent/engine.py fuses by rank because it is choosing between
candidates; here the question is "do I actually hold this at all", which rank
cannot answer — normalise a set of bad candidates and the least-bad one still
comes out at 1.0. So the blend follows commons/store.py: a deterministic half
carrying 0.62, a dense half carrying 0.38, and below EVIDENCE_FLOOR the honest
answer is nothing.

And then the part that is not retrieval at all. `forgotten()` searches Tier 3 —
the residue of compaction — and answers in the only form that is not a lie:

    compacted 2026-08-22 14:02 · reason squeezed · original event a41f09c2

never a reconstruction. `recall()` runs both and returns them separately,
because "I know X" and "I dropped something about X" are different claims and
collapsing them is the failure this package exists to prevent.

What this deliberately does NOT do:

  * No recency decay. precedent half-lives its cases because a stale fix is
    weak advice; a memory is not advice, and an old approval is exactly as
    binding as a new one. Recency is used only to break ties.
  * No query expansion, no synonyms, no LLM rewriting. Every score here is
    reproducible offline with no model call.
  * No cross-encoder rerank. The exact leg already dominates when it fires, and
    a reranker would be a second opinion with no ground truth behind it.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precedent.engine import embed, pack_bits, hamming, cosine, BYTES, DIM  # noqa: E402
from . import store  # noqa: E402

# Below this, nothing is returned. Same value as the commons, for the same
# reason: a memory that always answers is a memory that invents.
EVIDENCE_FLOOR = 0.20

# Tier 3 is judged more leniently, on purpose. A residue claim is a 96-character
# stub, so its vector is thin and it will never score like a full memory. The
# asymmetry is deliberate: a false positive here costs one pointer read, and a
# false negative costs a decision made on a hole.
RESIDUE_FLOOR = 0.12

# Wide enough that quantisation error cannot push a true match out, narrow
# enough that the exact rescore stays cheap. Same shape as commons.
SHORTLIST = 96

_WS = re.compile(r"\s+")
_PATHY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]*\.[A-Za-z0-9]{1,6}")
_DOTTED = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_QUOTED = re.compile(r"[\"'“‘]([^\"'”’]{3,80})[\"'”’]")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


def tokens(query: str) -> list:
    """The parts of a query that could match something exactly.

    Paths, dotted identifiers and quoted spans. Bare words are left out on
    purpose — an "exact" leg that fires on the word `the` is just a bad lexical
    scorer wearing the wrong weight.
    """
    q = query or ""
    out = set()
    for m in _PATHY.findall(q):
        out.add(_norm(m))
    for m in _DOTTED.findall(q.lower()):
        out.add(_norm(m))
    for m in _QUOTED.findall(q):
        out.add(_norm(m))
    return sorted(t for t in out if t)


@dataclass
class Recollection:
    """One thing the store can say about a query."""
    tier: int
    id: str
    kind: str
    run_id: str
    ts: float
    subject: str
    value: str
    score: float
    parts: dict = field(default_factory=dict)
    event_id: str = ""          # Tier-0 pointer; always live for tier 3
    dropped_at: float = 0.0
    reason: str = ""

    def held(self) -> bool:
        return self.tier != 3

    def line(self) -> str:
        if self.tier == 3:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.dropped_at))
            return ("FORGOTTEN  compacted %s · %s · original event %s\n           “%s”"
                    % (when, self.reason, self.event_id, self.subject))
        return "T%d %-11s %-44s %s" % (self.tier, self.kind, self.subject[:44], self.value[:40])


@dataclass
class Recall:
    """Both halves of the answer, never merged."""
    query: str
    held: list = field(default_factory=list)
    forgotten: list = field(default_factory=list)
    ms: float = 0.0
    scanned: int = 0

    def empty(self) -> bool:
        return not self.held and not self.forgotten


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _exact(toks: list, subject: str, text: str = "") -> float:
    """How literally does this row answer the question?

    Full equality is proof. A basename match is nearly proof — a query saying
    `bracket.py` about a fact recorded as `hardware/bracket.py` is the same
    question. Containment is weaker but still not similarity. Anything else is
    left to the vector leg.
    """
    subj, body = _norm(subject), _norm(text)
    best = 0.0
    for t in toks:
        if not t:
            continue
        if t == subj:
            return 1.0
        if subj and t.rsplit("/", 1)[-1] == subj.rsplit("/", 1)[-1]:
            best = max(best, 0.85)
        elif subj and (t in subj or subj in t):
            best = max(best, 0.60)
        elif body and t in body:
            best = max(best, 0.45)
    return best


def _cover(want: set, subject: str, kind: str, text: str = "") -> float:
    """Containment over gate names, not Jaccard.

    commons/store.py makes this argument and it holds here: dividing by the
    union penalises a run for having checked more gates than the caller asked
    about, which is backwards when the gates are the evidence.
    """
    if not want:
        return 0.0
    subj = _norm(subject)
    if kind == "gate" and subj in want:
        return 1.0
    hay = subj + " " + _norm(text)
    hit = sum(1 for g in want if g in hay)
    return min(1.0, hit / len(want)) * (1.0 if kind == "gate" else 0.7)


def _evidence(exact: float, cover: float, cos: float) -> float:
    """Absolute evidence, never normalised rank.

    When any deterministic signal fired, it carries 0.62 and the vector carries
    0.38. When none did, the vector is judged on its own scale rather than being
    scaled down into the floor — otherwise a genuine paraphrase match, which on
    a signed-hash embedding tops out around 0.5, could never clear the bar and
    the dense tier would be decorative.
    """
    struct = 0.60 * exact + 0.40 * cover
    return 0.62 * struct + 0.38 * cos if struct > 0 else cos


# ---------------------------------------------------------------------------
# the query
# ---------------------------------------------------------------------------

def _search(con, table, cols, qvec, qbits, toks, want, floor, subject_of, text_of):
    """Two phases, the same shape precedent/engine.py uses.

    Phase one never loads a float blob: the whole tier is scanned as 64-byte
    binary vectors, which is what makes scanning the whole tier affordable.
    Phase two loads floats for the shortlist only, and rescores exactly —
    quantisation reorders near-neighbours and the rescore repairs it.

    Rows carrying a deterministic signal bypass the shortlist entirely. That is
    not an optimisation, it is the point: a fact whose prose has nothing in
    common with the query can still be the exact thing being asked for, and a
    Hamming cutoff would throw it away before the exact leg ever ran.

    A row whose blob is the wrong width is skipped rather than scored. `cosine`
    zips, so a short blob would silently truncate the dot product and hand back
    a plausible number computed from a corrupt row. One bad row must not sink
    the query, and it must not quietly win it either.
    """
    rows = con.execute("SELECT %s, bits FROM %s" % (", ".join(cols), table)).fetchall()
    scored, forced = [], {}
    for r in rows:
        b = r["bits"]
        if not isinstance(b, (bytes, bytearray)) or len(b) != BYTES:
            continue
        kind = r["kind"] if "kind" in r.keys() else ""
        subj, body = subject_of(r), text_of(r)
        ex = _exact(toks, subj, body)
        cv = _cover(want, subj, kind, body)
        if ex > 0 or cv > 0:
            forced[r["id"]] = (r, ex, cv)
        scored.append((hamming(qbits, bytes(b)), r, ex, cv))

    scored.sort(key=lambda t: t[0])
    pool = {r["id"]: (r, ex, cv) for _, r, ex, cv in scored[:SHORTLIST]}
    pool.update(forced)
    if not pool:
        return []

    ids = list(pool)
    floats = {}
    for chunk in [ids[i:i + 400] for i in range(0, len(ids), 400)]:
        q = ",".join("?" * len(chunk))
        for vr in con.execute("SELECT id, vec FROM %s WHERE id IN (%s)" % (table, q), chunk):
            blob = vr["vec"]
            if isinstance(blob, (bytes, bytearray)) and len(blob) == DIM * 4:
                floats[vr["id"]] = store.unpack_f32(bytes(blob))

    out = []
    for rid, (r, ex, cv) in pool.items():
        v = floats.get(rid)
        if v is None:
            continue
        cos = max(0.0, cosine(qvec, v))
        ev = _evidence(ex, cv, cos)
        if ev < floor:
            continue
        out.append((ev, {"exact": round(ex, 3), "cover": round(cv, 3),
                         "cosine": round(cos, 3), "evidence": round(ev, 3)}, r))
    out.sort(key=lambda t: (-t[0], -t[2]["ts"]))
    return out


def recall(con, query: str, gates=None, k: int = 5,
           floor: float = EVIDENCE_FLOOR) -> Recall:
    """What do I know about this — and what did I drop about it?

    Both halves come back separately. A caller that only reads `.held` gets an
    ordinary memory system; a caller that reads `.forgotten` gets the thing this
    package is for.
    """
    t0 = time.time()
    toks = tokens(query)
    want = set(_norm(g) for g in (gates or []) if g)
    # Gate names written into the prose count as gates: an agent typing
    # "taste.t1 keeps failing" has supplied a signature whether it meant to or not.
    want |= set(t for t in toks if "." in t and "/" not in t)
    qvec = embed(query or " ")
    qbits = pack_bits(qvec)

    held = []
    for ev, parts, r in _search(
            con, "fact", ["id", "event_id", "run_id", "ts", "kind", "subject", "value", "detail"],
            qvec, qbits, toks, want, floor,
            lambda r: r["subject"], lambda r: r["value"] + " " + r["detail"]):
        held.append(Recollection(1, r["id"], r["kind"], r["run_id"], r["ts"],
                                 r["subject"], r["value"], round(ev, 4), parts,
                                 event_id=r["event_id"]))

    for ev, parts, r in _search(
            con, "summary", ["id", "run_id", "ts", "prose", "probe_score"],
            qvec, qbits, toks, want, floor,
            lambda r: r["prose"][:120], lambda r: r["prose"]):
        held.append(Recollection(2, r["id"], "summary", r["run_id"], r["ts"],
                                 r["prose"][:160], "probe %.0f%%" % (100 * r["probe_score"]),
                                 round(ev, 4), parts))

    held.sort(key=lambda h: (-h.score, -h.ts))
    scanned = _count(con, "fact") + _count(con, "summary")
    out = Recall(query=query, held=held[:k], scanned=scanned)
    res = forgotten(con, query, gates=gates, k=k)
    out.forgotten = res.forgotten
    out.scanned += res.scanned
    out.ms = round((time.time() - t0) * 1000, 2)
    return out


def forgotten(con, query: str, gates=None, k: int = 5,
              floor: float = RESIDUE_FLOOR) -> Recall:
    """Tier 3 only: what did I compact away that bears on this?

    The answer is a pointer and a timestamp, never a reconstruction. Every hit
    is checked against Tier 0 before it is returned — a residue row whose
    pointer does not resolve is a broken promise, and reporting it as a
    recoverable memory would be worse than not recording it at all.
    """
    t0 = time.time()
    toks = tokens(query)
    want = set(_norm(g) for g in (gates or []) if g)
    want |= set(t for t in toks if "." in t and "/" not in t)
    qvec = embed(query or " ")
    qbits = pack_bits(qvec)

    hits = []
    for ev, parts, r in _search(
            con, "residue", ["id", "event_id", "run_id", "ts", "dropped_at", "kind",
                             "claim", "reason"],
            qvec, qbits, toks, want, floor,
            lambda r: r["claim"], lambda r: r["claim"]):
        if not con.execute("SELECT 1 FROM event WHERE id = ?", (r["event_id"],)).fetchone():
            continue
        hits.append(Recollection(3, r["id"], r["kind"] or "prose", r["run_id"], r["ts"],
                                 r["claim"], "", round(ev, 4), parts,
                                 event_id=r["event_id"], dropped_at=r["dropped_at"],
                                 reason=r["reason"]))
    hits.sort(key=lambda h: (-h.score, -h.dropped_at))
    return Recall(query=query, forgotten=hits[:k], scanned=_count(con, "residue"),
                  ms=round((time.time() - t0) * 1000, 2))


def _count(con, table: str) -> int:
    return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def verbatim(con, rec: Recollection) -> dict:
    """Follow a recollection's pointer back to the original.

    This is the second half of a Tier-3 answer. `forgotten()` says what is
    missing and where it went; this goes and gets it, from the append-only log
    that was never compacted.
    """
    return store.event(con, rec.event_id) if rec.event_id else {}
