"""
Counters and histograms for the six numbers that decide whether the factory
is working.

Traces answer "what happened in run 1042". Metrics answer "is the physics gate
failing more than it did an hour ago", which is the question you cannot answer
by reading traces one at a time. The instrument list is therefore short and
opinionated — each one exists because a specific claim in the pitch is only
credible if it is measured:

    daisy.gate.runs         pass/fail by gate name      "every artifact passes gates"
    daisy.gate.duration     ms by gate name             "gates are cheap enough to run always"
    daisy.scrape.freshness  seconds of staleness        "ground truth, not a hardcoded constant"
    daisy.repair.attempts   by gate and repair kind     "it fixes itself, and how often"
    daisy.agent.tokens      in/out by agent             "what the run actually cost"
    daisy.events            by event.kind               failure and repair as signals

    IS      synchronous Sum and Histogram instruments, attribute-keyed
            aggregation, and OTLP cumulative export
    IS NOT  exemplars, exponential histograms, gauges, observable/async
            instruments, views, or delta temporality

Cumulative is the honest label for what this does: values accumulate for the
life of the process and every export is a snapshot of the running total, with
`startTimeUnixNano` pinned to process start. Nothing is reset on export, so a
dropped batch loses resolution rather than data — the next export still
carries the full count.

Zero third-party dependencies.
"""

from __future__ import annotations

import atexit
import bisect
import math
import threading
import time

from .otlp import attributes, exporter, metric_payload

# OTLP AggregationTemporality — 1 is DELTA, 2 is CUMULATIVE.
CUMULATIVE = 2

# Gate durations span the taste linter (single-digit ms) and a rebuilt
# frontend (tens of seconds), so the buckets are log-ish across four decades.
DURATION_MS = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]

# Scrape freshness: the interesting thresholds are "this minute", "this hour",
# "today", "the data is a lie".
FRESHNESS_S = [1, 5, 15, 30, 60, 300, 900, 3600, 21600, 86400]


def _key(attrs: dict | None) -> tuple:
    """Attribute sets are the aggregation key, so they must be hashable and
    order-independent."""
    return tuple(sorted((str(k), v) for k, v in (attrs or {}).items()))


class Counter:
    """A monotonic Sum, keyed by attribute set."""

    def __init__(self, name: str, unit: str = "1", description: str = ""):
        self.name, self.unit, self.description = name, unit, description
        self.points: dict[tuple, int] = {}
        self._lock = threading.Lock()

    def add(self, value: int = 1, attrs: dict | None = None) -> None:
        with self._lock:
            k = _key(attrs)
            self.points[k] = self.points.get(k, 0) + int(value)

    def get(self, attrs: dict | None = None) -> int:
        with self._lock:
            return self.points.get(_key(attrs), 0)

    def to_otlp(self, start_ns: int, now_ns: int) -> dict | None:
        with self._lock:
            pts = list(self.points.items())
        if not pts:
            return None
        return {
            "name": self.name, "unit": self.unit, "description": self.description,
            "sum": {
                "dataPoints": [{
                    "attributes": attributes(dict(k)),
                    "startTimeUnixNano": str(start_ns),
                    "timeUnixNano": str(now_ns),
                    "asInt": str(v),
                } for k, v in pts],
                "aggregationTemporality": CUMULATIVE,
                "isMonotonic": True,
            },
        }


