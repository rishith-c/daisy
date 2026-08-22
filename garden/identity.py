"""
Who is publishing, established from credentials that already exist.

Garden needs an identity for attribution and for review, and the honest way to
get one is to read the sign-ins the machine already has rather than to invent a
new account system. `gh` is already authenticated; git already knows a name and
an email; the agent CLIs already hold their own tokens.

On "sign in with Google": deliberately absent. A Google button that does not
perform a real OAuth exchange against a registered client is a form that
collects a credential under false pretenses, and this file will not ship one.
If a client id is ever configured (GARDEN_GOOGLE_CLIENT_ID), the flow can be
added properly; until then `providers()` reports it as unconfigured rather than
drawing a button that lies.

Nothing here reads a token value. It reports *that* a provider is signed in and
under what account name — never the secret itself, because identity is all the
publisher needs and the spool ends up on a projector.

    python3 -m garden.cli whoami

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict

HOME = os.path.expanduser("~")


@dataclass
class Provider:
    name: str
    signed_in: bool
    account: str = ""
    detail: str = ""


def _gh() -> Provider:
    if not shutil.which("gh"):
        return Provider("github", False, detail="gh not installed")
    try:
        p = subprocess.run(["gh", "auth", "status"], capture_output=True,
                           text=True, timeout=15)
    except Exception as exc:
        return Provider("github", False, detail=str(exc)[:60])
    txt = (p.stdout or "") + (p.stderr or "")
    if "Logged in" not in txt:
        return Provider("github", False, detail="run: gh auth login")
    acct = ""
    for line in txt.splitlines():
        if "Logged in to" in line and "account" in line:
            parts = line.split("account")
            if len(parts) > 1:
                acct = parts[1].strip().split()[0]
            break
    return Provider("github", True, acct, "gh cli")


def _git() -> Provider:
    def cfg(k):
        try:
            return subprocess.run(["git", "config", "--get", k], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    name, email = cfg("user.name"), cfg("user.email")
    return Provider("git", bool(name), name, email)


def _cli(name: str, path: str, keys=()) -> Provider:
    full = os.path.join(HOME, path)
    if not os.path.exists(full):
        return Provider(name, False, detail="not signed in")
    try:
        d = json.load(open(full, encoding="utf-8"))
    except Exception:
        return Provider(name, False, detail="unreadable")
    # Report the shape, never a value.
    present = [k for k in (keys or d.keys()) if k in d]
    return Provider(name, bool(present), "", "holds: " + ", ".join(present[:4]))


def _google() -> Provider:
    cid = os.environ.get("GARDEN_GOOGLE_CLIENT_ID", "")
    if not cid:
        return Provider("google", False, detail=
                        "not configured — set GARDEN_GOOGLE_CLIENT_ID to enable a real OAuth flow")
    return Provider("google", False, detail="client configured; flow not implemented yet")


def providers() -> list[Provider]:
    return [
        _gh(),
        _git(),
        _cli("claude", ".claude/.credentials.json"),
        _cli("codex", ".codex/auth.json", ("auth_mode", "tokens")),
        _cli("opencode", ".local/share/opencode/auth.json"),
        _google(),
    ]


def publisher() -> dict:
    """The identity a Garden PR is attributed to. GitHub wins because that is
    where the review actually happens."""
    ps = {p.name: p for p in providers()}
    gh, git = ps.get("github"), ps.get("git")
    if gh and gh.signed_in and gh.account:
        return {"as": gh.account, "via": "github", "ok": True}
    if git and git.signed_in:
        return {"as": git.account, "via": "git", "ok": True,
                "note": "git identity only — a PR needs gh auth login"}
    return {"as": "", "via": "", "ok": False,
            "note": "no publishable identity; run: gh auth login"}
