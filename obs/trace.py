"""
A tracer small enough to audit — spans, a context stack, and one flush thread.

A factory run is a tree: a lane spawns gates, a failed gate spawns a repair,
a repair reruns the gate. Flat log lines cannot express that, and the thing a
judge wants to click is the span that went red and the four spans underneath
it. So spans are the primary record here and logs are the afterthought, which
is the opposite of how most projects start.

    IS      W3C-shaped ids, parent/child nesting per thread, wall-clock start
            and monotonic duration, OK/ERROR status, exception recording, and
            a bounded background batcher
    IS NOT  sampling, span links, remote parent extraction from `traceparent`,
            or context propagation across processes

The batcher's contract is the important part and it is one sentence: emitting
a span never blocks and never raises. When the queue is full, spans are
dropped and counted. A build system that stalls because its telemetry queue
backed up is a worse outcome than a missing span, every time.

Logs live in this module rather than next to the exporter because the only
interesting thing about a log record here is that it carries the trace and
span id of whatever was running when it was written.

Zero third-party dependencies.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field

from .otlp import Delivery, attributes, exporter, log_payload, trace_payload

# OTLP SpanKind. Everything the factory does is INTERNAL work in one process;
# the rest are here so a future HTTP client span does not need a new encoder.
SPAN_KIND = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}

# OTLP StatusCode.
UNSET, OK, ERROR = 0, 1, 2

# OTLP SeverityNumber, the levels a build system actually uses.
SEVERITY = {"DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}

# A stack trace per span is the difference between a 4 KB payload and a 400 KB
# one when a loop fails 100 times. Keep the head, which is where the cause is.
STACK_CHARS = 2000


def new_trace_id() -> str:
    """16 random bytes, hex. W3C forbids the all-zero id; urandom will not
    produce it before the sun goes out."""
    return os.urandom(16).hex()


def new_span_id() -> str:
    return os.urandom(8).hex()


# ---------------------------------------------------------------------------
# the span
# ---------------------------------------------------------------------------

@dataclass(eq=False)   # identity, not value: two gates named the same are two spans
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str = ""
    kind: str = "internal"
    start_ns: int = 0
    end_ns: int = 0
    status: int = UNSET
    message: str = ""
    attrs: dict = field(default_factory=dict)
    events: list = field(default_factory=list)   # (unix_nanos, name, attrs)

    def set(self, key: str, value) -> "Span":
        self.attrs[key] = value
        return self

    def event(self, name: str, attrs: dict | None = None) -> "Span":
        self.events.append((time.time_ns(), name, dict(attrs or {})))
        return self

    def error(self, exc: BaseException) -> "Span":
        """Record an exception the way a trace viewer expects to find it."""
        self.status = ERROR
        self.message = "%s: %s" % (type(exc).__name__, exc)
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.event("exception", {
            "exception.type": type(exc).__name__,
            "exception.message": str(exc),
            "exception.stacktrace": stack[:STACK_CHARS],
        })
        return self

    def to_otlp(self) -> dict:
        out = {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "name": self.name,
            "kind": SPAN_KIND.get(self.kind, 1),
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns),
            "attributes": attributes(self.attrs),
            "status": {"code": self.status},
        }
        if self.parent_id:
            out["parentSpanId"] = self.parent_id
        if self.message:
            out["status"]["message"] = self.message
        if self.events:
            out["events"] = [{"timeUnixNano": str(ts), "name": n,
                              "attributes": attributes(a)} for ts, n, a in self.events]
        return out


# ---------------------------------------------------------------------------
# context — per thread, because lanes run in parallel
# ---------------------------------------------------------------------------

_LOCAL = threading.local()


def _stack() -> list:
    s = getattr(_LOCAL, "spans", None)
    if s is None:
        s = _LOCAL.spans = []
    return s


def current() -> Span | None:
    s = _stack()
    return s[-1] if s else None


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------

class _Flush:
    """A marker the caller can wait on, so `flush()` means flushed."""

    def __init__(self):
        self.done = threading.Event()


class Batcher:
    """A bounded queue and one daemon thread that drains it. Traces and logs
    only — metrics are aggregated state, not a stream, and are exported from
    the registry instead.

    `start()` is explicit rather than automatic on first submit: a Batcher
    nobody started is a plain bounded queue, which is the only way to test
    the drop path without racing the drain.
    """

    def __init__(self, exp=None, max_queue: int = 2048, max_batch: int = 128,
                 interval: float = 2.0):
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.max_batch = max_batch
        self.interval = interval
        self.dropped = 0
        self.submitted = {"traces": 0, "logs": 0}
        # Bounded: a long run would otherwise accumulate one Delivery per
        # batch forever, and nothing ever reads more than the recent tail.
        self.exports: deque[Delivery] = deque(maxlen=64)
        self._exp = exp
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- caller side --------------------------------------------------------

    def submit(self, signal: str, obj: dict) -> bool:
        """Never blocks, never raises. False means the queue was full."""
        try:
            self.q.put_nowait((signal, obj))
            with self._lock:
                self.submitted[signal] += 1
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until everything queued so far has been handed to the exporter."""
        if self._thread is None:
            self._ship(self._drain())
            return True
        marker = _Flush()
        try:
            self.q.put(marker, timeout=timeout)
        except queue.Full:
            return False
        return marker.done.wait(timeout)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="daisy-obs", daemon=True)
        self._thread.start()

    # -- worker side --------------------------------------------------------

    def _run(self) -> None:
        buf: dict[str, list] = {"traces": [], "logs": []}
        while True:
            try:
                item = self.q.get(timeout=self.interval)
            except queue.Empty:
                self._ship(buf)
                continue
            if isinstance(item, _Flush):
                self._drain_into(buf)
                self._ship(buf)
                item.done.set()
                continue
            signal, obj = item
            buf[signal].append(obj)
            if sum(len(v) for v in buf.values()) >= self.max_batch:
                self._ship(buf)

    def _drain_into(self, buf: dict) -> None:
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, _Flush):
                item.done.set()
                continue
            buf[item[0]].append(item[1])

    def _drain(self) -> dict:
        buf: dict[str, list] = {"traces": [], "logs": []}
        self._drain_into(buf)
        return buf

    def _ship(self, buf: dict) -> None:
        """Hand each non-empty signal to the exporter and clear it.

        Wrapped whole: a transport that throws must not kill the only thread
        that will ever drain this queue.
        """
        exp = self._exp or exporter()
        for signal, items in buf.items():
            if not items:
                continue
            try:
                if signal == "traces":
                    payload = trace_payload(items, exp.cfg)
                else:
                    payload = log_payload(items, exp.cfg)
                d = exp.export(signal, payload)
                with self._lock:
                    self.exports.append(d)
            except Exception:
                pass
            finally:
                items.clear()

