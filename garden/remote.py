"""
The autonomous publish path — a verified solution, posted by a machine.

garden/publish.py opens a pull request, which is the right shape when a person
is going to read it. This file is the other shape: a linked instance POSTing a
solution to the Garden API with nobody watching. Everything that is comfortable
about the PR path — a human reads the diff, a human clicks merge, a mistake is
caught in review — is gone here, so the checks that remain have to be worth
more, not less.

There are three of them and they are independent, because they fail for three
different reasons and each has a different fix:

  1. **Verification** — every gate in the solution passed. Checked first and
     entirely offline: an unverified solution is refused before a socket is
     opened, because the network is not entitled to an opinion about it.
  2. **Consent** — the ledger grants `artifact` for target `garden`. Publishing
     ships the artifact, the brief and the provenance of the agent that wrote
     it, and a developer who never agreed to that has been exfiltrated by a
     cron job.
  3. **Link** — this machine holds a device token (garden/link.py). No token,
     no publish; there is no anonymous path and no fallback account.

`mode` is always one of blocked | dry-run | live, the same vocabulary
garden/publish.py uses, and it answers exactly one question: did anything leave
this machine. An already-published solution therefore reports `blocked` with
`already_published: True` — nothing was sent, and a mode that said otherwise to
signal "this is fine" would make the one field a caller can trust untrustworthy.

    IS      three preconditions, one POST, bounded retry, a local record of
            what has already gone out, and a toggle that says whether a human
            needs to be asked first
    IS NOT  a queue, a daemon, a background thread, batching, partial uploads,
            server-side dedup we rely on, or artifact upload — the body is
            metadata plus the gate signature

**Idempotency**, because this runs unattended and unattended code is the code
that runs twice. The solution id is already a content hash over task, recipe
and gate signature, so it is the idempotency key rather than a new one: two
runs that produced genuinely identical verified work are the same publish, and
a run that changed anything is a different one. Sends are recorded locally,
keyed by account as well as id — a machine relinked to a different Garden
account has genuinely not published there, and suppressing that would be a
silent data loss rather than a saved request. Only successes are recorded, so a
failed attempt is still retryable tomorrow.

**Retry** covers 429, 5xx and a dead socket, with the schedule doubling from
0.5 s to a hard cap and a hard attempt limit. A 4xx is never retried: the
server rejecting a solution is a verdict about that solution, and sending it
again is just a slower way to be told no. (A dead socket is neither a verdict
nor a 5xx; it is retried anyway, because venue wifi drops packets and an
unattended publisher that gives up on the first dropped packet is useless.)

**The autopublish toggle** is not visible from publish(). It cannot switch a
check off because it is never read on the path where the checks live — the only
thing it decides is whether a caller has to ask a human first, and that
decision is made in `may_publish_unattended()`, which requires consent and a
link *in addition* to the toggle. A flag that could be flipped into skipping the
consent check would not be a toggle, it would be a bypass with a friendly name.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import time
from collections import namedtuple

from commons.consent import Ledger
from commons.store import Solution
from garden import link
from garden.link import TransportError, UrllibTransport, decode, headers, message

TARGET = "garden"
SCOPE = "artifact"
SOLUTIONS_PATH = "/api/solutions"

MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
RETRY_AFTER_CAP_S = 30.0    # a server asking for five minutes is a stop, not a retry
RETRY_HEADERS = ("retry-after", "x-ratelimit-reset")
TIMEOUT_S = 20.0

# The exact commands that clear each precondition. Spelled once, so a message
# an agent prints at 03:00 cannot drift from a command that still works.
FIX_CONSENT = "python3 -m commons.cli consent grant --scope artifact --target garden"
FIX_LINK = "python3 -m garden.link pair --code ABC123"

Reply = namedtuple("Reply", "status body headers attempts error")


# ---------------------------------------------------------------------------
# the solution, in the shapes it actually arrives in
# ---------------------------------------------------------------------------

def fields(sol) -> dict:
    """Normalise a commons Solution or the dict garden/cli.py builds.

    Both are real callers, and the id and the signature are computed by
    commons.store rather than re-derived here — a second implementation of the
    hash is a second source of truth, and it would drift on the day someone
    adds a field to one of them.
    """
    if isinstance(sol, Solution):
        return {"id": sol.ident(), "task": sol.task, "brief": sol.brief,
                "kind": sol.kind, "recipe": sol.recipe, "vendor": sol.vendor,
                "model": sol.model, "artifact": sol.artifact,
                "tokens_cost": sol.tokens_cost, "gates": list(sol.gates),
                "gate_signature": sol.signature()}
    d = dict(sol or {})
    gates = list(d.get("gates") or [])
    mirror = Solution(task=d.get("task", ""), gates=gates, recipe=d.get("recipe", ""))
    return {"id": d.get("id") or mirror.ident(),
            "task": d.get("task", ""), "brief": d.get("brief", ""),
            "kind": d.get("kind", "software"), "recipe": d.get("recipe", ""),
            "vendor": d.get("vendor", ""), "model": d.get("model", ""),
            "artifact": d.get("artifact", ""), "tokens_cost": d.get("tokens_cost", 0),
            "gates": gates, "gate_signature": d.get("gate_sig") or mirror.signature()}


def wire(sol: dict) -> dict:
    """The POST body.

    Shaped like the manifest commons/publish.py already writes, so a solution
    means the same thing whether it travelled as a bundle or as JSON, plus the
    flat `gate_signature` the server checks before it accepts anything.
    """
    return {
        "schema": "daisy.commons.solution/1",
        "id": sol["id"],
        "title": sol["task"][:120],
        "task": sol["task"],
        "brief": sol["brief"],
        "kind": sol["kind"],
        "recipe": sol["recipe"],
        "produced_by": {"vendor": sol["vendor"], "model": sol["model"]},
        "tokens_cost": sol["tokens_cost"],
        "gate_signature": sol["gate_signature"],
        "gates": sol["gates"],
    }


# ---------------------------------------------------------------------------
# what has already gone out
# ---------------------------------------------------------------------------

def ledger_path(path: str = None) -> str:
    """Beside the credential, not inside it.

    Sharing the credential file would mean `unlink` erased the record of every
    prior publish, and the next link would happily post all of it again.
    """
    return os.path.join(os.path.dirname(link.cred_path(path)) or ".", "published.json")


def published(path: str = None) -> list:
    d = link.read_json(ledger_path(path))
    rows = d.get("published")
    return rows if isinstance(rows, list) else []


def find_published(sid: str, account: str, path: str = None) -> dict:
    for row in published(path):
        if row.get("solution") == sid and row.get("account", "") == account:
            return row
    return {}


def record_published(sid: str, account: str, remote_id: str, when: float = None,
                     path: str = None) -> dict:
    row = {"solution": sid, "account": account, "remote_id": remote_id,
           "at": round(when if when is not None else time.time(), 3)}
    rows = published(path)
    rows.append(row)
    # 0600 like the credential beside it: this file holds no secret, but it
    # does hold the list of everything this machine has ever published, and
    # that is nobody else's business either.
    link.write_private(ledger_path(path), {"version": 1, "published": rows})
    return row


# ---------------------------------------------------------------------------
# the network
# ---------------------------------------------------------------------------

def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


def _retry_after(hdrs: dict, fallback: float) -> float:
    for name in RETRY_HEADERS:
        v = hdrs.get(name) or hdrs.get(name.title())
        if v is None:
            continue
        try:
            return min(max(float(v), 0.0), RETRY_AFTER_CAP_S)
        except (TypeError, ValueError):
            continue           # an HTTP-date Retry-After is legal; fallback covers it
    return fallback


def send(url: str, body: dict, token: str, *, transport=None, timeout: float = TIMEOUT_S,
         sleep=time.sleep, extra: dict = None) -> Reply:
    """One POST, retried on 429/5xx/dead socket and never on anything else."""
    tp = transport or UrllibTransport()
    data = json.dumps(body).encode("utf-8")
    h = headers(token)
    h.update(extra or {})
    delay = BACKOFF_BASE_S
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = attempt == MAX_ATTEMPTS
        try:
            status, rh, raw = tp("POST", url, data, h, timeout)
        except TransportError as e:
            if last:
                return Reply(0, {}, {}, attempt, str(e))
            sleep(delay)
            delay = min(delay * 2.0, BACKOFF_CAP_S)
            continue
        if _retryable(status) and not last:
            sleep(_retry_after(rh, delay))
            delay = min(delay * 2.0, BACKOFF_CAP_S)
            continue
        return Reply(status, decode(raw), rh, attempt, "")
    raise AssertionError("unreachable")           # the loop always returns


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def preconditions(sol: dict, ledger: Ledger = None, path: str = None) -> dict:
    """The three gates, in the order they are allowed to fail in.

    Verification first because it needs nothing — not the network, not a
    ledger, not a credential — so an unverifiable solution is refused on the
    cheapest and most fundamental ground rather than on whichever precondition
    happened to be checked first. Returns {} when all three are green.
    """
    gates = sol.get("gates") or []
    if not gates or not all(g.get("passed") for g in gates):
        bad = [g.get("name", "?") for g in gates if not g.get("passed")]
        return {"mode": "blocked", "refused": "verification", "why":
                ("refusing to publish work that did not pass every gate: %s"
                 % ", ".join(bad) if bad else
                 "refusing to publish work that carries no gate results at all"),
                "fix": "re-run the gates until they pass — there is no command "
                       "that publishes unverified work"}

    ledger = ledger if ledger is not None else Ledger()
    if not ledger.allows(SCOPE, TARGET):
        return {"mode": "blocked", "refused": "consent", "why":
                "no '%s' grant for %s — nothing leaves this machine without one"
                % (SCOPE, TARGET), "fix": FIX_CONSENT}

    creds = link.credentials(path)
    if not creds:
        return {"mode": "blocked", "refused": "link", "why":
                "this instance is not linked to a Garden account (%s)"
                % link.cred_path(path), "fix": FIX_LINK}
    return {}


def publish(solution, ledger: Ledger = None, live: bool = False, *, transport=None,
            path: str = None, base: str = None, timeout: float = TIMEOUT_S,
            sleep=time.sleep, clock=time.time) -> dict:
    """Publish a verified solution to Garden over the API.

    `mode` is blocked | dry-run | live and is never fudged. The token is never
    in the returned dict, in any branch.
    """
    sol = fields(solution)
    stop = preconditions(sol, ledger, path)
    if stop:
        return dict(stop, id=sol["id"])

    creds = link.credentials(path)
    account = creds.get("uid", "")
    prior = find_published(sol["id"], account, path)
    if prior:
        # Not an error and not a send. The caller asked for a state that
        # already holds, so the honest report is that nothing was posted and
        # why it did not need to be.
        return {"mode": "blocked", "already_published": True, "id": sol["id"],
                "remote_id": prior.get("remote_id", ""), "account": account,
                "at": prior.get("at"), "why":
                "%s is already in Garden under this account — the solution id is "
                "a content hash, so an identical solution is the same publish"
                % sol["id"][:8]}

    url = link.api_base(base, creds) + SOLUTIONS_PATH
    body = wire(sol)
    if not live:
        return {"mode": "dry-run", "id": sol["id"], "account": account,
                "gate_signature": sol["gate_signature"],
                "would_post": {"url": url, "bytes": len(json.dumps(body).encode("utf-8")),
                               "idempotency_key": sol["id"],
                               "authorization": "Bearer <%s>" % link.fingerprint(creds["token"])},
                "why": "live not requested"}

    r = send(url, body, creds["token"], transport=transport, timeout=timeout,
             sleep=sleep, extra={"Idempotency-Key": sol["id"]})

    if 200 <= r.status < 300:
        remote_id = str(r.body.get("id") or "")
        record_published(sol["id"], account, remote_id, clock(), path)
        link.note_publish(clock(), path)
        return {"mode": "live", "id": sol["id"], "remote_id": remote_id,
                "account": account, "status": r.status, "attempts": r.attempts,
                "gate_signature": sol["gate_signature"]}

    # Everything below sent something and got told no. Nothing is recorded, so
    # a transient failure stays retryable and a rejection stays a rejection.
    common = {"mode": "blocked", "id": sol["id"], "status": r.status,
              "attempts": r.attempts, "account": account}
    if r.status == 0:
        return dict(common, refused="unreachable", why=
                    "no response from %s after %d attempt(s): %s"
                    % (url, r.attempts, r.error))
    if r.status == 404:
        return dict(common, refused="unavailable", remote="unavailable", why=
                    "%s has no %s — the Garden API is not deployed yet. The "
                    "solution is still in the local commons." % (link.api_base(base, creds),
                                                                 SOLUTIONS_PATH))
    if r.status in (401, 403):
        return dict(common, refused="token", why=
                    "Garden rejected this device token (HTTP %d)%s" %
                    (r.status, ": " + message(r.body) if message(r.body) else ""),
                    fix=FIX_LINK)
    if r.status < 500:
        return dict(common, refused="rejected", why=
                    "Garden rejected the solution (HTTP %d)%s — not retried, a "
                    "rejection is a verdict" % (r.status,
                    ": " + message(r.body) if message(r.body) else ""))
    return dict(common, refused="unavailable", why=
                "Garden is unavailable (HTTP %d) after %d attempts" % (r.status, r.attempts))


# ---------------------------------------------------------------------------
# unattended operation
# ---------------------------------------------------------------------------

def may_publish_unattended(ledger: Ledger = None, path: str = None) -> dict:
    """May a caller publish right now without asking a human?

    Four independent yeses, and the toggle is only one of them. Note what this
    function cannot do: it cannot make publish() skip anything, because
    publish() never calls it and never reads the toggle. Consent is re-checked
    inside publish() on every single call even when the answer here was yes,
    which is what makes "autopublish is on" a statement about who is asked
    rather than about what is checked.
    """
    ledger = ledger if ledger is not None else Ledger()
    creds = link.credentials(path)
    if not creds:
        return {"allowed": False, "why": "not linked", "fix": FIX_LINK}
    if not ledger.allows(SCOPE, TARGET):
        return {"allowed": False, "why": "no '%s' grant for %s" % (SCOPE, TARGET),
                "fix": FIX_CONSENT}
    if not creds.get("autopublish"):
        return {"allowed": False, "why": "autopublish is off",
                "fix": "python3 -m garden.link autopublish --on"}
    return {"allowed": True, "account": creds.get("uid", ""),
            "note": "consent and verification are still checked on every publish"}
