"""
Did the collaboration actually happen, or did it just look like it did?

This is the module that stops Duo being theatre. Two models can execute all
four rounds perfectly and change nothing: one writes a critique, the other
answers "ACCEPT" six times and re-emits the same draft, and the transcript is
indistinguishable from real work unless somebody measures. So three things get
measured, all of them from the text itself rather than from a model's opinion
of its own behaviour:

    drift        how much of a draft's wording actually changed after critique
    ledger       how many critique points were accepted, rejected, or never
                 answered at all
    survival     how many disagreements were still live at the end, which is
                 the number that has to reach the spec's open questions

A run where nothing moved is a FAILED Duo run, and it says so. That is the
whole reason these numbers are reported next to the document instead of in a
log nobody opens.

    IS      arithmetic over the transcript, and named verdicts derived from it
    IS NOT  a quality score. Nothing here knows whether the spec is any good;
            it knows whether two models engaged. A high drift on a draft that
            got worse still reads as high drift, and no metric here will catch
            that — a human or a downstream gate has to.

Zero third-party dependencies.
"""

from __future__ import annotations

import difflib
import re

# Below this fraction of changed words, a "revision" is a re-print. Two
# percent is roughly one reworded sentence in a 450-word draft: less than that
# and the model did not act on anything it was told.
DRIFT_FLOOR = 0.02

_WORD = re.compile(r"[A-Za-z0-9_./-]+")


def _words(text: str) -> list:
    return _WORD.findall((text or "").lower())


def drift(before: str, after: str) -> float:
    """Fraction of the draft's words the revision moved, added, or removed."""
    a, b = _words(before), _words(after)
    if not a and not b:
        return 0.0
    # autojunk is off: it treats any token occurring in >1% of a long sequence
    # as noise, which for English prose means "the" and "a" stop counting and
    # every diff ratio drifts upward for free.
    return round(1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


def _verdict(received: int, accepted: int, rejected: int, unanswered: int, d: float) -> str:
    if received == 0:
        return "no critique reached it"
    if accepted == 0 and rejected == 0:
        return "ignored every critique — answered none of %d points" % received
    if d < DRIFT_FLOOR and accepted > 0:
        return ("accepted %d point%s and changed %.1f%% of the draft — the accept "
                "is not visible in the text" % (accepted, "" if accepted == 1 else "s", d * 100))
    if accepted == 0:
        return "held its ground — rejected all %d points, with reasons" % rejected
    if rejected == 0 and unanswered == 0:
        return "accepted all %d points and rewrote %.0f%% of the draft" % (received, d * 100)
    return ("accepted %d, rejected %d, rewrote %.0f%% of the draft"
            % (accepted, rejected, d * 100))


def collaboration(tr) -> dict:
    """Every number Duo reports about itself, plus the verdicts that follow."""
    per, flags = {}, []
    for n in tr.participants:
        mine = [c for c in tr.critiques if c.against == n]
        acc = sum(1 for c in mine if c.verdict.upper() == "ACCEPT")
        rej = sum(1 for c in mine if c.verdict.upper() == "REJECT")
        una = len(mine) - acc - rej
        d = drift(tr.drafts.get(n, ""), tr.revised.get(n, tr.drafts.get(n, "")))
        per[n] = {
            "critiques_raised": sum(1 for c in tr.critiques if c.by == n),
            "critiques_received": len(mine),
            "accepted": acc, "rejected": rej, "unanswered": una,
            "drift": d,
            "draft_words": len(_words(tr.drafts.get(n, ""))),
            "revised_words": len(_words(tr.revised.get(n, ""))),
            "verdict": _verdict(len(mine), acc, rej, una, d),
        }
        if len(mine) and acc == 0 and rej == 0:
            flags.append("%s never answered a single critique point" % n)
        elif acc > 0 and d < DRIFT_FLOOR:
            flags.append("%s accepted %d point%s but its draft barely moved (%.1f%%) — "
                         "the acceptance is not in the text"
                         % (n, acc, "" if acc == 1 else "s", d * 100))

    raised = sum(v["critiques_raised"] for v in per.values())
    silent = [n for n, v in per.items() if v["critiques_raised"] == 0]
    if silent:
        flags.append("%s raised no critique at all — one lane of the exchange is missing"
                     % ", ".join(silent))
    if raised and all(v["drift"] < DRIFT_FLOOR for v in per.values()):
        flags.append("neither draft changed after critique — this is a failed Duo run, "
                     "not a converged one")

    surv = tr.survived()
    # Collaboration is a conjunction on purpose. Any one of these alone is
    # satisfied by a model going through the motions.
    ok = (len(tr.participants) == 2
          and all(v["critiques_raised"] > 0 for v in per.values())
          and all(v["accepted"] + v["rejected"] > 0 for v in per.values())
          and any(v["drift"] >= DRIFT_FLOOR for v in per.values()))

    return {
        "per_model": per,
        "critique_points": raised,
        "accepted": sum(v["accepted"] for v in per.values()),
        "rejected": sum(v["rejected"] for v in per.values()),
        "unanswered": sum(v["unanswered"] for v in per.values()),
        "survived": [{"n": c.n, "by": c.by, "against": c.against, "kind": c.kind,
                      "point": c.point, "why": c.why, "rebuttal": c.response}
                     for c in surv],
        "survived_count": len(surv),
        "drift_floor": DRIFT_FLOOR,
        "collaborated": ok,
        "flags": flags,
    }


def summary(m: dict) -> str:
    """The block that goes in front of the spec and in front of the operator."""
    rows = ["collaboration  %s"
            % ("two models changed each other's work"
               if m["collaborated"] else "NOT DEMONSTRATED — see flags below")]
    for n, v in m["per_model"].items():
        rows.append("  %-9s raised %d · received %d · accepted %d · rejected %d · "
                    "unanswered %d · drift %.1f%%"
                    % (n, v["critiques_raised"], v["critiques_received"], v["accepted"],
                       v["rejected"], v["unanswered"], v["drift"] * 100))
        rows.append("  %-9s %s" % ("", v["verdict"]))
    rows.append("  %-9s %d disagreement%s survived to open questions"
                % ("", m["survived_count"], "" if m["survived_count"] == 1 else "s"))
    for f in m["flags"]:
        rows.append("  flag      %s" % f)
    return "\n".join(rows)
