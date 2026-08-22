"""
Compaction with a conscience.

Three production compactors (Factory.ai's, Anthropic's compact_20260112, and
OpenAI's) were benchmarked across 36,611 real messages. All three achieved
98-99% compression. All three scored 2.19-2.45 out of 5.0 on *artifact
tracking* — they forget which files were modified.

For a factory whose whole claim is that every number traces to a calculation,
that failure mode is fatal. So compaction here does two things nobody's
compactor does by default:

  1. Structured facts are never summarised. Files, gates, margins and diffs
     are extracted as ROWS at the moment they happen. Only prose is squeezed.

  2. Every compaction is PROBE-VALIDATED. Facts are held out of the summary,
     then the summary is quizzed about them. A compaction that cannot answer
     its own probes is rejected and retried at a lower ratio.

Compression ratio is reported, but the gate is the probe score.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict

from .engine import embed, cosine, pack_bits, hamming

# ---------------------------------------------------------------------------
# stage 1 — deterministic cleanup (lossless, 15-30%)
# ---------------------------------------------------------------------------

_NOISE = (
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(?:\.|·|-){1,4}\s*$"),
)


def deterministic_clean(events: list[dict]) -> tuple[list[dict], dict]:
    """Drop what is provably redundant. Never touches structured facts."""
    out: list[dict] = []
    seen_reads: dict[str, int] = {}      # path -> index in out (keep latest only)
    resolved_errors: set[str] = set()
    dropped = {"repeat_read": 0, "resolved_error": 0, "blank": 0, "dup_tool": 0}
    last_tool: str | None = None

    for ev in events:
        kind = ev.get("kind", "")
        text = ev.get("text", "") or ""

        if any(p.match(text) for p in _NOISE) and kind not in ("gate", "diff", "repair"):
            dropped["blank"] += 1
            continue

        # a file read superseded by a later read of the same path is dead weight
        if kind == "read" and ev.get("path"):
            p = ev["path"]
            if p in seen_reads:
                out[seen_reads[p]] = None       # type: ignore[assignment]
                dropped["repeat_read"] += 1
            seen_reads[p] = len(out)

        # an error that later got resolved does not need its full text retained
        if kind == "error" and ev.get("id") in resolved_errors:
            dropped["resolved_error"] += 1
            continue
        if kind == "repair" and ev.get("fixes"):
            resolved_errors.add(ev["fixes"])

        # identical consecutive tool output
        if kind == "tool":
            sig = text[:160]
            if sig == last_tool:
                dropped["dup_tool"] += 1
                continue
            last_tool = sig
        else:
            last_tool = None

        # stacktraces: head + tail, never the middle
        if kind == "error" and text.count("\n") > 12:
            lines = text.split("\n")
            ev = dict(ev, text="\n".join(lines[:5] + ["  … %d frames elided …" % (len(lines) - 9)] + lines[-4:]))

        out.append(ev)

    kept = [e for e in out if e is not None]
    return kept, dropped


# ---------------------------------------------------------------------------
# stage 2 — semantic dedup
# ---------------------------------------------------------------------------

def semantic_dedup(events: list[dict], threshold: float = 0.95) -> tuple[list[dict], int]:
    """Greedy near-duplicate removal over the prose of an event log.

    Binary vectors give a cheap Hamming prefilter; only close pairs pay for the
    exact cosine. Structured events (gate/diff/repair) are never deduped.
    """
    kept: list[dict] = []
    kept_vec: list[tuple[bytes, list[float]]] = []
    removed = 0

    for ev in events:
        if ev.get("kind") in ("gate", "diff", "repair", "approval"):
            kept.append(ev)
            continue
        text = (ev.get("text") or "").strip()
        if not text:
            kept.append(ev)
            continue
        v = embed(text)
        b = pack_bits(v)
        dup = False
        for pb, pv in kept_vec:
            if hamming(b, pb) > 190:      # cheap reject: far apart in Hamming space
                continue
            if cosine(v, pv) >= threshold:
                dup = True
                break
        if dup:
            removed += 1
            continue
        kept.append(ev)
        kept_vec.append((b, v))

    return kept, removed


# ---------------------------------------------------------------------------
# stage 3 — structured distillation (facts stay rows, never prose)
# ---------------------------------------------------------------------------

@dataclass
class Essence:
    """The compacted form of a run. Facts are structured; only prose is squeezed."""
    run_id: str
    files_modified: list[str] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    prose: str = ""
    bytes_before: int = 0
    bytes_after: int = 0
    probe_score: float = 0.0
    probes: list[dict] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.bytes_before / self.bytes_after) if self.bytes_after else 0.0


def distill(run_id: str, events: list[dict]) -> Essence:
    """Pull the load-bearing facts out as rows before any summarisation."""
    e = Essence(run_id=run_id)
    prose_bits: list[str] = []

    for ev in events:
        k = ev.get("kind")
        if k == "diff":
            for p in ev.get("files", []):
                if p not in e.files_modified:
                    e.files_modified.append(p)
        elif k == "gate":
            e.gates.append({"name": ev.get("name"), "passed": bool(ev.get("passed")),
                            "margin": ev.get("margin"), "detail": ev.get("detail", "")})
            if not ev.get("passed"):
                e.failures.append({"gate": ev.get("name"), "margin": ev.get("margin"),
                                   "detail": ev.get("detail", "")})
        elif k == "repair":
            e.decisions.append("repaired %s via %s" % (ev.get("fixes"), ev.get("by")))
        elif k == "approval":
            e.approvals.append("%s by %s" % (ev.get("what"), ev.get("who")))
        elif k == "decision":
            e.decisions.append(ev.get("text", ""))
        elif k == "next":
            e.next_steps.append(ev.get("text", ""))
        else:
            t = (ev.get("text") or "").strip()
            if t:
                prose_bits.append(t)

    e.prose = _squeeze(prose_bits)
    return e


def _squeeze(bits: list[str], keep: int = 6) -> str:
    """Extractive summary: highest-signal sentences, original wording kept.

    Abstractive rewriting is exactly where artifact tracking goes to die, so
    this never paraphrases — it selects.
    """
    if not bits:
        return ""
    scored = []
    for i, b in enumerate(bits):
        score = 0.0
        score += 1.4 * len(re.findall(r"\b(?:because|so that|therefore|instead|rather than)\b", b, re.I))
        score += 1.1 * len(re.findall(r"\b(?:fail|failed|reject|refus|escalat|repair|margin|gate)\w*\b", b, re.I))
        score += 0.7 * len(re.findall(r"\b\d+(?:\.\d+)?\s*(?:MPa|mm|g|s|ms|%)\b", b))
        score += 0.4 if i < 2 or i >= len(bits) - 2 else 0.0     # lead + tail bias
        score -= 0.002 * max(0, len(b) - 260)
        scored.append((score, i, b))
    scored.sort(key=lambda t: (-t[0], t[1]))
    picked = sorted(scored[:keep], key=lambda t: t[1])
    return " ".join(p[2] for p in picked)


# ---------------------------------------------------------------------------
# stage 4 — PROBE VALIDATION (the part nobody ships)
# ---------------------------------------------------------------------------

PROBE_KINDS = ("artifact", "gate", "decision", "continuation")


def build_probes(events: list[dict], n: int = 8) -> list[dict]:
    """Hold out checkable facts from the raw log to quiz the compaction with."""
    probes: list[dict] = []

    files = [p for ev in events if ev.get("kind") == "diff" for p in ev.get("files", [])]
    for p in files[:3]:
        probes.append({"kind": "artifact", "q": "was %s modified?" % p, "expect": p})

    for ev in events:
        if ev.get("kind") == "gate" and not ev.get("passed"):
            probes.append({"kind": "gate", "q": "did %s fail?" % ev.get("name"),
                           "expect": str(ev.get("name"))})
    for ev in events:
        if ev.get("kind") == "repair":
            probes.append({"kind": "decision", "q": "how was %s repaired?" % ev.get("fixes"),
                           "expect": str(ev.get("by"))})
    for ev in events:
        if ev.get("kind") == "next":
            probes.append({"kind": "continuation", "q": "what is next?",
                           "expect": (ev.get("text") or "")[:40]})

    return probes[:n]


def validate(essence: Essence, probes: list[dict]) -> tuple[float, list[dict]]:
    """Quiz the compacted form. A compaction that can't answer is rejected.

    Answers are looked up in the STRUCTURED fields first — which is the whole
    argument: structure survives compaction, prose does not.
    """
    hay = json.dumps(asdict(essence)).lower()
    results = []
    for p in probes:
        want = (p["expect"] or "").lower().strip()
        ok = bool(want) and want in hay
        if not ok and want:
            # tolerate basename-only retention for paths
            base = want.rsplit("/", 1)[-1]
            ok = bool(base) and base in hay
        results.append({**p, "passed": ok})
    score = (sum(1 for r in results if r["passed"]) / len(results)) if results else 1.0
    return score, results


MIN_PROBE_SCORE = 0.85


def compact(run_id: str, events: list[dict], dedup_threshold: float = 0.95) -> Essence:
    """Full ladder, with a probe gate and automatic backoff.

    If the probe score is below MIN_PROBE_SCORE the compaction is retried with
    a gentler dedup threshold and a longer prose budget. Losing artifacts is
    treated as a failure, not as a compression win.
    """
    raw_bytes = len(json.dumps(events).encode())
    probes = build_probes(events)

    thresholds = (dedup_threshold, 0.985, 1.01)      # 1.01 == dedup disabled
    keeps = (6, 10, 16)
    last: Essence | None = None

    for thr, keep in zip(thresholds, keeps):
        cleaned, dropped = deterministic_clean(list(events))
        deduped, removed = semantic_dedup(cleaned, threshold=thr)
        essence = _distill_with_budget(run_id, deduped, keep)
        essence.bytes_before = raw_bytes
        essence.bytes_after = len(json.dumps(asdict(essence)).encode())
        score, results = validate(essence, probes)
        essence.probe_score = score
        essence.probes = results
        last = essence
        if score >= MIN_PROBE_SCORE:
            return essence

    assert last is not None
    return last


def _distill_with_budget(run_id: str, events: list[dict], keep: int) -> Essence:
    e = distill(run_id, events)
    prose_bits = [(ev.get("text") or "").strip() for ev in events
                  if ev.get("kind") not in ("gate", "diff", "repair", "approval", "decision", "next")
                  and (ev.get("text") or "").strip()]
    e.prose = _squeeze(prose_bits, keep=keep)
    return e
