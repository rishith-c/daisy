"""
Failure, repair and escalation as first-class signals — spans, not log lines.

The distinction is not cosmetic. A log line saying "physics gate failed" is a
string somebody has to grep for and cannot aggregate. A span carries the same
sentence plus the parent it failed under, the exact duration, the margin that
was short, and a counter you can graph — and it lands in the same trace as the
repair that followed it, so the whole failure/repair loop is one clickable
tree instead of four unrelated lines an hour apart.

Three kinds, and the list is closed on purpose:

    gate.fail         a deterministic verification gate said no
    scrape.repair     ground truth went stale or a selector broke, and the
                      factory fixed its own input
    human.escalation  the factory stopped and asked a person

Every one emits a span tagged `event.kind` *and* increments `daisy.events`,
because the span answers "what happened in this run" and the counter answers
"how often does this happen at all". Neither substitutes for the other.

    IS      the three events, and a `gate()` helper that makes recording them
            the path of least resistance
    IS NOT  alerting, deduplication, rate limiting, or anything that decides
            an event is uninteresting — that judgement belongs in SigNoz,
            where it can be changed without a redeploy

Zero third-party dependencies.
"""

from __future__ import annotations

import contextlib
import time

from .metrics import EVENTS, record_gate, record_repair
from .trace import ERROR, OK, tracer

GATE_FAIL = "gate.fail"
SCRAPE_REPAIR = "scrape.repair"
HUMAN_ESCALATION = "human.escalation"

KINDS = (GATE_FAIL, SCRAPE_REPAIR, HUMAN_ESCALATION)


def emit(kind: str, attrs: dict | None = None, status: int = ERROR):
    """One instant span carrying `event.kind`, plus the counter.

    The span is named for the kind, so a trace viewer's span-name facet is
    already the event taxonomy with no configuration.
    """
    a = dict(attrs or {})
    a["event.kind"] = kind
    with tracer().span(kind, a) as s:
        s.status = status
    EVENTS.add(1, {"event.kind": kind})
    return s


def gate_fail(gate: str, margin: float | None = None, detail: str = "", **extra):
    """A gate said no. This is the signal the whole rubric is about."""
    a = {"gate.name": gate, "gate.detail": detail}
    if margin is not None:
        a["gate.margin"] = float(margin)
    a.update(extra)
    return emit(GATE_FAIL, a)


def scrape_repair(source: str, action: str, detail: str = "", **extra):
    """The factory repaired its own ground truth. Not an error — a save."""
    a = {"scrape.source": source, "repair.action": action, "repair.detail": detail}
    a.update(extra)
    record_repair("scrape." + source, action)
    return emit(SCRAPE_REPAIR, a, status=OK)


def human_escalation(reason: str, waiting_on: str = "", **extra):
    """The factory stopped and asked. Worth a red span: unattended autonomy
    that quietly guesses instead of asking is the failure this prevents."""
    a = {"escalation.reason": reason, "escalation.waiting_on": waiting_on}
    a.update(extra)
    return emit(HUMAN_ESCALATION, a)


class Verdict:
    """What a gate decided, filled in by the gate body."""

    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.margin: float | None = None
        self.detail = ""

    def fail(self, margin: float | None = None, detail: str = "") -> "Verdict":
        self.passed = False
        self.margin = margin
        self.detail = detail
        return self


@contextlib.contextmanager
def gate(name: str, attrs: dict | None = None):
    """Run one verification gate inside a span and make its verdict a signal.

        with obs.gate("physics.bend") as g:
            r = bending(2.4, 90, 18, 3.2, "PETG")
            if not r.against(1.5):
                g.fail(r.margin, "web too thin")

    A gate that raises counts as a gate that failed. The alternative — an
    exception escaping as a crash while the gate is recorded as neither pass
    nor fail — is how a factory reports green on a run that never finished.
    """
    v = Verdict(name)
    t0 = time.perf_counter()
    with tracer().span("gate." + name, dict(attrs or {})) as s:
        try:
            yield v
        except Exception as exc:
            v.fail(None, "%s: %s" % (type(exc).__name__, exc))
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            record_gate(name, v.passed, ms)
            s.set("gate.name", name)
            s.set("gate.result", "pass" if v.passed else "fail")
            if v.margin is not None:
                s.set("gate.margin", float(v.margin))
            if not v.passed:
                s.status = ERROR
                s.message = v.detail or "gate failed"
                # Emitted inside the gate span, so the event is a child of the
                # thing that failed rather than a sibling of it.
                gate_fail(name, v.margin, v.detail)
