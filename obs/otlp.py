"""
OTLP over HTTP/JSON — the whole exporter, in one file, with no protobuf.

Why this exists at all. SigNoz speaks OTLP, and the documented way to speak
OTLP from Python is opentelemetry-sdk plus a protobuf runtime plus grpcio:
tens of megabytes of wheels and a compiler on the unlucky machines. Daisy's
entire claim is that it runs on a judge's laptop with `python3` and nothing
else, so that trade is not available. OTLP's JSON encoding is a first-class
part of the specification, `json` and `urllib` are stdlib, and the encoding is
small enough to read in one sitting — so it lives here instead.

    IS      the OTLP/HTTP JSON encoding for traces, metrics and logs, one
            POST per signal, and a newline-delimited spool that makes the
            offline path indistinguishable from the live one to the caller
    IS NOT  gRPC, protobuf, gzip, retry/backoff, sampling, or W3C
            `traceparent` propagation across process boundaries

The omissions are deliberate, and retry most of all. A retry loop inside the
flush thread parks every later batch behind a dead endpoint, which is the
exact failure mode a hackathon wifi network produces. The spool already *is*
the retry buffer: a failed POST becomes a spooled line, and `obs.cli replay`
ships it when the network comes back.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import math
import os
import platform
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from dataclasses import dataclass

VERSION = "0.1.0"
DEFAULT_SERVICE = "daisy"
SCOPE = {"name": "daisy.obs", "version": VERSION}

# The three OTLP/HTTP paths. Order is also the order `replay` walks them.
SIGNALS = {"traces": "/v1/traces", "metrics": "/v1/metrics", "logs": "/v1/logs"}

HERE = os.path.dirname(os.path.abspath(__file__))
SPOOL_DIR = os.path.join(HERE, "spool")

# Where a batch ended up. `dest` is one of live | spool | drop.
Delivery = namedtuple("Delivery", "dest target error")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def _base(url: str) -> str:
    """Normalise whatever got pasted into the env var down to an origin.

    People copy the full signal URL out of the SigNoz docs, so appending
    `/v1/traces` to a value that already ends in `/v1/traces` is the most
    likely way to earn a 404 at 3am. Strip it here rather than debug it there.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    for suffix in SIGNALS.values():
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    if "://" not in u:
        u = "https://" + u
    return u.rstrip("/")


@dataclass(frozen=True)
class Config:
    """Everything the exporter needs, resolved once.

    `endpoint` empty is not an error — it is offline mode, which is a
    supported way to run the factory, not a degraded one.
    """
    endpoint: str = ""
    key: str = ""
    service: str = DEFAULT_SERVICE
    spool_dir: str = SPOOL_DIR
    timeout: float = 5.0

    @property
    def live(self) -> bool:
        """A key is optional: a self-hosted collector on localhost wants none."""
        return bool(self.endpoint)

    def url(self, signal: str) -> str:
        return self.endpoint + SIGNALS[signal]

    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["signoz-ingestion-key"] = self.key
        return h

    def source(self) -> str:
        """Human-readable account of where the endpoint came from, for the CLI."""
        if not self.endpoint:
            return "unset — offline, spooling to %s" % self.spool_dir
        return self.endpoint


def from_env(env: dict | None = None) -> Config:
    """Read config from the environment.

    `OTEL_EXPORTER_OTLP_ENDPOINT` is honoured as an alias so a machine already
    configured for some other OTLP backend needs no Daisy-specific setup, but
    `SIGNOZ_ENDPOINT` wins when both are set — the specific name is the one a
    person typed on purpose.
    """
    env = os.environ if env is None else env
    ep = env.get("SIGNOZ_ENDPOINT") or env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or ""
    try:
        timeout = float(env.get("DAISY_OBS_TIMEOUT") or 5.0)
    except ValueError:
        timeout = 5.0
    return Config(
        endpoint=_base(ep),
        key=(env.get("SIGNOZ_INGESTION_KEY") or "").strip(),
        service=(env.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE).strip(),
        spool_dir=env.get("DAISY_OBS_SPOOL") or SPOOL_DIR,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# encoding — OTLP AnyValue and the three payload envelopes
# ---------------------------------------------------------------------------

def anyvalue(v) -> dict:
    """One Python value as an OTLP AnyValue.

    Two traps handled here. `bool` is a subclass of `int`, so it must be
    tested first or every flag ships as 0/1. And 64-bit integers are strings
    in proto3 JSON — a bare number is accepted by some collectors and
    silently truncated by others, so the spec-correct form is the only form.
    """
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        # `inf` is a legitimate margin in this codebase (a gate with zero
        # stress divides by zero on purpose), and json.dumps would emit the
        # literal `Infinity`, which is not JSON and gets the whole batch
        # rejected. Degrade the one attribute, not the payload.
        return {"doubleValue": v} if math.isfinite(v) else {"stringValue": repr(v)}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [anyvalue(x) for x in v]}}
    if v is None:
        return {"stringValue": ""}
    return {"stringValue": str(v)}


def attributes(d: dict | None) -> list:
    """Sorted, so two identical batches serialise byte-identically and a test
    can assert on a payload without chasing dict ordering."""
    return [{"key": str(k), "value": anyvalue(v)} for k, v in sorted((d or {}).items())]


def resource(cfg: Config) -> dict:
    return {"attributes": attributes({
        "service.name": cfg.service,
        "service.version": VERSION,
        "telemetry.sdk.name": "daisy-obs",
        "telemetry.sdk.language": "python",
        "host.name": platform.node(),
        "process.pid": os.getpid(),
    })}