class Histogram:
    """An explicit-bucket Histogram, keyed by attribute set."""

    def __init__(self, name: str, unit: str, bounds: list, description: str = ""):
        self.name, self.unit, self.description = name, unit, description
        self.bounds = list(bounds)
        self.points: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def record(self, value: float, attrs: dict | None = None) -> bool:
        """False means the sample was refused, which happens only for NaN/inf.

        A non-finite sample serialises as the literal `Infinity`, which is not
        JSON, and one bad sample would cost the whole batch. Losing the sample
        is the cheaper failure.
        """
        v = float(value)
        if not math.isfinite(v):
            return False
        # OTLP buckets are (bounds[i-1], bounds[i]] — upper bound inclusive —
        # so bisect_left, not bisect_right, puts a value equal to a boundary
        # in the bucket that boundary names.
        idx = bisect.bisect_left(self.bounds, v)
        with self._lock:
            k = _key(attrs)
            p = self.points.get(k)
            if p is None:
                p = self.points[k] = {"count": 0, "sum": 0.0, "min": v, "max": v,
                                      "buckets": [0] * (len(self.bounds) + 1)}
            p["count"] += 1
            p["sum"] += v
            p["min"] = min(p["min"], v)
            p["max"] = max(p["max"], v)
            p["buckets"][idx] += 1
        return True

    def get(self, attrs: dict | None = None) -> dict | None:
        with self._lock:
            p = self.points.get(_key(attrs))
            return dict(p) if p else None

    def to_otlp(self, start_ns: int, now_ns: int) -> dict | None:
        with self._lock:
            pts = [(k, dict(v)) for k, v in self.points.items()]
        if not pts:
            return None
        return {
            "name": self.name, "unit": self.unit, "description": self.description,
            "histogram": {
                "dataPoints": [{
                    "attributes": attributes(dict(k)),
                    "startTimeUnixNano": str(start_ns),
                    "timeUnixNano": str(now_ns),
                    "count": str(p["count"]),
                    "sum": p["sum"],
                    "min": p["min"],
                    "max": p["max"],
                    "bucketCounts": [str(c) for c in p["buckets"]],
                    "explicitBounds": list(self.bounds),
                } for k, p in pts],
                "aggregationTemporality": CUMULATIVE,
            },
        }


class Registry:
    """Every instrument in the process, and the one call that ships them."""

    def __init__(self):
        self.instruments: list = []
        self.start_ns = time.time_ns()

    def counter(self, name: str, unit: str = "1", description: str = "") -> Counter:
        c = Counter(name, unit, description)
        self.instruments.append(c)
        return c

    def histogram(self, name: str, unit: str, bounds: list, description: str = "") -> Histogram:
        h = Histogram(name, unit, bounds, description)
        self.instruments.append(h)
        return h

    def collect(self) -> list:
        """Instruments that have seen at least one sample. Empty is normal."""
        now = time.time_ns()
        out = []
        for i in self.instruments:
            m = i.to_otlp(self.start_ns, now)
            if m is not None:
                out.append(m)
        return out

    def export(self, exp=None):
        """Ship a snapshot. Returns None when there is nothing to say.

        Exporting an empty payload is not free — it is a round trip and a row
        in someone's ingestion bill for zero information.
        """
        metrics = self.collect()
        if not metrics:
            return None
        exp = exp or exporter()
        return exp.export("metrics", metric_payload(metrics, exp.cfg))


# ---------------------------------------------------------------------------
# the instruments
# ---------------------------------------------------------------------------

REGISTRY = Registry()

GATE_RUNS = REGISTRY.counter(
    "daisy.gate.runs", "1", "verification gate outcomes by gate name and result")
GATE_DURATION = REGISTRY.histogram(
    "daisy.gate.duration", "ms", DURATION_MS, "wall time of one gate")
SCRAPE_FRESHNESS = REGISTRY.histogram(
    "daisy.scrape.freshness", "s", FRESHNESS_S, "age of the ground truth a gate ran against")
REPAIR_ATTEMPTS = REGISTRY.counter(
    "daisy.repair.attempts", "1", "auto-repair attempts by gate and repair kind")
AGENT_TOKENS = REGISTRY.counter(
    "daisy.agent.tokens", "1", "tokens consumed by agent and direction")
EVENTS = REGISTRY.counter(
    "daisy.events", "1", "first-class factory events by kind")


def record_gate(gate: str, passed: bool, ms: float) -> None:
    GATE_RUNS.add(1, {"gate": gate, "result": "pass" if passed else "fail"})
    GATE_DURATION.record(ms, {"gate": gate})


def record_freshness(source: str, age_s: float) -> None:
    SCRAPE_FRESHNESS.record(age_s, {"source": source})


def record_repair(gate: str, kind: str) -> None:
    """`kind` matches precedent's fix_kind vocabulary — algebra,
    resume-findings, human — so a metric and a case can be joined."""
    REPAIR_ATTEMPTS.add(1, {"gate": gate, "repair.kind": kind})


def record_tokens(agent: str, direction: str, n: int) -> None:
    AGENT_TOKENS.add(n, {"agent": agent, "direction": direction})


# A run that records gate outcomes and exits without calling flush() would
# otherwise ship nothing at all — and the last thing recorded before an exit
# is usually the interesting one. No-ops when no sample was ever taken.
atexit.register(REGISTRY.export)


def registry() -> Registry:
    return REGISTRY


def export(exp=None):
    return REGISTRY.export(exp)
