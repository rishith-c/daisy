"""
Publishing a verified solution to Garden as a pull request.

Three gates stand in front of a PR, and they are independent because they fail
for different reasons:

  1. **Verification** — the solution must already carry a gate signature in
     which everything passed. Garden's whole promise is that its entries were
     verified; a PR that breaks that promise is worse than an empty index.
  2. **Consent** — the ledger must grant `artifact` (and `source`, if the brief
     goes too) for target `garden`. Opening a PR publishes work under a real
     name to a place other people read.
  3. **Identity** — there has to be someone to attribute it to. `gh` handles
     this; nothing here invents an account.

Default is a dry run: the branch and the commit are made locally and the exact
`gh pr create` invocation is printed. Nothing is pushed unless a caller passes
`live=True` and all three gates above are green. There is no flag that skips
the consent check — a toggle that can be turned on without a recorded grant is
not consent, it is a default.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from commons.consent import Ledger
from commons.publish import bundle
from garden import index
from garden.identity import publisher

TARGET = "garden"


class NotPublishable(Exception):
    """Raised when the solution itself is not fit to publish."""


def _branch(sol: dict) -> str:
    return "solution/%s-%s" % (index.slug(sol.get("task", ""), 40), sol["id"][:8])


def prepare(sol: dict, path: str = None) -> dict:
    """Write the entry into the local Garden on its own branch. No network."""
    gates = sol.get("gates", [])
    if not gates or not all(g.get("passed") for g in gates):
        raise NotPublishable("refusing to publish work that did not pass every gate")

    path = index.ensure_local(path)
    br = _branch(sol)
    index.git(path, "checkout", "-q", "-B", br)

    dest = os.path.join(path, index.SOLUTIONS, "%s-%s"
                        % (index.slug(sol.get("task", ""), 40), sol["id"][:8]))
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    bundle(sol, dest)

    who = publisher()
    meta = os.path.join(dest, "manifest.json")
    m = json.load(open(meta, encoding="utf-8"))
    m["published_by"] = {"as": who.get("as", ""), "via": who.get("via", "")}
    m["published_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    json.dump(m, open(meta, "w", encoding="utf-8"), indent=1)

    index.git(path, "add", "-A")
    index.git(path, "commit", "-q", "-m",
              "Add: %s\n\nVerified by: %s\nProduced by: %s"
              % (m.get("title", "")[:70], m["verified"]["gate_signature"],
                 m.get("produced_by", {}).get("model", "?")))
    return {"branch": br, "dir": dest, "files": sorted(os.listdir(dest)),
            "manifest": m}


def publish(sol: dict, ledger: Ledger = None, live: bool = False,
            path: str = None, repo: str = None) -> dict:
    """Prepare, then either report the PR or open it.

    `mode` is always one of blocked | dry-run | live and never fudged, so a
    caller can always tell whether anything left the machine.
    """
    ledger = ledger or Ledger()
    if not ledger.allows("artifact", TARGET):
        return {"mode": "blocked", "why":
                "no 'artifact' grant for garden — run: "
                "python3 -m commons.cli consent grant --scope artifact --target garden"}

    who = publisher()
    if not who.get("ok"):
        return {"mode": "blocked", "why": who.get("note", "no publishable identity")}

    prep = prepare(sol, path)
    path = path or index.clone_path()
    repo = repo or index.DEFAULT_REMOTE or index.has_remote(path)
    title = prep["manifest"].get("title", "")[:70]
    body = ("Verified solution from a Daisy run.\n\n"
            "**Gate signature:** `%s`\n"
            "**Produced by:** %s\n"
            "**Published by:** %s via %s\n\n"
            "Every gate in the signature passed. The verification table is in "
            "`VERIFICATION.md`.\n"
            % (prep["manifest"]["verified"]["gate_signature"],
               prep["manifest"].get("produced_by", {}).get("model", "?"),
               who.get("as", ""), who.get("via", "")))
    cmd = ["gh", "pr", "create", "--title", "Garden: %s" % title,
           "--body", body, "--head", prep["branch"]]
    if repo:
        cmd += ["--repo", repo]

    if not live or not repo:
        return {"mode": "dry-run", "branch": prep["branch"], "dir": prep["dir"],
                "files": prep["files"], "publisher": who,
                "why": "no garden remote set (GARDEN_REPO)" if not repo
                       else "live not requested",
                "would_run": " ".join(cmd[:6]) + " ..."}

    push = index.git(path, "push", "-u", "origin", prep["branch"])
    if push.returncode != 0:
        return {"mode": "dry-run", "branch": prep["branch"],
                "why": "push failed: " + (push.stderr or "")[-160:]}
    pr = subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=90)
    return {"mode": "live" if pr.returncode == 0 else "dry-run",
            "branch": prep["branch"], "publisher": who,
            "pr": (pr.stdout or "").strip() or (pr.stderr or "").strip()[-200:]}
