"""Tests for the Garden link and the autonomous publish path.

    python3 -m garden.test_garden

Every case builds its own credential file, consent ledger and publish ledger in
a tempdir, and every HTTP call goes through an injected transport obeying the
same five-argument contract as the real one — so what runs here is the shipping
retry loop, the shipping header assembly and the shipping JSON encoding, not a
stand-in for them. No network, no credentials, nothing read from ~/.daisy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile

from commons.consent import Ledger
from commons.store import Solution

from . import link as L
from . import remote as R

PASS, FAIL = 0, 0
TMP = ""

TOKEN = "gdn_live_2f6c9a1e4b8d7c30e5a1"     # never a real one; asserted absent everywhere
BASE = "https://garden.test"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# the fake transport — a transport, not a mock of the client
# ---------------------------------------------------------------------------

class Fake:
    """Queued responses, plus a log of exactly what was sent.

    Each queued item is (status, headers, json_body) or an Exception to raise.
    A test scripts only the responses it cares about; the rest come from
    `default`.
    """

    def __init__(self, *responses, default=(200, {}, {"id": "garden-1"})):
        self.queue = list(responses)
        self.default = default
        self.calls = []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body.decode()) if body else None})
        item = self.queue.pop(0) if self.queue else self.default
        if isinstance(item, Exception):
            raise item
        status, hdrs, payload = item
        return status, hdrs, json.dumps(payload).encode("utf-8")


class Clock:
    """Time that only moves when something sleeps. Makes backoff assertable."""

    def __init__(self, t=1700000000.0):
        self.t = t
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def workspace(name: str):
    """A credential path and a consent ledger, both in their own directory."""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "garden.json"), Ledger(os.path.join(d, "consent.json"))


def granted(led: Ledger) -> Ledger:
    led.grant("artifact", "garden")
    return led


def pair(path: str, uid="u-rishith", token=TOKEN, code="ABC123"):
    f = Fake((200, {}, {"device_id": "dev-77", "token": token, "uid": uid}))
    out = L.link(code, transport=f, base=BASE, path=path)
    return out, f


def sol(**kw) -> dict:
    d = dict(task="size a cantilever bracket web for FoS 1.5 under a 2.4 kg tip load",
             brief="the 3.2 mm web fails bending", kind="hardware",
             recipe="invert sigma = 6M/(b t^2) for t at FoS; round up",
             vendor="claude", model="claude-opus-5", tokens_cost=48000,
             gates=[{"name": "physics.bend", "passed": True, "margin": 1.5},
                    {"name": "physics.mass", "passed": True, "margin": 1.2}],
             gate_sig="physics.bend=pass|physics.mass=pass")
    d.update(kw)
    return d


def failing(**kw) -> dict:
    return sol(gates=[{"name": "physics.bend", "passed": False, "margin": 0.7},
                      {"name": "physics.mass", "passed": True, "margin": 1.2}],
               gate_sig="physics.bend=fail|physics.mass=pass", **kw)


def mode_of(path):
    return os.stat(path).st_mode & 0o777


def leaks(obj) -> bool:
    return TOKEN in json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# not linked
# ---------------------------------------------------------------------------

def test_absent_and_malformed_read_as_not_linked():
    print("\nan unreadable credential is never an authorisation")
    p, _ = workspace("absent")
    check("a missing file is not linked", L.status(p)["linked"] is False)
    check("and says which command fixes it", "garden.link pair" in L.status(p)["why"])
    check("credentials() is empty, not None", L.credentials(p) == {})
    check("autopublish defaults off when there is no file", L.autopublish(p) is False)

    open(p, "w").write("{not json at all")
    check("truncated JSON is not linked", L.status(p)["linked"] is False)
    open(p, "w").write(json.dumps(["a", "list"]))
    check("JSON of the wrong shape is not linked", L.status(p)["linked"] is False)
    open(p, "w").write(json.dumps({"uid": "u", "device_id": "d"}))
    check("a file with no token is not linked", L.status(p)["linked"] is False)
    open(p, "w").write(json.dumps({"token": TOKEN, "token_sha256": "deadbeef", "uid": "u"}))
    check("a token that disagrees with its own hash is not linked",
          L.status(p)["linked"] is False)
    open(p, "w").write(json.dumps({"token": TOKEN, "token_sha256": L.sha256(TOKEN)}))
    check("a self-consistent file is linked", L.status(p)["linked"] is True)


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------

def test_pairing_refuses_bad_codes_offline():
    print("\na malformed code is refused without a network call")
    p, _ = workspace("badcode")
    f = Fake()
    out = L.link("ABC12", transport=f, base=BASE, path=p)
    check("a five-character code is refused", out["linked"] is False)
    check("and no request was made", f.calls == [], str(f.calls))
    check("the message says what a code looks like", "6 letters or digits" in out["why"])
    check("a code with punctuation is refused",
          L.link("AB!123", transport=f, base=BASE, path=p)["linked"] is False)
    check("an empty code is refused", L.link("", transport=f, base=BASE, path=p)["linked"] is False)
    check("still no request was made", f.calls == [], str(f.calls))
    check("no credential file was created", not os.path.exists(p))


def test_pairing_stores_the_token():
    print("\npairing — one exchange, one 0600 file")
    p, _ = workspace("pairing")
    out, f = pair(p, code="abc-123 ")
    check("pairing succeeds", out["linked"] is True, str(out))
    check("it POSTs to /api/pair", f.calls[0]["url"] == BASE + "/api/pair",
          f.calls[0]["url"])
    check("formatting is stripped but case is preserved",
          f.calls[0]["body"] == {"code": "abc123"}, str(f.calls[0]["body"]))
    check("exactly one request", len(f.calls) == 1)
    check("the account comes back", out["account"] == "u-rishith")
    check("the credential file exists", os.path.exists(p))
    check("the credential file is 0600", mode_of(p) == 0o600, oct(mode_of(p)))
    check("no group or other bits at all", not (os.stat(p).st_mode & (stat.S_IRWXG | stat.S_IRWXO)))
    check("the token is on disk (it has to be)", TOKEN in open(p).read())
    check("its SHA-256 is stored beside it",
          json.load(open(p))["token_sha256"] == hashlib.sha256(TOKEN.encode()).hexdigest())
    check("the link() result never carries the token", not leaks(out), str(out))

    st = L.status(p)
    check("status is linked", st["linked"] is True)
    check("status names the account", st["account"] == "u-rishith")
    check("status shows the first 8 hex of the token hash",
          st["fingerprint"] == hashlib.sha256(TOKEN.encode()).hexdigest()[:8])
    check("the fingerprint is 8 characters", len(st["fingerprint"]) == 8)
    check("status never carries the token", not leaks(st), str(st))
    check("status carries no last publish yet", st["last_publish"] is None)
    check("autopublish is off on a fresh link", st["autopublish"] is False)


def test_pairing_degrades_and_repairs_modes():
    print("\npairing — a Garden that is not there yet, and a loosened file")
    p, _ = workspace("degrade")
    out = L.link("ABC123", transport=Fake((404, {}, {"error": "not found"})),
                 base=BASE, path=p)
    check("a 404 is 'not deployed yet', not a crash", out["linked"] is False)
    check("and says so in those terms", out.get("remote") == "unavailable", str(out))
    check("nothing was stored", not os.path.exists(p))

    out = L.link("ABC123", transport=Fake((400, {}, {"error": "code expired"})),
                 base=BASE, path=p)
    check("a refused code reports the server's reason", "code expired" in out["why"])
    check("still nothing stored", not os.path.exists(p))

    out = L.link("ABC123", transport=Fake((200, {}, {"uid": "u", "device_id": "d"})),
                 base=BASE, path=p)
    check("a 200 with no token is not a link", out["linked"] is False)

    out = L.link("ABC123", transport=Fake(L.TransportError("connection reset")),
                 base=BASE, path=p)
    check("a dead socket is reported, not raised", out["linked"] is False)
    check("and named as unreachable", out.get("remote") == "unreachable")

    pair(p)
    os.chmod(p, 0o644)
    pair(p, uid="u-second")
    check("re-linking over a loosened file restores 0600", mode_of(p) == 0o600,
          oct(mode_of(p)))
    check("re-linking switches account", L.status(p)["account"] == "u-second")
    L.set_autopublish(True, p)
    pair(p, uid="u-third")
    check("re-linking re-arms autopublish to off", L.autopublish(p) is False)


def test_directory_and_base_resolution():
    print("\nwhere the credential lives, and which server it belongs to")
    d = os.path.join(TMP, "freshdir")
    p = os.path.join(d, "garden.json")
    pair(p)
    check("the parent directory is created 0700", mode_of(d) == 0o700, oct(mode_of(d)))
    creds = L.credentials(p)
    check("the base the token came from is stored", creds["base"] == BASE)
    check("a stored base beats the environment",
          L.api_base(None, creds) == BASE)
    check("an explicit base beats the stored one",
          L.api_base("https://other.test", creds) == "https://other.test")
    check("trailing slashes are normalised away",
          L.api_base("https://other.test/", None) == "https://other.test")
    old = os.environ.get("DAISY_HOME")
    os.environ["DAISY_HOME"] = d
    try:
        check("DAISY_HOME redirects the default path", L.cred_path() == p)
    finally:
        os.environ.pop("DAISY_HOME", None)
        if old is not None:
            os.environ["DAISY_HOME"] = old


def test_unlink():
    print("\nunlink — local first, remote best-effort")
    p, _ = workspace("unlink")
    pair(p)
    f = Fake((200, {}, {"revoked": True}))
    out = L.unlink(transport=f, path=p)
    check("the file is gone", not os.path.exists(p))
    check("status reads as not linked", L.status(p)["linked"] is False)
    check("it reports the local removal", out["removed_local"] is True)
    check("it asked the server to revoke", f.calls[0]["method"] == "DELETE")
    check("with the device token as bearer",
          f.calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN)
    check("and reports the revocation", out["revoked_remotely"] is True)

    out = L.unlink(transport=Fake(), path=p)
    check("unlinking twice is not an error", out["unlinked"] is True)
    check("and touches no transport when there is nothing to revoke",
          out["revoked_remotely"] is False)

    pair(p)
    out = L.unlink(transport=Fake((404, {}, {})), path=p)
    check("a server that cannot revoke still loses the local credential",
          not os.path.exists(p) and out["revoked_remotely"] is False)
    check("and says where to finish the job", "web UI" in out["why"], out["why"])


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------

def test_unverified_is_refused_before_the_network():
    print("\nprecondition 1 — verification, decided offline")
    p, led = workspace("unverified")
    pair(p)
    granted(led)
    f = Fake()
    out = R.publish(failing(), led, live=True, transport=f, path=p)
    check("a failed gate blocks the publish", out["mode"] == "blocked", str(out))
    check("refused for verification", out["refused"] == "verification")
    check("nothing was sent", f.calls == [], str(f.calls))
    check("the failing gate is named", "physics.bend" in out["why"], out["why"])
    check("no command is offered, because none would help",
          "no command" in out["fix"], out["fix"])

    out = R.publish(sol(gates=[], gate_sig=""), led, live=True, transport=f, path=p)
    check("a solution with no gates at all is refused", out["refused"] == "verification")
    check("and says it carries no gate results", "no gate results" in out["why"])
    check("still nothing sent", f.calls == [])


def test_consent_is_required():
    print("\nprecondition 2 — consent, per scope and per target")
    p, led = workspace("consent")
    pair(p)
    f = Fake()
    out = R.publish(sol(), led, live=True, transport=f, path=p)
    check("an empty ledger blocks the publish", out["mode"] == "blocked")
    check("refused for consent", out["refused"] == "consent")
    check("nothing was sent", f.calls == [])
    check("the fix is the exact grant command", out["fix"] == R.FIX_CONSENT)
    check("which names scope and target",
          "--scope artifact --target garden" in out["fix"])

    led.grant("artifact", "makerworld")
    check("a grant for another target does not count",
          R.publish(sol(), led, live=True, transport=f, path=p)["refused"] == "consent")
    led.grant("source", "garden")
    check("a different scope does not count either",
          R.publish(sol(), led, live=True, transport=f, path=p)["refused"] == "consent")
    led.grant("artifact", "garden")
    check("the right grant clears this gate",
          R.publish(sol(), led, live=True, transport=f, path=p)["mode"] == "live")
    led.revoke("artifact", "garden")
    check("revoking blocks it again",
          R.publish(sol(task="another"), led, live=True, transport=f,
                    path=p)["refused"] == "consent")


def test_link_is_required_and_order_is_fixed():
    print("\nprecondition 3 — a linked instance, and the order of refusals")
    p, led = workspace("nolink")
    granted(led)
    f = Fake()
    out = R.publish(sol(), led, live=True, transport=f, path=p)
    check("an unlinked instance blocks the publish", out["mode"] == "blocked")
    check("refused for the link", out["refused"] == "link")
    check("nothing was sent", f.calls == [])
    check("the fix is the pairing command", out["fix"] == R.FIX_LINK)

    bare = Ledger(os.path.join(TMP, "nolink", "empty.json"))
    out = R.publish(failing(), bare, live=True, transport=f, path=p)
    check("with all three broken, verification answers first",
          out["refused"] == "verification", str(out))
    check("the three refusals carry three different reasons",
          len({"verification", "consent", "link"}) == 3)
    check("and still nothing was sent", f.calls == [])


# ---------------------------------------------------------------------------
# the publish itself
# ---------------------------------------------------------------------------

def test_dry_run_touches_nothing():
    print("\ndry run — the default, and it sends nothing")
    p, led = workspace("dry")
    pair(p)
    granted(led)
    f = Fake()
    out = R.publish(sol(), led, transport=f, path=p)
    check("the mode is dry-run", out["mode"] == "dry-run")
    check("no transport call at all", f.calls == [])
    check("it reports the URL it would have used",
          out["would_post"]["url"] == BASE + "/api/solutions")
    check("and the idempotency key it would have sent",
          out["would_post"]["idempotency_key"] == out["id"])
    check("the authorization is shown as a fingerprint",
          out["would_post"]["authorization"] == "Bearer <%s>" % L.fingerprint(TOKEN))
    check("a dry run never carries the token", not leaks(out), str(out))


def test_happy_path():
    print("\nthe happy path — one POST, once")
    p, led = workspace("happy")
    pair(p)
    granted(led)
    clock = Clock()
    f = Fake((201, {}, {"id": "garden-abc123"}))
    s = sol()
    out = R.publish(s, led, live=True, transport=f, path=p, sleep=clock.sleep,
                    clock=clock.now)
    check("the mode is live", out["mode"] == "live", str(out))
    check("exactly one request", len(f.calls) == 1)
    check("posted to /api/solutions", f.calls[0]["url"] == BASE + "/api/solutions")
    check("with the device token as bearer",
          f.calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN)
    check("and the solution id as the idempotency key",
          f.calls[0]["headers"]["Idempotency-Key"] == out["id"])
    body = f.calls[0]["body"]
    check("the body carries the gate signature",
          body["gate_signature"] == "physics.bend=pass|physics.mass=pass",
          body["gate_signature"])
    check("every gate in the signature says pass",
          all(part.endswith("=pass") for part in body["gate_signature"].split("|")))
    check("the body carries the id the key was built from", body["id"] == out["id"])
    check("the body carries the gate detail", len(body["gates"]) == 2)
    check("the remote id comes back", out["remote_id"] == "garden-abc123")
    check("one attempt", out["attempts"] == 1)
    check("the id is the commons content hash",
          out["id"] == Solution(task=s["task"], gates=s["gates"],
                                recipe=s["recipe"]).ident())
    check("the result never carries the token", not leaks(out), str(out))
    check("last publish is recorded", L.status(p)["last_publish"] == clock.now())
    check("the publish ledger never carries the token",
          not leaks(json.load(open(R.ledger_path(p)))))
    check("recording a publish leaves the credential at 0600", mode_of(p) == 0o600,
          oct(mode_of(p)))
    check("and writes the publish ledger at 0600 too",
          mode_of(R.ledger_path(p)) == 0o600, oct(mode_of(R.ledger_path(p))))


def test_idempotency():
    print("\nidempotency — the same solution never goes twice")
    p, led = workspace("idem")
    pair(p)
    granted(led)
    first = R.publish(sol(), led, live=True, transport=Fake(), path=p)
    check("the first publish is live", first["mode"] == "live")

    f2 = Fake()
    again = R.publish(sol(), led, live=True, transport=f2, path=p)
    check("the second reports already_published", again.get("already_published") is True,
          str(again))
    check("and posts nothing", f2.calls == [], str(f2.calls))
    check("the mode still says nothing left the machine", again["mode"] == "blocked")
    check("it names the remote id it already has",
          again["remote_id"] == first["remote_id"])
    check("the ledger holds exactly one row", len(R.published(p)) == 1)
    check("a dry run of a published solution says the same",
          R.publish(sol(), led, transport=f2, path=p).get("already_published") is True)

    f3 = Fake()
    other = R.publish(sol(task="route the scraper around a restructured table"),
                      led, live=True, transport=f3, path=p)
    check("a genuinely different solution still publishes", other["mode"] == "live")
    check("with its own request", len(f3.calls) == 1)
    check("and its own id", other["id"] != first["id"])
    check("the ledger holds two rows", len(R.published(p)) == 2)

    pair(p, uid="u-someone-else")
    f4 = Fake()
    relinked = R.publish(sol(), led, live=True, transport=f4, path=p)
    check("the same solution under a different account is a real publish",
          relinked["mode"] == "live", str(relinked))
    check("because the ledger is keyed by account too", len(f4.calls) == 1)


def test_failed_publishes_stay_retryable():
    print("\na failure is not a publish — nothing is recorded")
    p, led = workspace("failed")
    pair(p)
    granted(led)
    clock = Clock()
    out = R.publish(sol(), led, live=True, path=p, sleep=clock.sleep,
                    transport=Fake(*[(503, {}, {"error": "down"})] * 4))
    check("an exhausted retry blocks", out["mode"] == "blocked")
    check("nothing was recorded", R.published(p) == [])
    check("and last publish is untouched", L.status(p)["last_publish"] is None)
    later = R.publish(sol(), led, live=True, transport=Fake(), path=p)
    check("so the same solution publishes when Garden comes back",
          later["mode"] == "live")


# ---------------------------------------------------------------------------
# retry policy
# ---------------------------------------------------------------------------

def test_retries_on_5xx_and_429():
    print("\nretry — 5xx and 429, bounded and capped")
    p, led = workspace("retry")
    pair(p)
    granted(led)

    clock = Clock()
    out = R.publish(sol(), led, live=True, path=p, sleep=clock.sleep, clock=clock.now,
                    transport=Fake((500, {}, {"error": "boom"}), (200, {}, {"id": "g1"})))
    check("a 500 then a 200 succeeds", out["mode"] == "live", str(out))
    check("it took two attempts", out["attempts"] == 2)
    check("and slept once, for the base delay", clock.slept == [0.5], str(clock.slept))

    p2, led2 = workspace("retry2")
    pair(p2)
    granted(led2)
    clock = Clock()
    f = Fake((500, {}, {}), (502, {}, {}), (200, {}, {"id": "g2"}))
    out = R.publish(sol(), led2, live=True, path=p2, sleep=clock.sleep, clock=clock.now,
                    transport=f)
    check("two failures then success", out["mode"] == "live" and out["attempts"] == 3)
    check("the backoff doubles", clock.slept == [0.5, 1.0], str(clock.slept))
    check("three requests were made", len(f.calls) == 3)

    p3, led3 = workspace("retry3")
    pair(p3)
    granted(led3)
    clock = Clock()
    f = Fake(*[(503, {}, {"error": "down"})] * 4)
    out = R.publish(sol(), led3, live=True, path=p3, sleep=clock.sleep, clock=clock.now,
                    transport=f)
    check("a hard attempt cap stops the loop", len(f.calls) == R.MAX_ATTEMPTS,
          str(len(f.calls)))
    check("it blocks rather than pretending", out["mode"] == "blocked")
    check("naming the remote as unavailable", out["refused"] == "unavailable")
    check("with one sleep fewer than attempts", clock.slept == [0.5, 1.0, 2.0],
          str(clock.slept))


def test_429_respects_the_server():
    print("\n429 — the server's own backoff wins")
    p, led = workspace("throttle")
    pair(p)
    granted(led)
    clock = Clock()
    out = R.publish(sol(), led, live=True, path=p, sleep=clock.sleep, clock=clock.now,
                    transport=Fake((429, {"retry-after": "2"}, {}), (200, {}, {"id": "g"})))
    check("a 429 is retried", out["mode"] == "live")
    check("waiting exactly what the server asked for", clock.slept == [2.0],
          str(clock.slept))

    p2, led2 = workspace("throttle2")
    pair(p2)
    granted(led2)
    clock = Clock()
    R.publish(sol(), led2, live=True, path=p2, sleep=clock.sleep, clock=clock.now,
              transport=Fake((429, {"x-ratelimit-reset": "600"}, {}), (200, {}, {"id": "g"})))
    check("an absurd wait is clamped to the cap", clock.slept == [R.RETRY_AFTER_CAP_S],
          str(clock.slept))

    p3, led3 = workspace("throttle3")
    pair(p3)
    granted(led3)
    clock = Clock()
    R.publish(sol(), led3, live=True, path=p3, sleep=clock.sleep, clock=clock.now,
              transport=Fake((429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, {}),
                             (200, {}, {"id": "g"})))
    check("an HTTP-date Retry-After falls back to the schedule", clock.slept == [0.5],
          str(clock.slept))


def test_4xx_is_never_retried():
    print("\n4xx — a verdict, not a transient failure")
    p, led = workspace("verdict")
    pair(p)
    granted(led)
    for code, label in ((400, "bad request"), (409, "conflict"), (422, "unprocessable")):
        clock = Clock()
        f = Fake((code, {}, {"error": label}))
        out = R.publish(sol(task="task %d" % code), led, live=True, path=p,
                        sleep=clock.sleep, clock=clock.now, transport=f)
        check("HTTP %d is sent exactly once" % code, len(f.calls) == 1, str(len(f.calls)))
        check("HTTP %d never sleeps" % code, clock.slept == [])
        check("HTTP %d blocks with the server's reason" % code,
              out["mode"] == "blocked" and label in out["why"], out["why"])
        check("HTTP %d is not recorded as published" % code,
              R.find_published(out["id"], "u-rishith", p) == {})

    clock = Clock()
    f = Fake((401, {}, {"error": "device revoked"}))
    out = R.publish(sol(task="revoked"), led, live=True, path=p, sleep=clock.sleep,
                    transport=f)
    check("a 401 is sent once", len(f.calls) == 1)
    check("refused for the token", out["refused"] == "token")
    check("and points at re-pairing", out["fix"] == R.FIX_LINK)

    f = Fake((404, {}, {}))
    out = R.publish(sol(task="not deployed"), led, live=True, path=p, transport=f)
    check("a 404 is 'not deployed yet'", out.get("remote") == "unavailable", str(out))
    check("sent once, no retry", len(f.calls) == 1)
    check("and it says the work is still local", "local commons" in out["why"])


def test_dead_socket_is_retried_then_reported():
    print("\na dead socket — retried, then reported honestly")
    p, led = workspace("socket")
    pair(p)
    granted(led)
    clock = Clock()
    out = R.publish(sol(), led, live=True, path=p, sleep=clock.sleep, clock=clock.now,
                    transport=Fake(L.TransportError("connection reset"), (200, {}, {"id": "g"})))
    check("a dropped packet does not end the publish", out["mode"] == "live", str(out))
    check("it retried once", out["attempts"] == 2)

    p2, led2 = workspace("socket2")
    pair(p2)
    granted(led2)
    clock = Clock()
    out = R.publish(sol(), led2, live=True, path=p2, sleep=clock.sleep, clock=clock.now,
                    transport=Fake(*[L.TransportError("no route to host")] * 4))
    check("a network that never answers blocks", out["mode"] == "blocked")
    check("reported as unreachable", out["refused"] == "unreachable")
    check("with the underlying error", "no route to host" in out["why"])
    check("and nothing recorded", R.published(p2) == [])


# ---------------------------------------------------------------------------
# autopublish
# ---------------------------------------------------------------------------

def test_autopublish_is_honest():
    print("\nautopublish — off by default, and never a bypass")
    p, led = workspace("auto")
    pair(p)
    check("default off", L.autopublish(p) is False)
    check("off means an unattended caller must ask",
          R.may_publish_unattended(led, p)["allowed"] is False)

    L.set_autopublish(True, p)
    check("it can be turned on", L.autopublish(p) is True)
    check("the file stays 0600", mode_of(p) == 0o600, oct(mode_of(p)))
    check("status reports it", L.status(p)["autopublish"] is True)

    # The whole point: on, but consent absent.
    f = Fake()
    out = R.publish(sol(), led, live=True, transport=f, path=p)
    check("autopublish on does NOT bypass the consent check",
          out["refused"] == "consent", str(out))
    check("and posts nothing", f.calls == [], str(f.calls))
    check("nor does it bypass verification",
          R.publish(failing(), granted(led), live=True, transport=f,
                    path=p)["refused"] == "verification")
    check("still posts nothing", f.calls == [])

    check("with consent it is allowed unattended",
          R.may_publish_unattended(led, p)["allowed"] is True)
    L.set_autopublish(False, p)
    out = R.may_publish_unattended(led, p)
    check("turning it off stops unattended publishing", out["allowed"] is False)
    check("and names the toggle as the reason", "autopublish is off" in out["why"])
    check("but an attended publish still works",
          R.publish(sol(), led, live=True, transport=Fake(), path=p)["mode"] == "live")

    p2, _ = workspace("auto2")
    out = L.set_autopublish(True, p2)
    check("it cannot be turned on when unlinked", out["autopublish"] is False)
    check("and says to pair first", "garden.link pair" in out["why"])

    led2 = Ledger(os.path.join(TMP, "auto2", "consent.json"))
    check("unattended is refused when unlinked",
          R.may_publish_unattended(led2, p2)["allowed"] is False)


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------

def test_accepts_both_solution_shapes():
    print("\nthe two shapes a solution actually arrives in")
    p, led = workspace("shapes")
    pair(p)
    granted(led)
    s = Solution(task="size a bracket web", recipe="invert the bending equation",
                 gates=[{"name": "physics.bend", "passed": True}])
    f = Fake()
    out = R.publish(s, led, live=True, transport=f, path=p)
    check("a commons Solution publishes", out["mode"] == "live", str(out))
    check("its id is the one the commons would store", out["id"] == s.ident())
    check("its signature is the one the commons would compute",
          f.calls[0]["body"]["gate_signature"] == s.signature())

    d = dict(task="size a bracket web", recipe="invert the bending equation",
             gates=[{"name": "physics.bend", "passed": True}])
    check("the equivalent dict is the same publish",
          R.publish(d, led, live=True, transport=Fake(),
                    path=p).get("already_published") is True)
    check("a dict with no gate_sig gets one computed",
          R.fields(d)["gate_signature"] == "physics.bend=pass")


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="daisy-garden-")
    print("garden link and remote publish — test suite")
    try:
        test_absent_and_malformed_read_as_not_linked()
        test_pairing_refuses_bad_codes_offline()
        test_pairing_stores_the_token()
        test_pairing_degrades_and_repairs_modes()
        test_directory_and_base_resolution()
        test_unlink()
        test_unverified_is_refused_before_the_network()
        test_consent_is_required()
        test_link_is_required_and_order_is_fixed()
        test_dry_run_touches_nothing()
        test_happy_path()
        test_idempotency()
        test_failed_publishes_stay_retryable()
        test_retries_on_5xx_and_429()
        test_429_respects_the_server()
        test_4xx_is_never_retried()
        test_dead_socket_is_retried_then_reported()
        test_autopublish_is_honest()
        test_accepts_both_solution_shapes()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
