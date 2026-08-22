"""
Consent ledger for the Verified Commons.

Nothing leaves this machine without an explicit, recorded, revocable grant.
That is not politeness — publishing a solution ships the artifact, the brief
that produced it and the provenance of the agent that wrote it, and a developer
who never agreed to that has had their work exfiltrated by a dashboard.

Three properties the rest of the package depends on:

  * default deny — an absent ledger means no consent, never "assume yes"
  * per-scope — agreeing to share a printable model is not agreeing to share
    source, and the scopes are separate grants
  * revocable, with effect — revoking marks previously published entries
    withdrawn, so `pending_withdrawals()` can tell the publisher what to pull

The ledger is a plain JSON file the user can read and delete. A consent record
you cannot inspect in a text editor is not consent.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict

SCOPES = ("local", "artifact", "source", "telemetry")
SCOPE_TEXT = {
    "local":     "keep solutions in the local commons so your own agents reuse them",
    "artifact":  "publish the built artifact (STL, bundle) to an external index",
    "source":    "publish the source and the brief that produced it",
    "telemetry": "share anonymous counts of reuse and tokens saved",
}

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consent.json")


@dataclass
class Grant:
    scope: str
    granted: bool
    at: float
    target: str = ""          # e.g. "makerworld" — empty means everywhere
    note: str = ""


class Ledger:
    def __init__(self, path: str = None):
        self.path = path or DEFAULT_PATH
        self.grants: list[Grant] = []
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return                       # default deny; an unreadable ledger grants nothing
        for g in raw.get("grants", []):
            try:
                self.grants.append(Grant(**g))
            except TypeError:
                continue                 # a malformed record is ignored, not trusted

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "grants": [asdict(g) for g in self.grants]}, fh, indent=1)
        os.replace(tmp, self.path)       # atomic: never leave a half-written ledger

    # -- decisions ----------------------------------------------------------

    def grant(self, scope: str, target: str = "", note: str = "") -> Grant:
        if scope not in SCOPES:
            raise ValueError("unknown scope %r" % scope)
        g = Grant(scope, True, time.time(), target, note)
        self.grants.append(g)
        self.save()
        return g

    def revoke(self, scope: str, target: str = "", note: str = "") -> Grant:
        g = Grant(scope, False, time.time(), target, note)
        self.grants.append(g)
        self.save()
        return g

    def allows(self, scope: str, target: str = "") -> bool:
        """Latest matching decision wins; absent means no.

        A grant with an empty target covers every target; a grant naming a
        target covers only that one. Revocation follows the same shape, so
        revoking one target does not silently revoke the rest.
        """
        best, best_at = None, -1.0
        for g in self.grants:
            if g.scope != scope:
                continue
            if g.target and g.target != target:
                continue
            if g.at >= best_at:
                best, best_at = g, g.at
        return bool(best and best.granted)

    def state(self) -> dict:
        return {s: self.allows(s) for s in SCOPES}

    def revoked_since(self, scope: str) -> float:
        """When this scope was most recently turned off, or 0.0."""
        at = 0.0
        for g in self.grants:
            if g.scope == scope and not g.granted:
                at = max(at, g.at)
        return at
