"""Tests for entropy-guided compaction.

    python3 -m memory.test_entropy

Corpora are built in the test. Nothing reads the developer's real memory store
and nothing touches the network.
"""

from __future__ import annotations

import re

from .entropy import Model, compress, segments, WORD

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


GATEY = re.compile(r"(gate|pass|fail|margin|approv|decision|repair)", re.I)
prot = lambda s: bool(GATEY.search(s))


def test_model_learns_the_corpus():
    print("\nmodel — adapted to this corpus, not to English")
    repeated = ["the scraper returned fewer keys per row"] * 40
    novel = ["a wombat recalibrated the interferometer"]
    m = Model().fit(repeated + novel)
    check("vocabulary is collected", len(m.vocab) > 5, str(len(m.vocab)))
    check("token count is tracked", m.n > 100, str(m.n))

    rep_d = m.density(repeated[0])
    nov_d = m.density(novel[0])
    check("a phrase this corpus repeats is cheap", rep_d < nov_d,
          "%.2f vs %.2f bits/token" % (rep_d, nov_d))
    # An absolute bits/token threshold is not meaningful — it moves with vocab
    # size and corpus length. The relationship is what the compactor depends on.
    check("an unseen phrase costs several times more per token",
          nov_d > rep_d * 3, "%.2f vs %.2f" % (nov_d, rep_d))
    check("empty text costs nothing", m.bits("") == 0.0)
    check("density of empty text is zero, not a crash", m.density("") == 0.0)


def test_escape_to_lower_order():
    print("\nsmoothing — one novel word must not blow up a segment")
    m = Model().fit(["physics bend gate failed at margin zero point seven"] * 12)
    known = m.density("physics bend gate failed")
    mixed = m.density("physics bend gate failed zzzqqq")
    check("a novel token raises the cost", mixed > known, "%.2f vs %.2f" % (mixed, known))
    check("but does not make it infinite", mixed < known * 6,
          "%.2f vs %.2f" % (mixed, known))


def test_segments():
    print("\nsegmentation — clause-sized units")
    s = segments("First thing. Second thing! Third?\nFourth thing")
    check("splits on sentence ends and newlines", len(s) == 4, str(s))
    check("no empty segments", all(x.strip() for x in s))
    check("empty input gives no segments", segments("") == [])
    check("a single clause stays whole", len(segments("just one clause")) == 1)


def test_compression_drops_the_predictable():
    print("\ncompression — the redundant goes first")
    filler = "the scraper returned fewer keys per row. " * 12
    signal = "physics.bend failed at FoS 0.72 and was repaired to 4.61 mm."
    text = filler + signal
    m = Model().fit([text])
    r = compress(text, m, keep=0.35)
    check("something was dropped", r["dropped"] > 0, str(r["dropped"]))
    check("output is shorter", len(r["text"]) < len(text))
    check("ratio is reported", r["ratio"] > 1.0, "%.2f" % r["ratio"])
    check("the distinctive line survives", "0.72" in r["text"] or "4.61" in r["text"],
          r["text"][:90])
    check("information retained is reported", 0.0 <= r["information_retained"] <= 1.05,
          str(r.get("information_retained")))


def test_protected_segments_are_never_dropped():
    print("\nprotection — verification state is not summarised away")
    facts = ["gate physics.bend failed at margin 0.72.",
             "approval granted by rishith.",
             "repair set web_thickness to 4.61 mm."]
    noise = ["reading file %d of many, nothing notable happened here." % i for i in range(30)]
    text = " ".join(noise[:15] + facts + noise[15:])
    m = Model().fit([text])
    for keep in (0.05, 0.15, 0.35, 0.9):
        r = compress(text, m, keep=keep, protect=prot)
        survived = sum(1 for f in facts if f in r["text"])
        check("all facts survive at keep=%.2f" % keep, survived == len(facts),
              "%d/%d" % (survived, len(facts)))
    r = compress(text, m, keep=0.05, protect=prot)
    check("and noise is still cut hard", r["dropped"] > 10, str(r["dropped"]))


def test_budget_is_information_not_length():
    print("\nbudget — length is not information")
    m = Model().fit(["alpha beta gamma delta epsilon zeta eta theta"] * 20)
    long_dull = "alpha beta gamma delta. " * 20
    short_rich = "quixotic femtosecond bathysphere."
    check("a long repetitive passage is less dense than a short novel one",
          m.density(long_dull) < m.density(short_rich),
          "%.2f vs %.2f" % (m.density(long_dull), m.density(short_rich)))

    mixed = long_dull + " " + short_rich
    r = compress(mixed, Model().fit([mixed]), keep=0.4)
    check("the rare clause is the one kept",
          "quixotic" in r["text"] or "bathysphere" in r["text"], r["text"][-70:])


def test_edges():
    print("\ncompression — degenerate inputs")
    m = Model().fit(["one two three four five six"])
    r = compress("", m)
    check("empty text is a no-op", r["text"] == "" and r["dropped"] == 0)
    r2 = compress("only one clause here", m)
    check("a single clause is never dropped", r2["kept"] == 1 and r2["dropped"] == 0)
    check("ratio of a no-op is 1.0", abs(r2["ratio"] - 1.0) < 1e-9, str(r2["ratio"]))
    r3 = compress("a. b. c. d.", m, keep=1.0)
    check("keep=1.0 retains everything", r3["dropped"] == 0, str(r3["dropped"]))
    big = "unicode ✓ émoji-free but accented. " * 40
    r4 = compress(big, Model().fit([big]), keep=0.3)
    check("unicode survives round-trip", "✓" in r4["text"] or r4["kept"] > 0)


def test_deterministic():
    print("\ndeterminism — same corpus, same result")
    txt = "alpha. beta gamma. delta epsilon zeta. eta. " * 6
    m1, m2 = Model().fit([txt]), Model().fit([txt])
    check("identical densities", abs(m1.density(txt) - m2.density(txt)) < 1e-9)
    a = compress(txt, m1, keep=0.4)["text"]
    b = compress(txt, m2, keep=0.4)["text"]
    check("identical output", a == b)


def test_stacks_on_a_summary():
    print("\nstacking — entropy on top of an already-compacted summary")
    # A summary is already dense; the point is that it is not yet minimal.
    summary = (" ".join("run %d completed with no findings." % i for i in range(24))
               + " gate physics.bend failed at 0.72 and was repaired to 4.61 mm.")
    m = Model().fit([summary])
    r = compress(summary, m, keep=0.35, protect=prot)
    check("a summary still compresses", r["ratio"] > 1.3, "%.2fx" % r["ratio"])
    check("and the gate line is what survives", "0.72" in r["text"] or "physics.bend" in r["text"],
          r["text"][:90])


def main():
    print("entropy-guided compaction — test suite")
    test_model_learns_the_corpus()
    test_escape_to_lower_order()
    test_segments()
    test_compression_drops_the_predictable()
    test_protected_segments_are_never_dropped()
    test_budget_is_information_not_length()
    test_edges()
    test_deterministic()
    test_stacks_on_a_summary()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
