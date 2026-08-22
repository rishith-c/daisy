"""
Linking one Daisy instance to a Garden account, and holding the credential.

The git path (garden/publish.py) attributes a solution to whoever `gh` is
signed in as, and that works because a pull request is read by a person. The
autonomous path has no person in it. An agent that finishes a verified run at
03:00 has to be able to publish without waking anyone, and something on disk
has to say which account that run belongs to. That is the whole job of this
file.

The exchange is deliberately one-way and short-lived at the front: a six
character code shown in the web UI is worth nothing on its own and expires,
and it is traded exactly once for a long-lived device token that afterwards
never appears in a URL, a log line, an exception, or a returned dict.
`status()` prints a fingerprint instead — the first eight hex of the token's
SHA-256 — which is enough to tell two machines apart and useless to whoever is
reading the terminal over your shoulder.

    IS      one JSON file at ~/.daisy/garden.json, mode 0600 and re-checked
            after every write, holding the device token, the account it pairs
            to, and the autopublish toggle; plus the HTTP seam both this file
            and garden/remote.py send through
    IS NOT  a keychain, an encrypted store, token refresh or rotation, a
            second copy of the consent ledger, or an account system

A file mode is not encryption and this file does not pretend otherwise: any
process running as this user can read the token, which is exactly the trust
boundary `gh` and every agent CLI on this machine already assume. What 0600
buys is that another *user* cannot, and that a screen-share of `ls -l`, a
world-readable backup or a shared checkout does not hand the token away. If
the filesystem will not hold 0600 — a FAT stick, some mounted shares — the
file is deleted rather than left readable and the caller is told, because
storing a secret somewhere it cannot be protected is worse than not storing
it at all.

Every way of failing to read this file means *not linked*. A missing file, a
truncated write, hand-edited JSON, a token that disagrees with its own
recorded hash: all one answer, because the alternative is an unattended agent
concluding it is authorised on the strength of a file it could not parse.

The transport lives here rather than in garden/remote.py only because this is
the lower of the two files that needs it — pairing is itself a network call,
and remote.py already imports this module. It is single-shot on purpose:
retrying a pairing code that a human is watching expire buys nothing, and the
retry policy that matters is the unattended one, which belongs next to the
unattended publish.

    python3 -m garden.link pair --code ABC123
    python3 -m garden.link status

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_API = "https://garden-taupe-three.vercel.app"

# Every Garden path this client knows, in one place. The other half of this
# contract is being written in another repo; when a route moves, it moves here
# and nowhere else.
PAIR_PATH = "/api/pair"
UNPAIR_PATH = "/api/pair"          # DELETE — assumed; a 404/405 degrades quietly

USER_AGENT = "daisy-garden/1.0 (+https://github.com/rishith-c/daisy)"
TIMEOUT_S = 15.0
CODE_LEN = 6


class TransportError(Exception):
    """The request never got a status back — DNS, TLS, timeout, closed socket."""


class CredentialsUnsafe(Exception):
    """The token could not be stored at 0600, so it was not stored at all.

    Loud on purpose. Every other failure in this file resolves quietly to "not
    linked"; this one cannot, because the quiet outcome would be a readable
    secret sitting on disk while the caller believes it is protected.
    """


# ---------------------------------------------------------------------------
# transport — the seam tests inject a fake through
# ---------------------------------------------------------------------------

class UrllibTransport:
    """The real one, split out purely so tests can inject a fake.

    A transport is a plain callable, the same five-argument contract
    port/client.py uses, so a reader who has seen one has seen both:

        (method, url, body_bytes|None, headers, timeout)
            -> (status, headers, body_bytes)
    """

    def __call__(self, method, url, body, headers, timeout):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            # A 4xx/5xx is a response, not a transport failure, and its status
            # is the entire input to the retry policy in garden/remote.py.
            # Raising here would turn a retryable 503 into a hard stop.
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
        except Exception as e:                    # URLError, socket.timeout, ssl
            raise TransportError(str(e)) from e


def headers(token: str = "") -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": USER_AGENT}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def decode(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", "replace")[:600]}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def message(body: dict) -> str:
    """Whatever the server called its complaint, in one string."""
    for k in ("error", "message", "detail", "why"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v[:200]
    return json.dumps(body)[:200] if body else ""


# ---------------------------------------------------------------------------
# where things live
# ---------------------------------------------------------------------------

def home() -> str:
    """Resolved per call, not at import: tests point DAISY_HOME at a tempdir."""
    return os.environ.get("DAISY_HOME") or os.path.join(os.path.expanduser("~"), ".daisy")


def cred_path(path: str = None) -> str:
    return path or os.path.join(home(), "garden.json")


def api_base(explicit: str = None, creds: dict = None) -> str:
    """Where to talk to Garden.

    A stored base beats the environment deliberately: a device token belongs to
    the server that issued it, so re-pointing GARDEN_API must never quietly
    send yesterday's token to a machine that did not mint it.
    """
    for candidate in (explicit, (creds or {}).get("base"),
                      os.environ.get("GARDEN_API"), DEFAULT_API):
        if candidate:
            return str(candidate).rstrip("/")
    return DEFAULT_API


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------

def read_json(path: str) -> dict:
    """Any unreadable file is an empty one. Callers turn that into "not linked"."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_private(path: str, data: dict) -> str:
    """Write JSON at 0600, atomically, and prove the mode afterwards.

    Three things are load-bearing here and each has bitten somebody:

      * O_CREAT's mode argument is ignored when the file already exists, so a
        re-link over a file someone chmod'ed to 0644 would inherit 0644. The
        explicit chmod covers that.
      * the umask subtracts from the mode, never adds, so 0600 is a ceiling
        that a hostile umask cannot loosen — but a filesystem without POSIX
        modes ignores it entirely, which is why the stat below is a check and
        not a formality.
      * os.replace is atomic, so a crash mid-write leaves the old credential
        rather than half of a new one.
    """
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.remove(path)
        raise CredentialsUnsafe(
            "%s came back as %04o, not 0600 — refusing to leave a device token "
            "on a filesystem that cannot protect it" % (path, mode))
    return path


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(token: str) -> str:
    """First eight hex of the token hash — identifies, does not authenticate."""
    return sha256(token)[:8] if token else ""