# ---------------------------------------------------------------------------
# the tracer
# ---------------------------------------------------------------------------

class Tracer:
    """Spans in, batched OTLP out."""

    def __init__(self, exp=None, batcher: Batcher | None = None):
        self.batcher = batcher or Batcher(exp=exp)
        self.batcher.start()

    @contextlib.contextmanager
    def span(self, name: str, attrs: dict | None = None, kind: str = "internal"):
        parent = current()
        s = Span(name=name,
                 trace_id=parent.trace_id if parent else new_trace_id(),
                 span_id=new_span_id(),
                 parent_id=parent.span_id if parent else "",
                 kind=kind,
                 start_ns=time.time_ns(),
                 attrs=dict(attrs or {}))
        # Wall clock for the absolute timestamp OTLP wants, monotonic for the
        # length. An NTP step mid-gate would otherwise report a negative
        # duration, and a negative duration in a flame graph is unreadable.
        t0 = time.perf_counter_ns()
        stack = _stack()
        stack.append(s)
        try:
            yield s
        except BaseException as exc:
            s.error(exc)
            raise
        finally:
            if stack and stack[-1] is s:
                stack.pop()
            s.end_ns = s.start_ns + (time.perf_counter_ns() - t0)
            if s.status == UNSET:
                s.status = OK
            self.emit(s)

    def emit(self, s: Span) -> bool:
        return self.batcher.submit("traces", s.to_otlp())

    def log(self, body: str, severity: str = "INFO", attrs: dict | None = None) -> bool:
        """A log record, correlated to whatever span is running."""
        now = str(time.time_ns())
        sev = severity.upper()
        rec = {
            "timeUnixNano": now,
            "observedTimeUnixNano": now,
            "severityNumber": SEVERITY.get(sev, 9),
            "severityText": sev,
            "body": {"stringValue": str(body)},
            "attributes": attributes(attrs or {}),
        }
        s = current()
        if s is not None:
            rec["traceId"] = s.trace_id
            rec["spanId"] = s.span_id
        return self.batcher.submit("logs", rec)

    def flush(self, timeout: float = 5.0) -> bool:
        return self.batcher.flush(timeout)


# ---------------------------------------------------------------------------
# the process tracer
# ---------------------------------------------------------------------------

_TRACER: Tracer | None = None
_TLOCK = threading.Lock()


def tracer() -> Tracer:
    """Built on first use, never at import, and flushed at exit.

    The atexit hook is what makes a one-shot script honest: a factory run that
    ends without an explicit flush would otherwise throw away its last batch,
    which is reliably the batch containing the failure.
    """
    global _TRACER
    if _TRACER is None:
        with _TLOCK:
            if _TRACER is None:
                _TRACER = Tracer()
                atexit.register(_TRACER.flush, 3.0)
    return _TRACER


def span(name: str, attrs: dict | None = None, kind: str = "internal"):
    return tracer().span(name, attrs, kind)


def log(body: str, severity: str = "INFO", attrs: dict | None = None) -> bool:
    return tracer().log(body, severity, attrs)


def flush(timeout: float = 5.0) -> bool:
    return tracer().flush(timeout)
