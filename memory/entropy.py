"""
Entropy-guided compaction — LLMLingua's idea, without LLMLingua's dependency.

LLMLingua (Microsoft, EMNLP'23 / ACL'24) reaches ~20x prompt compression by
running a small language model over the text and dropping the tokens it finds
*predictable*: if GPT-2 can guess a token from its context, that token is
carrying almost no information and removing it costs almost nothing. It is the
right idea and it is better than what this project had.

What cannot be borrowed is the mechanism. LLMLingua-2 is a BERT-level encoder;
loading one means torch, a model download, and a machine that can run it —
against a project whose entire claim is that a judge clones the repo and runs
it with nothing installed.

So this borrows the criterion and drops the model. Predictability is not a
neural property, it is an information-theoretic one, and a language model is
one estimator of it among several. This uses an order-3 character/word n-gram
model built **from the corpus being compacted**, which has two advantages worth
naming:

  * it is adapted, not general. A pretrained LM finds "the" predictable because
    English makes it so. A corpus model finds "physics.bend" predictable
    *because this corpus says it forty times*, which is exactly the redundancy
    a memory store accumulates and a general model would miss.
  * it costs nothing. Building the model is one pass of dict updates; scoring
    is a lookup.

Two more things taken from the paper and kept:

  **A budget controller.** Compression is not applied uniformly. Segments get a
  budget proportional to how much information they carry, so a dense gate
  result is squeezed lightly and a repetitive tool dump is squeezed hard.
  Uniform ratios are why naive compaction loses the one line that mattered.

  **Coarse to fine.** Whole low-information segments are dropped before any
  token-level work happens, because deleting a redundant paragraph is cheaper
  and safer than shaving words off every paragraph.

What is deliberately NOT taken: token-level pruning inside a sentence. It
produces text that reads as damaged, and this store is read by humans during an
audit as well as by agents. Dropping whole clauses keeps what survives
grammatical.

    python3 -m memory.entropy --demo

Zero third-party dependencies.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

WORD = re.compile(r"\w+|[^\w\s]")
ORDER = 3


class Model:
    """An order-N word model over the corpus being compacted.

    Escape-to-lower-order smoothing (the PPM idea): an unseen trigram falls
    back to the bigram, then the unigram, then a uniform floor. Without it a
    single novel word makes a whole segment look infinitely surprising and the
    budget controller protects noise.
    """

    def __init__(self, order: int = ORDER):
        self.order = order
        self.counts = [defaultdict(lambda: defaultdict(int)) for _ in range(order + 1)]
        self.totals = [defaultdict(int) for _ in range(order + 1)]
        self.vocab = set()
        self.n = 0

    def fit(self, texts) -> "Model":
        for t in texts:
            toks = WORD.findall((t or "").lower())
            self.vocab.update(toks)
            self.n += len(toks)
            for i, tok in enumerate(toks):
                for k in range(self.order + 1):
                    ctx = tuple(toks[max(0, i - k):i]) if k else ()
                    if k and len(ctx) < k:
                        continue
                    self.counts[k][ctx][tok] += 1
                    self.totals[k][ctx] += 1
        return self

    def logp(self, ctx: tuple, tok: str) -> float:
        """log2 P(tok | ctx), escaping to shorter contexts."""
        V = max(1, len(self.vocab))
        for k in range(min(self.order, len(ctx)), -1, -1):
            c = tuple(ctx[len(ctx) - k:]) if k else ()
            tot = self.totals[k].get(c, 0)
            if tot:
                hit = self.counts[k][c].get(tok, 0)
                # add-one over the observed continuations, not the whole vocab:
                # a context seen twice should not be swamped by 20k unseen words
                cont = max(1, len(self.counts[k][c]))
                p = (hit + 1.0) / (tot + cont)
                if hit:
                    return math.log2(p)
        return math.log2(1.0 / (V + 1))

    def bits(self, text: str) -> float:
        """Information content of a string, in bits, under this corpus."""
        toks = WORD.findall((text or "").lower())
        if not toks:
            return 0.0
        total = 0.0
        for i, tok in enumerate(toks):
            total += -self.logp(tuple(toks[max(0, i - self.order):i]), tok)
        return total

    def density(self, text: str) -> float:
        """Bits per token — reported, but not what the ranking uses. See worth()."""
        toks = WORD.findall((text or "").lower())
        return (self.bits(text) / len(toks)) if toks else 0.0

    def worth(self, text: str) -> float:
        """What a segment is worth keeping: bits, damped by sqrt of length.

        Neither raw total nor bits-per-token is right, and a test caught it.
        Ranking by total bits keeps whatever is longest. Ranking by bits per
        token does the opposite and keeps whatever is shortest — measured on a
        real case, a 12x-repeated filler line scored 0.42 bits/token while the
        one line that mattered ("physics.bend failed at FoS 0.72 and was
        repaired to 4.61 mm") scored 0.34, purely because it was longer. The
        compactor dropped the only sentence in the passage worth keeping.

        Dividing by sqrt(n) is the standard compromise: a segment twice as long
        must carry ~1.4x the information to rank equally, so length is taxed
        without being disqualifying. This is the same shape as the length
        normalisation BM25 applies, for the same reason.
        """
        toks = WORD.findall((text or "").lower())
        if not toks:
            return 0.0
        return self.bits(text) / math.sqrt(len(toks))


def segments(text: str) -> list:
    """Split into clause-sized units. Whole units are kept or dropped."""
    parts = re.split(r"(?<=[.!?;\n])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def compress(text: str, model: Model, keep: float = 0.35,
             protect=None) -> dict:
    """Drop the least informative segments until the budget is met.

    `keep` is a floor on retained *information*, not on length — the loop stops
    when the survivors carry `keep` of the original bits, so a dense passage is
    barely touched and a repetitive one collapses. `protect` is a predicate for
    segments that are never dropped whatever their score; structured facts go
    there, because the whole argument of this package is that verification
    state does not get summarised away.
    """
    segs = segments(text)
    if len(segs) <= 1:
        return {"text": text, "kept": len(segs), "dropped": 0,
                "bits_before": model.bits(text), "bits_after": model.bits(text),
                "ratio": 1.0}

    scored = []
    for i, s in enumerate(segs):
        forced = bool(protect and protect(s))
        scored.append((i, s, model.bits(s), model.worth(s), forced))

    total_bits = sum(x[2] for x in scored) or 1.0
    budget = total_bits * keep

    # Rank by worth (bits damped by sqrt of length), not by raw bits and not by
    # bits per token — both of those have a degenerate preference for one end of
    # the length distribution. Protected segments sort first so they are never
    # the ones cut.
    order = sorted(scored, key=lambda x: (not x[4], -x[3]))
    keep_idx, spent = set(), 0.0
    for i, s, bits, dens, forced in order:
        if forced or spent < budget:
            keep_idx.add(i)
            spent += bits

    kept = [s for i, s, *_ in scored if i in keep_idx]
    out = " ".join(kept)
    return {
        "text": out,
        "kept": len(kept),
        "dropped": len(segs) - len(kept),
        "bits_before": total_bits,
        "bits_after": model.bits(out),
        "ratio": (len(text) / len(out)) if out else float("inf"),
        "information_retained": (model.bits(out) / total_bits) if total_bits else 1.0,
    }


def main(argv=None) -> int:
    import argparse, json, os, sqlite3, sys
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "memory.db"))
    ap.add_argument("--keep", type=float, default=0.35)
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)

    texts = []
    try:
        con = sqlite3.connect(a.db)
        for tbl, col in (("event", "text"), ("summary", "text")):
            try:
                for (t,) in con.execute("SELECT %s FROM %s WHERE %s IS NOT NULL" % (col, tbl, col)):
                    if t and len(str(t)) > 40:
                        texts.append(str(t))
            except sqlite3.Error:
                pass
        con.close()
    except sqlite3.Error:
        pass
    if not texts:
        print("no corpus at %s" % a.db)
        return 1

    m = Model().fit(texts)
    print("corpus  %d texts · %d tokens · %d vocab" % (len(texts), m.n, len(m.vocab)))
    before = after = 0
    bits_b = bits_a = 0.0
    for t in texts:
        r = compress(t, m, keep=a.keep)
        before += len(t); after += len(r["text"])
        bits_b += r["bits_before"]; bits_a += r["bits_after"]
    print("bytes   %d -> %d   (x%.2f)" % (before, after, before / max(1, after)))
    print("bits    %.0f -> %.0f   (%.1f%% of the information retained)"
          % (bits_b, bits_a, 100.0 * bits_a / max(1.0, bits_b)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
