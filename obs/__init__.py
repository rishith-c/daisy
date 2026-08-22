"""
The factory's observability layer — OTLP to SigNoz, or to a file, always both
available and never required.

    import obs

    with obs.span("factory_run", {"run.id": 1042}):
        with obs.gate("physics.bend") as g:
            if margin < 1.5:
                g.fail(margin, "web too thin")        # -> span + counter + event
        obs.scrape_repair("mcmaster", "reselect")
    obs.flush()

Four modules, one rule each:

    otlp.py     encode and ship; never raise at the caller
    trace.py    spans and their nesting; never block the caller
    metrics.py  the six numbers that make the pitch checkable
    events.py   failure and repair as signals rather than prose

With `SIGNOZ_ENDPOINT` set, everything goes to SigNoz. With nothing set,
everything goes to `obs/spool/*.jsonl` and `obs.cli replay` ships it later.
The caller cannot tell the difference, which is the point: a demo on a dead
wifi network runs the same code path as a demo on a good one.
"""

from .events import (GATE_FAIL, HUMAN_ESCALATION, SCRAPE_REPAIR, KINDS, Verdict,
                     emit, gate, gate_fail, human_escalation, scrape_repair)
from .metrics import (REGISTRY, record_freshness, record_gate, record_repair,
                      record_tokens, registry)
from .otlp import Config, Exporter, configure, exporter, from_env, replay
from .trace import Span, current, log, span, tracer

__all__ = [
    "Config", "Exporter", "Span", "Verdict", "REGISTRY", "KINDS",
    "GATE_FAIL", "SCRAPE_REPAIR", "HUMAN_ESCALATION",
    "configure", "current", "emit", "exporter", "flush", "from_env", "gate",
    "gate_fail", "human_escalation", "log", "record_freshness", "record_gate",
    "record_repair", "record_tokens", "registry", "replay", "scrape_repair",
    "span", "tracer", "status",
]


def flush(timeout: float = 5.0) -> bool:
    """Ship everything: queued spans and logs first, then a metric snapshot.

    Spans first because they are the perishable ones — metrics are cumulative
    and the next export carries the same totals, while a dropped span is gone.
    """
    ok = tracer().flush(timeout)
    REGISTRY.export()
    return ok


def status() -> dict:
    """Where telemetry is going and what has happened to it so far."""
    exp = exporter()
    st = exp.stats()
    st.update({
        "mode": "live" if exp.cfg.live else "offline",
        "endpoint": exp.cfg.endpoint,
        "key": bool(exp.cfg.key),
        "service": exp.cfg.service,
        "spool_dir": exp.cfg.spool_dir,
        "queue_dropped": tracer().batcher.dropped,
        "submitted": dict(tracer().batcher.submitted),
    })
    return st