def credentials(path: str = None) -> dict:
    """The stored credential, or {} — which always means *not linked*.

    The recorded hash is checked against the token rather than trusted, so a
    hand-edited or partially-restored file reads as unlinked instead of as a
    credential whose displayed fingerprint would be a lie.
    """
    d = read_json(cred_path(path))
    token = d.get("token")
    if not isinstance(token, str) or not token:
        return {}
    recorded = d.get("token_sha256")
    if recorded and recorded != sha256(token):
        return {}
    return d


def _save(creds: dict, path: str = None) -> str:
    return write_private(cred_path(path), creds)


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------

def _clean(code: str) -> str:
    """Strip what people paste around a code; never change what it says.

    Spaces and the dash a UI puts in the middle are formatting. Case is not:
    folding it here would break a server that compares exactly, and a server
    that folds its own codes does not need the help.
    """
    return "".join(ch for ch in (code or "") if ch not in " -_\t\r\n")


def link(code: str, *, transport=None, base: str = None, path: str = None,
         timeout: float = TIMEOUT_S) -> dict:
    """Trade a pairing code for a device token and store it.

    Returns a dict that always carries `linked`, and never carries the token.
    Nothing raises for a network condition: a Garden that is not deployed yet
    is an expected state of the world during a build, and an agent that
    crashes on it is an agent that cannot run before the backend lands.
    """
    clean = _clean(code)
    if len(clean) != CODE_LEN or not clean.isalnum():
        # Verdict reachable without the network, so it is reached without it.
        return {"linked": False, "why":
                "a pairing code is %d letters or digits, as shown in the Garden "
                "web UI — got %d character(s)" % (CODE_LEN, len(clean))}

    url = api_base(base) + PAIR_PATH
    tp = transport or UrllibTransport()
    try:
        status, _h, raw = tp("POST", url, json.dumps({"code": clean}).encode("utf-8"),
                             headers(), timeout)
    except TransportError as e:
        return {"linked": False, "remote": "unreachable",
                "why": "could not reach %s: %s" % (url, e)}

    body = decode(raw)
    if status == 404:
        return {"linked": False, "remote": "unavailable", "why":
                "%s has no %s — the Garden API is not deployed yet. Nothing was "
                "stored and nothing is assumed." % (api_base(base), PAIR_PATH)}
    if status >= 400:
        return {"linked": False, "why":
                "Garden refused the pairing code (HTTP %d)%s" %
                (status, ": " + message(body) if message(body) else "")}
    token = body.get("token")
    if not isinstance(token, str) or not token:
        return {"linked": False, "why":
                "Garden accepted the code but returned no device token"}

    creds = {
        "version": 1,
        "base": api_base(base),
        "device_id": str(body.get("device_id") or ""),
        "uid": str(body.get("uid") or ""),
        "token": token,
        "token_sha256": sha256(token),
        "linked_at": time.time(),
        "last_publish": 0.0,
        # A new device token is a new authorisation. Unattended publishing is
        # re-armed by hand rather than inherited from whatever the last pairing
        # on this machine had switched on.
        "autopublish": False,
    }
    _save(creds, path)
    return {"linked": True, "account": creds["uid"], "device_id": creds["device_id"],
            "fingerprint": fingerprint(token), "base": creds["base"],
            "path": cred_path(path)}


