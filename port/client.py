"""
The one place in the factory that talks to Port over a network.

WHY this file exists
--------------------
Every other module in this repo is deterministic and offline. This one is not:
it crosses a network, so it is the only place a dead venue wifi, an expired
token or a rate limit can take a run down. Isolating that means the rest of the
package treats Port as a function call, and means there is exactly one file to
read when something goes wrong in front of a judge.

Two modes, and it says which one ran on every single response:

    live    PORT_CLIENT_ID and PORT_CLIENT_SECRET are set. Real HTTPS, real JWT.
    dry     no credentials, or PORT_DRY=1. Every request is appended to
            port/spool/<run>.jsonl exactly as it would have been sent, and
            reads are answered by replaying that spool.

Dry mode is deliberately NOT a mock that returns 200 for everything. A GET for
something the spool has never seen returns 404, because that is what makes the
factory's central claim testable rather than rhetorical: take the record away
and the plan lookup fails, so no agent starts. A mock that always succeeded
would quietly turn the approval gate back into a notification.

What it deliberately does NOT do:

    - no connection pooling, no keep-alive, no async. A run is tens of
      requests, not thousands. urllib is enough, and urllib is stdlib.
    - no random jitter in the backoff. One client on one laptop: a schedule the
      test suite can assert is worth more here than thundering-herd protection.
      With N concurrent workers you want the jitter back.
    - no validation of Port's response shapes. Port owns those; a second copy
      here would be a second source of truth, and it would drift.
    - no token on disk. A JWT on a hackathon laptop is a liability and the
      exchange costs one request.

Zero third-party dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# api.port.io is the current canonical host and api.us.port.io is the US
# region; api.getport.io still serves the same API and is what the integration
# was written against. Override with PORT_BASE_URL rather than editing this.
DEFAULT_BASE = "https://api.getport.io/v1"

# Every endpoint path in one dict, on purpose. If Port moves one, this is the
# only line that changes; nothing else in the package ever spells a URL.
ROUTES = {
    "token":              ("POST",  "/auth/access_token"),
    "blueprint_create":   ("POST",  "/blueprints"),
    "blueprint_get":      ("GET",   "/blueprints/{blueprint}"),
    "blueprint_update":   ("PATCH", "/blueprints/{blueprint}"),
    "scorecard_create":   ("POST",  "/blueprints/{blueprint}/scorecards"),
    "entity_create":      ("POST",  "/blueprints/{blueprint}/entities"),
    "entity_get":         ("GET",   "/blueprints/{blueprint}/entities/{entity}"),
    "entity_update":      ("PATCH", "/blueprints/{blueprint}/entities/{entity}"),
    "action_create":      ("POST",  "/actions"),
    "action_run_create":  ("POST",  "/actions/{action}/runs"),
    "action_run_get":     ("GET",   "/actions/runs/{run}"),
    "action_run_update":  ("PATCH", "/actions/runs/{run}"),
    "action_run_approve": ("PATCH", "/actions/runs/{run}/approval"),
}

# Port's entity POST takes `upsert` as an explicitly-required query parameter,
# so it is always sent rather than left to a server-side default.
UPSERT = {"upsert": "true", "merge": "true"}

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
RETRY_AFTER_CAP_S = 30.0     # a server asking us to wait five minutes is a stop, not a retry
TIMEOUT_S = 15.0
TOKEN_SKEW_S = 60.0          # refresh early; a token that expires mid-run reads as a 401
DEFAULT_TOKEN_TTL_S = 3600.0
USER_AGENT = "daisy-factory/1.0 (+https://github.com/rishith-c/daisy)"

# Port documents no Retry-After. It sends x-ratelimit-reset (seconds until the
# window resets), so that is the header the backoff actually obeys; Retry-After
# is still read first in case a proxy in front of it sets one.
RETRY_HEADERS = ("retry-after", "x-ratelimit-reset")

# Any value under one of these keys is masked before a request reaches the
# spool. The spool is a file on a laptop that ends up on a projector.
SECRET_KEYS = frozenset({
    "clientsecret", "client_secret", "secret", "password", "token",
    "accesstoken", "access_token", "authorization", "apikey", "api_key",
})
MASK = "[redacted]"

SPOOL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spool")
GENESIS = "0" * 32
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

class PortError(Exception):
    """A request Port refused, or could not be made at all."""

    def __init__(self, status: int, path: str, body):
        self.status, self.path, self.body = status, path, body
        detail = body
        if isinstance(body, dict):
            detail = body.get("message") or body.get("error") or json.dumps(body)[:200]
        super().__init__("%s %s: %s" % (status, path, detail))


class AuthError(PortError):
    """Credentials were present and Port rejected them.

    Kept distinct from PortError because the operator response is different:
    every other failure is worth a retry, this one never is.
    """


class TransportError(PortError):
    """The request never got a status back — DNS, TLS, timeout, closed socket."""


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------

class UrllibTransport:
    """The real one, split out purely so tests can inject a fake.

    A transport is a plain callable:

        (method, url, body_bytes|None, headers, timeout) -> (status, headers, body_bytes)

    That signature is the whole seam. Anything obeying it — a recorder, a
    failure injector, the fake in test_port.py — drops in without the client
    knowing.
    """

    def __call__(self, method, url, body, headers, timeout):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            # A 4xx/5xx is still a response, and its status is the entire input
            # to the retry policy — surfacing it as a transport failure would
            # turn a retryable 503 into a hard stop.
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
        except Exception as e:                      # URLError, socket.timeout, ssl errors
            raise TransportError(0, url, str(e)) from e


@dataclass(frozen=True)
class Response:
    status: int
    body: dict
    headers: dict
    mode: str                  # "live" | "dry" — never inferred by the caller
    attempts: int = 1
    served_from: str = ""      # dry reads say which spool record answered them

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

class PortClient:
    """A Port REST client small enough to read in one sitting."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 *, base: str | None = None, transport=None, run_id: str = "session",
                 spool: str | None = None, timeout: float = TIMEOUT_S,
                 sleep=time.sleep, clock=time.time, force_dry: bool | None = None):
        self.base = (base or os.environ.get("PORT_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self._id = client_id if client_id is not None else os.environ.get("PORT_CLIENT_ID", "")
        self._secret = (client_secret if client_secret is not None
                        else os.environ.get("PORT_CLIENT_SECRET", ""))
        forced = force_dry
        if forced is None:
            forced = os.environ.get("PORT_DRY", "").lower() not in ("", "0", "false", "no")
        # Credentials decide the mode. There is no third state and no partial
        # mode: half a credential is dry, so a typo in one env var can never
        # produce a run that claims to be live and is not.
        self.mode = "dry" if (forced or not (self._id and self._secret)) else "live"
        self.transport = transport or UrllibTransport()
        self.run_id = run_id
        self.spool = spool or os.path.join(SPOOL_DIR, "%s.jsonl" % _safe(run_id))
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._token = ""
        self._token_exp = 0.0
        self.sent = 0              # transport calls actually made; 0 forever in dry mode

    # -- naming ------------------------------------------------------------

    def __repr__(self) -> str:
        return "<PortClient mode=%s base=%s run=%s>" % (self.mode, self.base, self.run_id)

    def describe(self) -> dict:
        """What ran, in a form the CLI can print verbatim.

        Every command prints this. Nothing in the package is allowed to report
        a result without saying which mode produced it.
        """
        return {
            "mode": self.mode,
            "base": self.base if self.mode == "live" else None,
            "spool": self.spool,
            "requests_sent": self.sent,
            "note": ("live calls to Port" if self.mode == "live" else
                     "NO network call was made — requests were spooled and reads "
                     "replayed from %s" % self.spool),
        }

    # -- auth ---------------------------------------------------------------

    def token(self) -> str:
        """Exchange credentials for a JWT, cached until shortly before expiry."""
        if self.mode != "live":
            raise PortError(0, ROUTES["token"][1], "dry mode never mints a token")
        if self._token and self._clock() < self._token_exp - TOKEN_SKEW_S:
            return self._token
        r = self._send("POST", self._url(ROUTES["token"][1]),
                       {"clientId": self._id, "clientSecret": self._secret},
                       auth=False)
        self._write(r, "POST", ROUTES["token"][1], {"clientId": self._id, "clientSecret": MASK},
                    resource="auth:token")
        if r.status in (401, 403):
            raise AuthError(r.status, ROUTES["token"][1], r.body)
        if not r.ok:
            raise PortError(r.status, ROUTES["token"][1], r.body)
        tok = r.body.get("accessToken") or r.body.get("access_token") or ""
        if not tok:
            raise AuthError(r.status, ROUTES["token"][1], "no accessToken in the response")
        ttl = r.body.get("expiresIn")
        exp = self._clock() + float(ttl) if ttl else (_jwt_expiry(tok) or
                                                     self._clock() + DEFAULT_TOKEN_TTL_S)
        self._token, self._token_exp = tok, exp
        return tok

    # -- verbs --------------------------------------------------------------

    def call(self, route: str, *, body=None, params=None, **path_args) -> Response:
        """Invoke a named route from ROUTES. The only entry point the factory uses.

        Going through names rather than paths is what lets dry mode answer a
        read: the route plus its arguments name a *resource*, and a resource is
        something the spool can be replayed for.
        """
        method, template = ROUTES[route]
        path = template.format(**path_args)
        return self.request(method, path, body=body, params=params,
                            resource=_resource(route, path_args, body, path))

    def get(self, path: str, params=None, resource: str | None = None) -> Response:
        return self.request("GET", path, params=params, resource=resource)

    def post(self, path: str, body, params=None, resource: str | None = None) -> Response:
        return self.request("POST", path, body=body, params=params, resource=resource)

    def patch(self, path: str, body, params=None, resource: str | None = None) -> Response:
        return self.request("PATCH", path, body=body, params=params, resource=resource)

    def request(self, method: str, path: str, body=None, params=None,
                resource: str | None = None) -> Response:
        resource = resource or ("path:" + path)
        if self.mode == "dry":
            return self._dry(method, path, body, resource)
        r = self._send(method, self._url(path, params), body,
                       headers={"Authorization": "Bearer " + self.token()})
        self._write(r, method, path, body, resource)
        return r

    # -- the network --------------------------------------------------------

    def _url(self, path: str, params=None) -> str:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _send(self, method: str, url: str, body=None, headers=None, auth=True) -> Response:
        """One request, retried on 429/5xx and on a dead socket.

        Backoff doubles from 0.5 s and is overridden by whatever the server
        asks for. A 4xx that is not 429 is never retried: the request is wrong
        and sending it again just wastes the window.
        """
        data = json.dumps(body).encode("utf-8") if body is not None else None
        h = {"Content-Type": "application/json", "Accept": "application/json",
             "User-Agent": USER_AGENT}
        h.update(headers or {})
        delay = BACKOFF_BASE_S
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                status, rh, raw = self.transport(method, url, data, h, self.timeout)
                self.sent += 1
            except TransportError:
                if last:
                    raise
                self._sleep(delay)
                delay = min(delay * 2.0, BACKOFF_CAP_S)
                continue
            if status in RETRY_STATUS and not last:
                self._sleep(_retry_after(rh, delay))
                delay = min(delay * 2.0, BACKOFF_CAP_S)
                continue
            return Response(status, _decode(raw), rh, "live", attempt)
        raise TransportError(0, url, "exhausted %d attempts" % MAX_ATTEMPTS)  # unreachable

    # -- the spool ----------------------------------------------------------

    def _write(self, r: Response, method: str, path: str, body, resource: str) -> None:
        """Append one hash-chained record of what was sent and what came back.

        Live runs are spooled too. The spool is the factory's own record of the
        sequence, independent of Port's copy — which is the only version an
        auditor can check without credentials.
        """
        append(self.spool, {
            "ts": round(self._clock(), 3),
            "mode": r.mode,
            "method": method,
            "path": path,
            "resource": resource,
            "status": r.status,
            "attempts": r.attempts,
            "body": _redact(body),
        })

    def _dry(self, method: str, path: str, body, resource: str) -> Response:
        """Serve a request from the spool instead of the network.

        Writes are recorded and echoed back in Port's envelope shape. Reads are
        answered by replaying every write against the same resource in order —
        POST then PATCH merges exactly the way Port's `merge=true` upsert does.
        A resource with no recorded write is a 404, never an empty success.
        """
        kind = resource.split(":", 1)[0]
        envelope = kind if kind in ("entity", "blueprint", "scorecard", "action") else "result"
        if method in ("POST", "PUT", "PATCH"):
            status = 201 if method == "POST" else 200
            r = Response(status, {"ok": True, "dry": True, envelope: body}, {}, "dry")
            self._write(r, method, path, body, resource)
            return r
        state = replay(self.spool, resource)
        if state is None:
            r = Response(404, {"ok": False, "dry": True, "error": "not_found",
                               "message": "no record of %s in %s" % (resource, self.spool)},
                         {}, "dry", served_from="")
        else:
            r = Response(200, {"ok": True, "dry": True, envelope: state}, {}, "dry",
                         served_from=self.spool)
        self._write(r, method, path, body, resource)
        return r


# ---------------------------------------------------------------------------
# spool format — an append-only, hash-chained jsonl of every request
# ---------------------------------------------------------------------------

def append(path: str, record: dict) -> dict:
    """Chain one record onto the spool and return it as written.

    Each line carries the hash of the line before it, so a record cannot be
    edited or removed after the fact without breaking every hash downstream.

    The chain assumes one writer at a time. Two processes appending in the same
    instant will show up as a broken link — which is the correct failure mode:
    verify() reports it rather than repairing it.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    rows = read(path)
    record = dict(record)
    record["seq"] = len(rows) + 1
    record["prev"] = rows[-1]["hash"] if rows else GENESIS
    record["hash"] = digest(record)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def digest(record: dict) -> str:
    """blake2b over the canonical record, excluding the hash field itself."""
    payload = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.blake2b(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"),
                           digest_size=16).hexdigest()


def read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def verify(path: str) -> dict:
    """Recompute the chain. Returns where it first breaks, if it does."""
    rows = read(path)
    prev = GENESIS
    for i, r in enumerate(rows):
        if r.get("prev") != prev or r.get("hash") != digest(r):
            return {"intact": False, "records": len(rows), "broken_at": i + 1,
                    "head": prev}
        prev = r["hash"]
    return {"intact": True, "records": len(rows), "broken_at": None, "head": prev}


def replay(path: str, resource: str) -> dict | None:
    """Fold every recorded write for one resource into its current state."""
    state = None
    for r in read(path):
        if r.get("resource") != resource or r.get("method") not in ("POST", "PUT", "PATCH"):
            continue
        if not (200 <= int(r.get("status", 0)) < 300):
            continue
        body = r.get("body")
        if not isinstance(body, dict):
            continue
        state = body if state is None else _merge(state, body)
    return state


def _merge(base: dict, over: dict) -> dict:
    """Port's PATCH semantics: top-level replace, one level of dict merge.

    `properties` and `relations` merge key-by-key so a patch of one field does
    not silently drop the rest of the entity; everything else is replaced.
    """
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _resource(route: str, args: dict, body, path: str) -> str:
    """Name the thing a request acts on, so a later read can find it."""
    bp = args.get("blueprint")
    ident = (body or {}).get("identifier") if isinstance(body, dict) else None
    if route == "entity_create":
        return "entity:%s:%s" % (bp, ident)
    if route in ("entity_get", "entity_update"):
        return "entity:%s:%s" % (bp, args.get("entity"))
    if route == "blueprint_create":
        return "blueprint:%s" % ident
    if route in ("blueprint_get", "blueprint_update"):
        return "blueprint:%s" % bp
    if route == "scorecard_create":
        return "scorecard:%s:%s" % (bp, ident)
    if route == "action_create":
        return "action:%s" % ident
    if route in ("action_run_get", "action_run_update", "action_run_approve"):
        return "action:run:%s" % args.get("run")
    if route == "action_run_create":
        return "action:run:%s" % args.get("action")
    return "path:" + path


def _redact(obj):
    if isinstance(obj, dict):
        return {k: (MASK if k.lower() in SECRET_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _decode(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", "replace")[:2000]}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _retry_after(headers: dict, fallback: float) -> float:
    for name in RETRY_HEADERS:
        v = headers.get(name) or headers.get(name.title())
        if v is None:
            continue
        try:
            return min(max(float(v), 0.0), RETRY_AFTER_CAP_S)
        except (TypeError, ValueError):
            continue          # an HTTP-date Retry-After is legal; the fallback covers it
    return fallback


def _jwt_expiry(tok: str) -> float | None:
    """Read `exp` out of the JWT payload as a fallback when expiresIn is absent.

    The signature is deliberately not checked. We are not the audience for this
    token — Port verifies it — and pretending to validate it here would be
    security theatre. All this reads is the expiry, and a wrong answer costs one
    extra token exchange.
    """
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


def _safe(name: str) -> str:
    return _UNSAFE.sub("-", str(name)).strip("-") or "session"
