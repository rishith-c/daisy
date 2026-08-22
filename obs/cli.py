"""Terminal interface to the observability layer.

    python3 -m obs.cli selftest          emit one of everything, say where it went
    python3 -m obs.cli tail              read the spool back, readably
    python3 -m obs.cli replay            ship the spool to the live endpoint

`selftest` is the command to run when someone says "is SigNoz wired up". It
answers with the destination of every batch it produced rather than a boolean,
because "it worked" and "it silently spooled to disk" are different answers and
only one of them means the dashboard will have anything on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import events, metrics, otlp, trace
from .otlp import SIGNALS, Config, Exporter, from_env


def _cfg(a) -> Config:
    cfg = from_env()
    if getattr(a, "endpoint", None):
        cfg = Config(endpoint=otlp._base(a.endpoint), key=getattr(a, "key", "") or cfg.key,
                     service=cfg.service, spool_dir=cfg.spool_dir, timeout=cfg.timeout)
    elif getattr(a, "key", None):
        cfg = Config(endpoint=cfg.endpoint, key=a.key, service=cfg.service,
                     spool_dir=cfg.spool_dir, timeout=cfg.timeout)
    return cfg


def _banner(cfg: Config) -> None:
    print("endpoint        %s" % (cfg.endpoint or "(unset)"))
    print("ingestion key   %s" % ("set (%d chars)" % len(cfg.key) if cfg.key else "(unset)"))
    print("service.name    %s" % cfg.service)
    print("spool           %s" % cfg.spool_dir)
    print("mode            %s" % ("LIVE" if cfg.live else "OFFLINE — everything spools to disk"))


# ---------------------------------------------------------------------------

def cmd_selftest(a) -> int:
    cfg = _cfg(a)
    exp = otlp.configure(cfg)
    _banner(cfg)
    print()

    t = trace.tracer()
    with t.span("obs.selftest", {"selftest": True}) as root:
        trace_id = root.trace_id
        t.log("selftest starting", "INFO", {"pid": os.getpid()})
        with events.gate("physics.bend", {"material": "PETG"}) as g:
            time.sleep(0.002)
            g.fail(0.72, "3.2 mm PETG web at FoS 1.5")     # -> gate.fail event
        metrics.record_repair("physics.bend", "algebra")
        with events.gate("physics.bend.rerun", {"material": "PETG"}):
            time.sleep(0.001)                              # passes, no event
        events.scrape_repair("mcmaster", "reselect", "selector drifted")
        events.human_escalation("two repairs in one run", "rishith")
        metrics.record_freshness("mcmaster", 47.0)
        metrics.record_tokens("claude.frontend", "in", 18412)
        metrics.record_tokens("claude.frontend", "out", 2210)
        t.log("selftest complete", "WARN", {"gate.failed": 1})

    t.flush(cfg.timeout + 2.0)
    md = metrics.export(exp)

    deliveries = list(t.batcher.exports)
    if md is not None:
        deliveries.append(md)
    sub = t.batcher.submitted
    print("emitted         %d spans · %d metric instruments · %d log records"
          % (sub["traces"], len(metrics.REGISTRY.collect()), sub["logs"]))
    print("trace id        %s\n" % trace_id)

    dropped = 0
    for d in deliveries:
        if d.dest == "live":
            print("  live    POST %s" % d.target)
        elif d.dest == "spool":
            why = d.error or ("offline" if not cfg.live else "")
            print("  spool   %s%s" % (d.target, ("   (%s)" % why) if why else ""))
        else:
            dropped += 1
            print("  DROPPED %s" % d.error)
    if not deliveries:
        print("  (nothing was exported — this is a bug)")
        return 1

    print()
    if cfg.live and all(d.dest == "live" for d in deliveries):
        print("all signals reached SigNoz. Search traces for %s" % trace_id)
    elif not cfg.live:
        print("offline: read it back with `python3 -m obs.cli tail`,")
        print("then set SIGNOZ_ENDPOINT + SIGNOZ_INGESTION_KEY and run "
              "`python3 -m obs.cli replay`")
    else:
        print("endpoint configured but unreachable — spooled instead, nothing lost.")
        print("run `python3 -m obs.cli replay` once the network is back")
    return 1 if dropped else 0


# ---------------------------------------------------------------------------

def _ts(ns: str | int) -> str:
    n = int(ns)
    return time.strftime("%H:%M:%S", time.localtime(n / 1e9)) + ".%03d" % (n // 1_000_000 % 1000)


STATUS = {0: "     ", 1: "ok   ", 2: "ERROR"}


def _tail_traces(payloads: list, n: int) -> None:
    rows = []
    for p in payloads:
        for rs in p.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    rows.append(s)
    rows.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))
    print("traces   %d payload(s), %d span(s)" % (len(payloads), len(rows)))
    for s in rows[-n:]:
        dur = (int(s.get("endTimeUnixNano", 0)) - int(s.get("startTimeUnixNano", 0))) / 1e6
        kind = ""
        for at in s.get("attributes", []):
            if at["key"] == "event.kind":
                kind = "  <%s>" % at["value"].get("stringValue", "")
        print("  %s  %-5s %-28s %8.2f ms  %s%s"
              % (_ts(s.get("startTimeUnixNano", 0)),
                 STATUS.get(s.get("status", {}).get("code", 0), "?"),
                 s.get("name", "")[:28], dur, s.get("traceId", "")[:16], kind))


def _tail_metrics(payloads: list, n: int) -> None:
    rows = []
    for p in payloads:
        for rm in p.get("resourceMetrics", []):
            for sm in rm.get("scopeMetrics", []):
                rows.extend(sm.get("metrics", []))
    print("metrics  %d payload(s), %d instrument(s)" % (len(payloads), len(rows)))
    for m in rows[-n:]:
        body = m.get("sum") or m.get("histogram") or {}
        for dp in body.get("dataPoints", []):
            at = " ".join("%s=%s" % (a["key"], list(a["value"].values())[0])
                          for a in dp.get("attributes", []))
            if "asInt" in dp:
                val = dp["asInt"]
            else:
                val = "n=%s sum=%.3g min=%.3g max=%.3g" % (
                    dp.get("count"), dp.get("sum", 0.0), dp.get("min", 0.0), dp.get("max", 0.0))
            print("  %-24s %-34s %s" % (m.get("name", "")[:24], at[:34], val))


def _tail_logs(payloads: list, n: int) -> None:
    rows = []
    for p in payloads:
        for rl in p.get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                rows.extend(sl.get("logRecords", []))
    rows.sort(key=lambda r: int(r.get("timeUnixNano", 0)))
    print("logs     %d payload(s), %d record(s)" % (len(payloads), len(rows)))
    for r in rows[-n:]:
        print("  %s  %-5s %-40s %s"
              % (_ts(r.get("timeUnixNano", 0)), r.get("severityText", ""),
                 r.get("body", {}).get("stringValue", "")[:40], r.get("traceId", "")[:16]))


TAILERS = {"traces": _tail_traces, "metrics": _tail_metrics, "logs": _tail_logs}


def cmd_tail(a) -> int:
    exp = Exporter(_cfg(a))
    wanted = [a.signal] if a.signal else list(SIGNALS)
    empty = True
    for signal in wanted:
        payloads = otlp.read_spool(exp, signal)
        if not payloads:
            continue
        empty = False
        TAILERS[signal](payloads, a.n)
        print()
    if empty:
        print("spool is empty: %s" % exp.cfg.spool_dir)
        print("run `python3 -m obs.cli selftest` with no SIGNOZ_ENDPOINT set")
    return 0


# ---------------------------------------------------------------------------

def cmd_replay(a) -> int:
    cfg = _cfg(a)
    exp = Exporter(cfg)
    _banner(cfg)
    print()
    if not cfg.live:
        print("no endpoint configured — nothing to replay to.")
        print("set SIGNOZ_ENDPOINT (and SIGNOZ_INGESTION_KEY) first")
        return 2
    out = otlp.replay(exp, keep=a.keep)
    if not out:
        print("spool is empty — nothing to replay")
        return 0
    bad = 0
    for signal, r in out.items():
        bad += r["failed"]
        print("  %-8s %d/%d sent%s" % (signal, r["sent"], r["total"],
                                       "   %d left in spool" % r["failed"] if r["failed"] else ""))
    if a.keep:
        print("\n--keep: the spool was not consumed")
    return 1 if bad else 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="obs.cli", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("selftest", help="emit a sample trace, metric and log")
    s.add_argument("--endpoint", default="", help="override SIGNOZ_ENDPOINT")
    s.add_argument("--key", default="", help="override SIGNOZ_INGESTION_KEY")
    s.set_defaults(fn=cmd_selftest)

    t = sub.add_parser("tail", help="print the spool readably")
    t.add_argument("--signal", choices=sorted(SIGNALS), default=None)
    t.add_argument("-n", type=int, default=25, help="rows per signal (default 25)")
    t.set_defaults(fn=cmd_tail)

    r = sub.add_parser("replay", help="POST the spool to the live endpoint")
    r.add_argument("--endpoint", default="", help="override SIGNOZ_ENDPOINT")
    r.add_argument("--key", default="", help="override SIGNOZ_INGESTION_KEY")
    r.add_argument("--keep", action="store_true", help="do not consume the spool on success")
    r.set_defaults(fn=cmd_replay)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