def trace_payload(spans: list, cfg: Config) -> dict:
    return {"resourceSpans": [{"resource": resource(cfg),
                               "scopeSpans": [{"scope": SCOPE, "spans": list(spans)}]}]}


def metric_payload(metrics: list, cfg: Config) -> dict:
    return {"resourceMetrics": [{"resource": resource(cfg),
                                 "scopeMetrics": [{"scope": SCOPE, "metrics": list(metrics)}]}]}


def log_payload(records: list, cfg: Config) -> dict:
    return {"resourceLogs": [{"resource": resource(cfg),
                              "scopeLogs": [{"scope": SCOPE, "logRecords": list(records)}]}]}


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

class Exporter:
    """One POST per batch, with the spool as the failure path.

    Nothing here raises at the caller. Observability that can take down the
    thing it observes is worse than no observability, so every path out of
    `export` returns a Delivery describing what happened.
    """

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or from_env()
        self.sent = 0
        self.spooled = 0
        self.dropped = 0
        self._lock = threading.Lock()

    # -- the one entry point ------------------------------------------------

    def export(self, signal: str, payload: dict) -> Delivery:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        err = ""
        if self.cfg.live:
            url = self.cfg.url(signal)
            try:
                self._post(url, body)
                with self._lock:
                    self.sent += 1
                return Delivery("live", url, "")
            except Exception as e:                       # noqa: BLE001 — see docstring
                err = "%s: %s" % (type(e).__name__, str(e)[:160])
        try:
            path = self.spool(signal, body)
            with self._lock:
                self.spooled += 1
            return Delivery("spool", path, err)
        except Exception as e:                           # noqa: BLE001
            with self._lock:
                self.dropped += 1
            return Delivery("drop", "", err or "%s: %s" % (type(e).__name__, e))

    def stats(self) -> dict:
        with self._lock:
            return {"sent": self.sent, "spooled": self.spooled, "dropped": self.dropped}

    # -- live ---------------------------------------------------------------

    def _post(self, url: str, body: bytes) -> None:
        """POST one already-encoded payload. Raises on anything but success.

        urllib title-cases header names, so the key goes out as
        `Signoz-ingestion-key`. HTTP header names are case-insensitive
        (RFC 7230) and SigNoz's Go frontend canonicalises them anyway.
        """
        req = urllib.request.Request(url, data=body, method="POST")
        for k, v in self.cfg.headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                resp.read()
                if resp.status >= 300:
                    raise OSError("HTTP %d" % resp.status)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
            raise OSError("HTTP %d %s" % (e.code, detail)) from None

    # -- spool --------------------------------------------------------------

    def spool_path(self, signal: str) -> str:
        return os.path.join(self.cfg.spool_dir, signal + ".jsonl")

    def spool(self, signal: str, body: bytes) -> str:
        """Append one whole OTLP payload as one line.

        One payload per line, not one span per line: replay is then a literal
        re-POST of the bytes, with no re-encoding step that could disagree
        with the encoder that wrote them.
        """
        path = self.spool_path(signal)
        os.makedirs(self.cfg.spool_dir, exist_ok=True)
        with self._lock:
            with open(path, "ab") as fh:
                fh.write(body + b"\n")
        return path


# ---------------------------------------------------------------------------
# replay — the spool catching up
# ---------------------------------------------------------------------------

def read_spool(exp: Exporter, signal: str) -> list[dict]:
    """Decoded payloads, newest last. Bad lines are skipped, not fatal."""
    path = exp.spool_path(signal)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "rb") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw.decode("utf-8")))
            except ValueError:
                continue
    return out


def replay(exp: Exporter, keep: bool = False) -> dict:
    """POST every spooled payload to the live endpoint.

    A line that fails stays in the spool rather than being re-spooled, so a
    half-successful replay over a flaky connection cannot duplicate what it
    already delivered.
    """
    if not exp.cfg.live:
        raise ValueError("no endpoint configured — nothing to replay to")
    out: dict[str, dict] = {}
    for signal in SIGNALS:
        path = exp.spool_path(signal)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            lines = [l.strip() for l in fh if l.strip()]
        if not lines:
            continue
        sent, kept = 0, []
        for raw in lines:
            try:
                exp._post(exp.cfg.url(signal), raw)
                sent += 1
            except Exception:
                kept.append(raw)
        out[signal] = {"total": len(lines), "sent": sent, "failed": len(kept)}
        if not keep:
            _rewrite(path, kept)
    return out


def _rewrite(path: str, lines: list[bytes]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        for l in lines:
            fh.write(l + b"\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# the process exporter
# ---------------------------------------------------------------------------

_EXPORTER: Exporter | None = None
_ELOCK = threading.Lock()


def exporter() -> Exporter:
    """The lazily-built, process-wide exporter.

    Lazy on purpose: importing `obs` must not read the environment, touch the
    filesystem or start a thread, or every test in this repo inherits the
    developer's SigNoz credentials.
    """
    global _EXPORTER
    if _EXPORTER is None:
        with _ELOCK:
            if _EXPORTER is None:
                _EXPORTER = Exporter(from_env())
    return _EXPORTER


def configure(cfg: Config | None = None) -> Exporter:
    """Replace the process exporter. For the CLI's flags and for tests."""
    global _EXPORTER
    with _ELOCK:
        _EXPORTER = Exporter(cfg if cfg is not None else from_env())
    return _EXPORTER