def unlink(*, transport=None, path: str = None, timeout: float = TIMEOUT_S) -> dict:
    """Remove the credential locally; ask Garden to revoke it if it will.

    Local removal happens either way. A revoke that cannot be delivered — no
    network, no such route, a server that has forgotten this device — must not
    leave a token sitting on the disk of a machine the user has decided is no
    longer linked.
    """
    p = cred_path(path)
    creds = credentials(path)
    revoked, why = False, ""
    if creds.get("token"):
        tp = transport or UrllibTransport()
        try:
            status, _h, raw = tp("DELETE", api_base(None, creds) + UNPAIR_PATH, None,
                                 headers(creds["token"]), timeout)
            revoked = 200 <= status < 300
            if not revoked:
                why = "remote revoke returned HTTP %d%s" % (
                    status, " — revoke the device in the web UI" if status == 404 else "")
        except TransportError as e:
            why = "remote unreachable, revoke it in the web UI: %s" % e
    try:
        os.remove(p)
        removed = True
    except FileNotFoundError:
        removed = False                # already unlinked; that is the desired state
    except OSError as e:
        removed = False
        why = why or ("could not remove %s: %s" % (p, e))
    return {"unlinked": True, "removed_local": removed,
            "revoked_remotely": revoked, "why": why, "path": p}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def status(path: str = None) -> dict:
    """Linked or not, which account, which token, when it last published.

    The fingerprint is recomputed from the stored token rather than read back
    from the stored hash. A displayed fingerprint that does not describe the
    credential actually in use would be worse than displaying nothing.
    """
    creds = credentials(path)
    if not creds:
        return {"linked": False, "path": cred_path(path), "autopublish": False,
                "why": "no usable credential at %s — run: python3 -m garden.link "
                       "pair --code ABC123" % cred_path(path)}
    last = float(creds.get("last_publish") or 0.0)
    return {
        "linked": True,
        "account": creds.get("uid", ""),
        "device_id": creds.get("device_id", ""),
        "fingerprint": fingerprint(creds["token"]),
        "base": creds.get("base", ""),
        "linked_at": creds.get("linked_at", 0.0),
        "last_publish": last or None,
        "last_publish_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last)) if last else "",
        "autopublish": bool(creds.get("autopublish")),
        "path": cred_path(path),
    }


def note_publish(when: float = None, path: str = None) -> float:
    """Record that something was published just now. No-op when unlinked."""
    creds = credentials(path)
    if not creds:
        return 0.0
    creds["last_publish"] = float(when if when is not None else time.time())
    _save(creds, path)
    return creds["last_publish"]


# ---------------------------------------------------------------------------
# the autopublish toggle
# ---------------------------------------------------------------------------

def autopublish(path: str = None) -> bool:
    """Off unless a linked credential says otherwise.

    This answers exactly one question — may an agent publish *without asking a
    human first* — and it is not, and must never become, an answer to "may an
    agent publish". Consent and verification are separate questions with
    separate answers, and garden/remote.py's publish() cannot even see this
    flag, which is what stops the two from ever being confused.
    """
    return bool(credentials(path).get("autopublish"))


def set_autopublish(on: bool, path: str = None) -> dict:
    creds = credentials(path)
    if not creds:
        return {"autopublish": False, "why":
                "not linked — run: python3 -m garden.link pair --code ABC123"}
    creds["autopublish"] = bool(on)
    _save(creds, path)
    return {"autopublish": bool(on), "account": creds.get("uid", ""),
            "note": "consent and verification are still checked on every publish"}


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="garden.link", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="trade a pairing code for a device token")
    p.add_argument("--code", required=True)
    sub.add_parser("status", help="linked or not, and as whom")
    sub.add_parser("unlink", help="remove the credential; revoke it if Garden will")
    a = sub.add_parser("autopublish", help="publish without asking each time")
    a.add_argument("--on", action="store_true")
    a.add_argument("--off", action="store_true")
    o = ap.parse_args(argv)

    if o.cmd == "pair":
        r = link(o.code); ok = r["linked"]
    elif o.cmd == "status":
        r = status(); ok = r["linked"]
    elif o.cmd == "unlink":
        r = unlink(); ok = r["unlinked"]
    else:
        if o.on == o.off:
            print("pass exactly one of --on / --off")
            return 2
        r = set_autopublish(o.on); ok = "why" not in r
    print(json.dumps(r, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
