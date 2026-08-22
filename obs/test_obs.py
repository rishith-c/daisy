"""Tests for the observability layer.

    python3 -m obs.test_obs

Nothing here opens a socket or reads a credential. The live path is exercised
by overriding `Exporter._post`, and every spool test writes into its own
tempdir — a telemetry test suite that only passes on the machine with the
SigNoz key is not a test suite, and one that quietly hits the network turns a
red CI run into a wifi problem.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import shutil
import tempfile
import time

from .events import (GATE_FAIL, HUMAN_ESCALATION, KINDS, SCRAPE_REPAIR,
                     gate, gate_fail, human_escalation, scrape_repair)
from .metrics import (EVENTS, GATE_RUNS, REGISTRY, Counter, Histogram, Registry,
                      _key, export as export_metrics)
from .otlp import (Config, Delivery, Exporter, SIGNALS, _base, anyvalue,
                   attributes, configure, from_env, log_payload, metric_payload,
                   read_spool, replay, resource, trace_payload)
from .trace import (ERROR, OK, Batcher, Tracer, current, new_span_id,
                    new_trace_id, tracer)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# fixtures — nothing below this line touches a network
# ---------------------------------------------------------------------------

class Recorder:
    """Stands in for the Exporter. Records payloads, optionally explodes."""

    def __init__(self, cfg=None, boom=False):
        self.cfg = cfg or Config()
        self.calls = []
        self.boom = boom

    def export(self, signal, payload):
        self.calls.append((signal, payload))
        if self.boom:
            raise RuntimeError("transport is on fire")
        return Delivery("live", "fake://" + signal, "")

    def spans(self):
        out = []
        for signal, p in self.calls:
            if signal != "traces":
                continue
            for rs in p["resourceSpans"]:
                for ss in rs["scopeSpans"]:
                    out.extend(ss["spans"])
        return out


class FakeHTTP(Exporter):
    """The real Exporter with the socket replaced."""

    def __init__(self, cfg, boom=False):
        super().__init__(cfg)
        self.posts = []
        self.boom = boom

    def _post(self, url, body):
        self.posts.append((url, body, dict(self.cfg.headers())))
        if self.boom:
            raise OSError("HTTP 503 upstream unavailable")


def attr(obj, key):
    """Pull one attribute value out of an OTLP attribute list."""
    for a in obj.get("attributes", []):
        if a["key"] == key:
            return list(a["value"].values())[0]
    return None


def named(spans, name):
    return [s for s in spans if s["name"] == name]


def collect_spans(payloads):
    out = []
    for p in payloads:
        for rs in p.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                out.extend(ss["spans"])
    return out


# ---------------------------------------------------------------------------

def test_config():
    print("\nconfig — what a user has to set, and what they can get wrong")
    c = from_env({"SIGNOZ_ENDPOINT": "https://ingest.us.signoz.cloud:443",
                  "SIGNOZ_INGESTION_KEY": "abc123"})
    check("SIGNOZ_ENDPOINT is read", c.endpoint == "https://ingest.us.signoz.cloud:443", c.endpoint)
    check("the endpoint alone means live", c.live)
    check("the key rides the signoz-ingestion-key header",
          c.headers().get("signoz-ingestion-key") == "abc123", str(c.headers()))
    check("content type is json", c.headers()["Content-Type"] == "application/json")
    check("signal paths are appended", c.url("traces").endswith("/v1/traces"), c.url("traces"))
    check("all three signals have a path", set(SIGNALS) == {"traces", "metrics", "logs"})

    alias = from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example:4318"})
    check("OTEL_EXPORTER_OTLP_ENDPOINT works as an alias",
          alias.endpoint == "https://otel.example:4318", alias.endpoint)
    both = from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example:4318",
                     "SIGNOZ_ENDPOINT": "https://signoz.example:443"})
    check("the SigNoz-specific name wins when both are set",
          both.endpoint == "https://signoz.example:443", both.endpoint)

    check("a pasted /v1/traces url is normalised back to the origin",
          _base("https://ingest.us.signoz.cloud:443/v1/traces") == "https://ingest.us.signoz.cloud:443")
    check("a pasted /v1/logs url is normalised too",
          _base("https://x.example/v1/logs") == "https://x.example")
    check("a trailing slash is dropped", _base("https://x.example/") == "https://x.example")
    check("a bare host gets https", _base("ingest.example:443") == "https://ingest.example:443")
    check("http is left alone", _base("http://localhost:4318") == "http://localhost:4318")

    off = from_env({})
    check("nothing configured is offline, not an error", not off.live and off.endpoint == "")
    check("offline sends no ingestion key header",
          "signoz-ingestion-key" not in off.headers(), str(off.headers()))
    check("a key with no endpoint is still offline",
          not from_env({"SIGNOZ_INGESTION_KEY": "k"}).live)
    check("service name defaults to daisy", off.service == "daisy")
    check("OTEL_SERVICE_NAME overrides it",
          from_env({"OTEL_SERVICE_NAME": "daisy-lane-2"}).service == "daisy-lane-2")
    check("a junk timeout falls back rather than raising",
          from_env({"DAISY_OBS_TIMEOUT": "banana"}).timeout == 5.0)


def test_encoding():
    print("\nOTLP/JSON encoding — the parts collectors are picky about")
    check("bool is tested before int", anyvalue(True) == {"boolValue": True},
          str(anyvalue(True)))
    check("64-bit ints are strings, per proto3 json",
          anyvalue(7) == {"intValue": "7"}, str(anyvalue(7)))
    check("floats stay numbers", anyvalue(1.5) == {"doubleValue": 1.5})
    check("inf degrades to a string instead of emitting invalid json",
          "stringValue" in anyvalue(float("inf")), str(anyvalue(float("inf"))))
    check("nan degrades too", "stringValue" in anyvalue(float("nan")))
    check("lists become arrayValue", "arrayValue" in anyvalue([1, 2]))
    check("None is an empty string, not a null", anyvalue(None) == {"stringValue": ""})
    check("attributes come out sorted",
          [a["key"] for a in attributes({"z": 1, "a": 2})] == ["a", "z"])

    # A margin of inf is real in this codebase: hardware.margins returns it
    # whenever the computed stress is zero.
    body = json.dumps(attributes({"gate.margin": float("inf")}))
    check("a payload with an infinite margin is still valid json",
          json.loads(body)[0]["value"]["stringValue"] == "inf", body)

    cfg = Config(service="daisy-test")
    r = resource(cfg)
    check("resource carries service.name", attr(r, "service.name") == "daisy-test")
    check("resource declares the sdk", attr(r, "telemetry.sdk.name") == "daisy-obs")

    tp = trace_payload([{"name": "x"}], cfg)
    check("trace payload nests resourceSpans/scopeSpans/spans",
          tp["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "x")
    mp = metric_payload([{"name": "m"}], cfg)
    check("metric payload nests resourceMetrics/scopeMetrics/metrics",
          mp["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"] == "m")
    lp = log_payload([{"body": {"stringValue": "hi"}}], cfg)
    check("log payload nests resourceLogs/scopeLogs/logRecords",
          lp["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"] == "hi")


def test_ids_and_spans():
    print("\nspans — W3C ids, nesting, status, exceptions")
    tid, sid = new_trace_id(), new_span_id()
    check("trace id is 16 bytes of hex", len(tid) == 32 and int(tid, 16) >= 0, tid)
    check("span id is 8 bytes of hex", len(sid) == 16 and int(sid, 16) >= 0, sid)
    check("ids are not the forbidden all-zero value", int(tid, 16) != 0 and int(sid, 16) != 0)
    check("ids do not repeat", len({new_trace_id() for _ in range(200)}) == 200)

    rec = Recorder()
    t = Tracer(exp=rec)
    with t.span("factory_run", {"run.id": 1042}) as root:
        check("the running span is discoverable", current() is root)
        with t.span("gate.taste.t1") as child:
            check("a child inherits the trace id", child.trace_id == root.trace_id)
            check("a child names its parent", child.parent_id == root.span_id)
        check("the stack unwinds", current() is root)
    check("the stack empties", current() is None)
    t.flush()

    spans = rec.spans()
    check("both spans were exported", len(spans) == 2, str(len(spans)))
    kid = named(spans, "gate.taste.t1")[0]
    par = named(spans, "factory_run")[0]
    check("the child carries parentSpanId", kid["parentSpanId"] == par["spanId"])
    check("the root carries no parentSpanId", "parentSpanId" not in par)
    check("timestamps are nano strings",
          isinstance(par["startTimeUnixNano"], str) and int(par["startTimeUnixNano"]) > 1e18,
          par["startTimeUnixNano"])
    check("end is after start", int(par["endTimeUnixNano"]) > int(par["startTimeUnixNano"]))
    check("a clean span is OK", par["status"]["code"] == OK, str(par["status"]))
    check("attributes survive the round trip", attr(par, "run.id") == "1042")
    check("span kind defaults to internal", par["kind"] == 1)

    rec2 = Recorder()
    t2 = Tracer(exp=rec2)
    raised = False
    try:
        with t2.span("boom"):
            raise ValueError("web too thin")
    except ValueError:
        raised = True
    t2.flush()
    s = rec2.spans()[0]
    check("the exception still reaches the caller", raised)
    check("the span is ERROR", s["status"]["code"] == ERROR, str(s["status"]))
    check("the status message names the exception",
          "web too thin" in s["status"].get("message", ""), str(s["status"]))
    ev = s.get("events", [{}])[0]
    check("an exception event is recorded", ev.get("name") == "exception", str(ev))
    check("the event carries the exception type",
          attr(ev, "exception.type") == "ValueError")
    check("the event carries a stacktrace", bool(attr(ev, "exception.stacktrace")))

    rec3 = Recorder()
    t3 = Tracer(exp=rec3)
    with t3.span("timed"):
        time.sleep(0.01)
    t3.flush()
    dur = (int(rec3.spans()[0]["endTimeUnixNano"]) -
           int(rec3.spans()[0]["startTimeUnixNano"])) / 1e6
    check("duration is measured, not guessed", 8.0 < dur < 250.0, "%.2f ms" % dur)


def test_logs():
    print("\nlogs — worth having only because they carry the span")
    rec = Recorder()
    t = Tracer(exp=rec)
    t.log("outside any span", "INFO")
    with t.span("gate.physics") as s:
        t.log("stress over allowable", "ERROR", {"margin": 0.72})
        tid, sid = s.trace_id, s.span_id
    t.flush()
    logs = [r for sig, p in rec.calls if sig == "logs"
            for rl in p["resourceLogs"] for sl in rl["scopeLogs"] for r in sl["logRecords"]]
    check("both records exported", len(logs) == 2, str(len(logs)))
    loose, inside = logs[0], logs[1]
    check("a record outside a span has no trace id", "traceId" not in loose)
    check("a record inside a span is correlated", inside.get("traceId") == tid)
    check("and names the exact span", inside.get("spanId") == sid)
    check("severity number follows the otlp table", inside["severityNumber"] == 17,
          str(inside["severityNumber"]))
    check("severity text is preserved", inside["severityText"] == "ERROR")
    check("attributes are encoded", attr(inside, "margin") == 0.72)


def test_batching():
    print("\nbatching — dropping a span must never block or crash the caller")
    b = Batcher(exp=Recorder(), max_queue=3)          # deliberately not started
    results = [b.submit("traces", {"n": i}) for i in range(5)]
    check("the queue accepts up to its bound", results[:3] == [True, True, True])
    check("and refuses beyond it instead of blocking", results[3:] == [False, False])
    check("refusals are counted", b.dropped == 2, str(b.dropped))
    check("submit never raised", True)

    rec = Recorder()
    b2 = Batcher(exp=rec)
    b2.submit("traces", {"name": "a"})
    b2.submit("logs", {"body": {"stringValue": "b"}})
    check("flush works with no worker thread at all", b2.flush())
    check("both signals were exported separately", len(rec.calls) == 2, str(len(rec.calls)))
    check("the queue is empty afterwards", b2.q.empty())

    rec2 = Recorder()
    b3 = Batcher(exp=rec2, interval=0.05)
    b3.start()
    for i in range(20):
        b3.submit("traces", {"n": i})
    check("a started batcher flushes on demand", b3.flush(2.0))
    total = sum(len(p["resourceSpans"][0]["scopeSpans"][0]["spans"]) for _, p in rec2.calls)
    check("every submitted span was exported", total == 20, str(total))
    check("they were batched, not sent one at a time", len(rec2.calls) < 20, str(len(rec2.calls)))

    boom = Recorder(boom=True)
    b4 = Batcher(exp=boom, interval=0.05)
    b4.start()
    b4.submit("traces", {"name": "doomed"})
    check("a transport that throws does not kill the flush thread", b4.flush(2.0))
    b4.submit("traces", {"name": "after"})
    check("and the thread is still draining afterwards", b4.flush(2.0))
    check("both batches were attempted", len(boom.calls) >= 2, str(len(boom.calls)))

    t = Tracer(exp=Recorder(boom=True))
    with t.span("caller must not notice"):
        pass
    check("a broken transport is invisible to the caller", t.flush(2.0))


def test_metrics():
    print("\nmetrics — counters and histograms")
    check("attribute order does not change the key",
          _key({"a": 1, "b": 2}) == _key({"b": 2, "a": 1}))

    c = Counter("daisy.test.count")
    c.add(1, {"gate": "physics"})
    c.add(1, {"gate": "physics"})
    c.add(3, {"gate": "taste"})
    check("counts aggregate per attribute set", c.get({"gate": "physics"}) == 2)
    check("different attributes are different series", c.get({"gate": "taste"}) == 3)
    check("an unseen series reads zero", c.get({"gate": "nope"}) == 0)
    m = c.to_otlp(1, 2)
    check("a counter encodes as a Sum", "sum" in m)
    check("with two data points", len(m["sum"]["dataPoints"]) == 2)
    check("cumulative temporality", m["sum"]["aggregationTemporality"] == 2)
    check("declared monotonic", m["sum"]["isMonotonic"] is True)
    check("values are int strings", m["sum"]["dataPoints"][0]["asInt"] in ("2", "3"))
    check("an untouched counter encodes to nothing at all",
          Counter("daisy.test.empty").to_otlp(1, 2) is None)

    h = Histogram("daisy.test.hist", "ms", [1, 5, 10])
    for v in (0.5, 1.0, 1.5, 5.0, 5.5, 10.0, 11.0):
        h.record(v, {"gate": "g"})
    p = h.get({"gate": "g"})
    check("every sample counted", p["count"] == 7, str(p["count"]))
    check("sum is the sum", abs(p["sum"] - 34.5) < 1e-9, str(p["sum"]))
    check("min and max are tracked", p["min"] == 0.5 and p["max"] == 11.0)
    check("there is one bucket more than there are bounds",
          len(p["buckets"]) == 4, str(p["buckets"]))
    # OTLP buckets are (lower, upper] — a value equal to a bound belongs to
    # that bound's bucket, not the next one up.
    check("boundary values land in the bucket their bound names",
          p["buckets"] == [2, 2, 2, 1], str(p["buckets"]))
    check("an infinite sample is refused, not serialised",
          h.record(float("inf"), {"gate": "g"}) is False)
    check("and the refusal does not corrupt the count",
          h.get({"gate": "g"})["count"] == 7)
    hm = h.to_otlp(1, 2)
    check("a histogram encodes as a Histogram", "histogram" in hm)
    dp = hm["histogram"]["dataPoints"][0]
    check("bucket counts are strings", all(isinstance(x, str) for x in dp["bucketCounts"]))
    check("explicit bounds ride along", dp["explicitBounds"] == [1, 5, 10])
    check("count is a string, sum is a number",
          isinstance(dp["count"], str) and isinstance(dp["sum"], float))

    r = Registry()
    rc = r.counter("daisy.test.r")
    check("an empty registry collects nothing", r.collect() == [])
    rec = Recorder()
    check("and exports nothing rather than an empty payload", r.export(rec) is None)
    rc.add(1, {"x": "y"})
    check("one sample makes one instrument", len(r.collect()) == 1)
    d = r.export(rec)
    check("export returns where it went", d.dest == "live", str(d))
    check("the payload is a metrics payload", rec.calls[0][0] == "metrics")


def test_events():
    print("\nevents — failure and repair as signals, not prose")
    check("the event taxonomy is closed and enumerable",
          KINDS == (GATE_FAIL, SCRAPE_REPAIR, HUMAN_ESCALATION), str(KINDS))
    before = EVENTS.get({"event.kind": GATE_FAIL})
    s = gate_fail("physics.bend", 0.72, "web too thin")
    check("gate.fail is a span, named for the kind", s.name == GATE_FAIL, s.name)
    check("tagged with event.kind", s.attrs.get("event.kind") == GATE_FAIL)
    check("carrying the margin that was short", s.attrs.get("gate.margin") == 0.72)
    check("and it is red", s.status == ERROR, str(s.status))
    check("the counter moved with it",
          EVENTS.get({"event.kind": GATE_FAIL}) == before + 1)

    s2 = scrape_repair("mcmaster", "reselect", "selector drifted")
    check("scrape.repair is its own kind", s2.attrs.get("event.kind") == SCRAPE_REPAIR)
    check("a successful self-repair is not an error", s2.status == OK, str(s2.status))
    check("it names the source", s2.attrs.get("scrape.source") == "mcmaster")

    s3 = human_escalation("two repairs in one run", "rishith")
    check("human.escalation is its own kind", s3.attrs.get("event.kind") == HUMAN_ESCALATION)
    check("asking a human is worth a red span", s3.status == ERROR)
    check("it records who is being waited on",
          s3.attrs.get("escalation.waiting_on") == "rishith")

    ok_before = GATE_RUNS.get({"gate": "unit.pass", "result": "pass"})
    with gate("unit.pass") as g:
        pass
    check("a passing gate counts as a pass",
          GATE_RUNS.get({"gate": "unit.pass", "result": "pass"}) == ok_before + 1)
    check("and leaves the verdict alone", g.passed)

    fail_before = GATE_RUNS.get({"gate": "unit.fail", "result": "fail"})
    ev_before = EVENTS.get({"event.kind": GATE_FAIL})
    with gate("unit.fail") as g2:
        g2.fail(0.4, "short by a lot")
    check("a failing gate counts as a fail",
          GATE_RUNS.get({"gate": "unit.fail", "result": "fail"}) == fail_before + 1)
    check("and emits the gate.fail event exactly once",
          EVENTS.get({"event.kind": GATE_FAIL}) == ev_before + 1)

    raised = False
    crash_before = GATE_RUNS.get({"gate": "unit.crash", "result": "fail"})
    try:
        with gate("unit.crash"):
            raise RuntimeError("the gate itself broke")
    except RuntimeError:
        raised = True
    check("a gate that raises still reaches the caller", raised)
    check("a gate that raises is recorded as failed, never as neither",
          GATE_RUNS.get({"gate": "unit.crash", "result": "fail"}) == crash_before + 1)


def test_offline_spool():
    print("\noffline — the path a bad venue network takes")
    with tempfile.TemporaryDirectory() as d:
        exp = Exporter(Config(spool_dir=d))
        d1 = exp.export("traces", trace_payload([{"name": "a"}], exp.cfg))
        d2 = exp.export("traces", trace_payload([{"name": "b"}], exp.cfg))
        check("offline export lands in the spool", d1.dest == "spool", str(d1))
        check("offline is not an error", d1.error == "", d1.error)
        check("the file is named for the signal",
              d1.target.endswith("traces.jsonl"), d1.target)
        raw = open(os.path.join(d, "traces.jsonl"), "rb").read().splitlines()
        check("one payload per line", len(raw) == 2, str(len(raw)))
        check("every line is valid json on its own",
              all(json.loads(l.decode()) for l in raw))
        check("nothing was posted anywhere", exp.stats()["sent"] == 0)
        check("spooled batches are counted", exp.stats()["spooled"] == 2)
        check("read_spool decodes them back",
              [s["name"] for s in collect_spans(read_spool(exp, "traces"))] == ["a", "b"])
        check("an absent signal reads as empty, not as an error",
              read_spool(exp, "logs") == [])


def test_live_and_fallback():
    print("\nlive — and what happens when live stops working")
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(endpoint="https://ingest.us.signoz.cloud:443", key="K", spool_dir=d)
        exp = FakeHTTP(cfg)
        got = exp.export("traces", trace_payload([{"name": "a"}], cfg))
        check("a live export reports where it went", got.dest == "live", str(got))
        url, body, headers = exp.posts[0]
        check("posted to the traces path",
              url == "https://ingest.us.signoz.cloud:443/v1/traces", url)
        check("with the ingestion key header",
              headers.get("signoz-ingestion-key") == "K", str(headers))
        check("the body is the encoded payload",
              json.loads(body.decode())["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "a")
        check("sends are counted", exp.stats()["sent"] == 1)
        check("nothing was spooled on the happy path",
              not os.path.exists(os.path.join(d, "traces.jsonl")))

        dead = FakeHTTP(cfg, boom=True)
        fell = dead.export("traces", trace_payload([{"name": "b"}], cfg))
        check("a dead endpoint falls back to the spool", fell.dest == "spool", str(fell))
        check("and says why", "503" in fell.error, fell.error)
        check("nothing is lost", len(read_spool(dead, "traces")) == 1)
        check("the failure is not counted as a send", dead.stats()["sent"] == 0)


def test_replay():
    print("\nreplay — the spool catching up")
    with tempfile.TemporaryDirectory() as d:
        off = Exporter(Config(spool_dir=d))
        for name in ("a", "b", "c"):
            off.export("traces", trace_payload([{"name": name}], off.cfg))
        off.export("logs", log_payload([{"body": {"stringValue": "x"}}], off.cfg))

        live = FakeHTTP(Config(endpoint="https://signoz.example:443", key="K", spool_dir=d))
        out = replay(live)
        check("every spooled trace payload was posted",
              out["traces"] == {"total": 3, "sent": 3, "failed": 0}, str(out))
        check("logs replayed too", out["logs"]["sent"] == 1, str(out))
        check("each line went to its own signal path",
              sorted({u for u, _, _ in live.posts}) ==
              ["https://signoz.example:443/v1/logs", "https://signoz.example:443/v1/traces"])
        check("the spool is consumed on success", read_spool(live, "traces") == [])
        check("replaying an empty spool is a no-op", replay(live) == {})

        off2 = Exporter(Config(spool_dir=d))
        off2.export("traces", trace_payload([{"name": "d"}], off2.cfg))
        dead = FakeHTTP(Config(endpoint="https://signoz.example:443", spool_dir=d), boom=True)
        out2 = replay(dead)
        check("a failed replay reports the failure",
              out2["traces"]["failed"] == 1, str(out2))
        check("and leaves the payload in the spool to try again",
              len(read_spool(dead, "traces")) == 1)

        check("replay with no endpoint refuses rather than pretending",
              _raises(lambda: replay(Exporter(Config(spool_dir=d))), ValueError))


def test_end_to_end():
    print("\nend to end — one run, offline, read back off disk")
    with tempfile.TemporaryDirectory() as d:
        configure(Config(spool_dir=d))
        t = tracer()
        with t.span("factory_run", {"run.id": 1042}) as root:
            trace_id = root.trace_id
            t.log("lane started", "INFO")
            with gate("physics.bend", {"material": "PETG"}) as g:
                g.fail(0.72, "3.2 mm web at FoS 1.5")
            scrape_repair("mcmaster", "reselect")
            human_escalation("two repairs in one run", "rishith")
        t.flush(2.0)
        export_metrics()

        # Filter to this run's trace. Earlier tests share the process-wide
        # tracer, and a test that assumes it owns the spool is a test that
        # passes or fails depending on what ran before it.
        everything = collect_spans(read_spool(Exporter(Config(spool_dir=d)), "traces"))
        spans = [s for s in everything if s["traceId"] == trace_id]
        by_name = {s["name"] for s in spans}
        check("the run span is on disk", "factory_run" in by_name, str(sorted(by_name)))
        check("so is the gate", "gate.physics.bend" in by_name)
        check("and the gate.fail event", GATE_FAIL in by_name)
        check("and the scrape repair", SCRAPE_REPAIR in by_name)
        check("and the escalation", HUMAN_ESCALATION in by_name)
        check("the run is exactly five spans, all on one trace",
              len(spans) == 5, str(sorted(by_name)))

        gspan = named(spans, "gate.physics.bend")[0]
        fspan = named(spans, GATE_FAIL)[0]
        check("the failed gate is red", gspan["status"]["code"] == ERROR)
        check("the gate records its verdict as an attribute",
              attr(gspan, "gate.result") == "fail")
        check("the failure event hangs off the gate that failed",
              fspan["parentSpanId"] == gspan["spanId"])
        check("the event is findable by event.kind alone",
              attr(fspan, "event.kind") == GATE_FAIL)

        logs = [r for p in read_spool(Exporter(Config(spool_dir=d)), "logs")
                for rl in p["resourceLogs"] for sl in rl["scopeLogs"]
                for r in sl["logRecords"] if r.get("traceId") == trace_id]
        check("the log record joins the same trace", len(logs) == 1, str(len(logs)))

        mp = read_spool(Exporter(Config(spool_dir=d)), "metrics")
        names = {m["name"] for p in mp for rm in p["resourceMetrics"]
                 for sm in rm["scopeMetrics"] for m in sm["metrics"]}
        check("gate outcomes were exported", "daisy.gate.runs" in names, str(sorted(names)))
        check("gate durations were exported", "daisy.gate.duration" in names)
        check("the event counter was exported", "daisy.events" in names)
        check("the whole metrics payload is json-serialisable",
              isinstance(json.dumps(mp[0]), str))


def test_cli():
    print("\ncli — the command a judge actually types")
    from . import cli

    def run(argv):
        """Run a subcommand, keeping its output out of this suite's."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(argv)
        return rc, buf.getvalue()

    with tempfile.TemporaryDirectory() as d:
        # Clear any real credentials first. A test suite that reaches a live
        # endpoint because the developer had one exported is a test suite that
        # fails on the venue wifi and nowhere else.
        saved = {k: os.environ.pop(k, None) for k in
                 ("SIGNOZ_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT", "SIGNOZ_INGESTION_KEY")}
        os.environ["DAISY_OBS_SPOOL"] = d
        try:
            rc, out = run(["selftest"])
            check("selftest succeeds with nothing configured", rc == 0, "rc=%d" % rc)
            check("and says it is offline", "OFFLINE" in out, out[:120])
            check("and reports the file each batch landed in",
                  out.count(d) >= 3, out[:200])
            check("and prints the trace id to search for", "trace id" in out)
            for signal in ("traces", "metrics", "logs"):
                check("selftest produced %s" % signal,
                      os.path.exists(os.path.join(d, signal + ".jsonl")))

            rc, out = run(["tail", "-n", "50"])
            check("tail reads the spool back", rc == 0, "rc=%d" % rc)
            check("tail shows the failure event", "<gate.fail>" in out, out[:400])
            check("tail shows the escalation", "<human.escalation>" in out)
            check("tail renders metrics as well as spans",
                  "daisy.gate.runs" in out and "daisy.events" in out)
            check("tail renders the log records", "selftest complete" in out)

            rc, out = run(["tail", "--signal", "traces"])
            check("tail can filter to one signal",
                  rc == 0 and "daisy.gate.runs" not in out)

            rc, out = run(["replay"])
            check("replay refuses when there is no endpoint", rc == 2, "rc=%d" % rc)
            check("and says what to set", "SIGNOZ_ENDPOINT" in out, out[:200])
        finally:
            os.environ.pop("DAISY_OBS_SPOOL", None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


def main():
    print("observability — test suite")
    # Several tests exercise the process-wide tracer, which is the thing real
    # callers use. Point it at a sandbox first so a test run never leaves
    # telemetry in the repo's own spool.
    sandbox = tempfile.mkdtemp(prefix="daisy-obs-test-")
    configure(Config(spool_dir=sandbox))
    test_config()
    test_encoding()
    test_ids_and_spans()
    test_logs()
    test_batching()
    test_metrics()
    test_events()
    test_offline_spool()
    test_live_and_fallback()
    test_replay()
    test_end_to_end()
    test_cli()
    # Drain before the sandbox goes away, so the atexit flush has nothing left
    # to write into a directory that no longer exists.
    configure(Config(spool_dir=sandbox))
    tracer().flush(2.0)
    # This process recorded a few hundred fake samples. Nobody wants them
    # shipped anywhere when the interpreter exits.
    atexit.unregister(REGISTRY.export)
    shutil.rmtree(sandbox, ignore_errors=True)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
