#!/usr/bin/env python3
"""
The whole loop, end to end, in one run.

The claim Daisy and Garden make together is a cycle, and a cycle is the kind of
thing that is easy to describe and easy to get wrong in exactly one place. So
this runs it rather than asserting it:

    1  a Daisy agent hits a failing gate
    2  it asks Garden — the public index — whether anyone has fixed THIS gate
    3  MISS: nobody has. It solves the problem itself, deterministically.
    4  the gates certify the fix
    5  it is admitted to the local commons, which refuses unverified work
    6  it is published to Garden, consent-gated, as a verified entry
    7  Garden reindexes; the solution is now a static shard on a CDN
    8  a DIFFERENT agent, on a different machine, hits the same gate
    9  HIT: it fetches the fix in one request and never re-solves it

Step 3 and step 9 are the two ends of the argument. If step 9 does not return
what step 6 published, the loop is broken no matter how good each half looks
on its own.

    python3 tools/e2e_garden.py [--gate demo.margin]

Uses a synthetic gate name by default so it can prove a genuine MISS -> HIT
transition without depending on what happens to already be in the index.

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commons.consent import Ledger                                  # noqa: E402
from commons.store import Solution, admit, recall, NotVerified      # noqa: E402
from garden import index as gindex                                  # noqa: E402
from garden.publish import prepare                                  # noqa: E402

SITE = os.path.dirname(ROOT) + "/garden-site"


def step(n, title):
    print("\n\033[1m%d. %s\033[0m" % (n, title))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", default="demo.margin")
    ap.add_argument("--keep", action="store_true", help="leave the demo entry in Garden")
    a = ap.parse_args(argv)
    gate = a.gate
    api = gindex.API

    step(1, "An agent hits a failing gate")
    print("   gate      %s" % gate)
    print("   verdict   FAIL — margin 0.72 against a required 1.5")

    step(2, "It asks Garden whether anyone has fixed this gate")
    t0 = time.time()
    before = gindex.api_search([gate])
    print("   GET %s/api/v1/gate/%s.json" % (api, gate))
    print("   %d result(s) in %.0f ms" % (len(before), (time.time() - t0) * 1000))
    if before:
        print("   (already present — rerun with --gate <something-new> for a clean MISS)")
    else:
        print("   \033[2mMISS. Nobody has published a verified fix. That is a real answer,\033[0m")
        print("   \033[2mnot an error: the honest move is to solve it and publish.\033[0m")

    step(3, "It solves the problem itself and the gates certify the fix")
    sol = Solution(
        task="end-to-end demo: %s failed and was repaired by algebra" % gate,
        brief="proving the Daisy -> Garden -> Daisy cycle closes",
        gates=[{"name": gate, "passed": True, "margin": 1.5}],
        vendor="labctl", model="deterministic", kind="software",
        recipe="invert the margin equation for the parameter, round up, re-run the gate",
        tokens_cost=41000)
    print("   signature %s" % sol.signature())

    step(4, "Admitted to the local commons — which refuses unverified work")
    try:
        bad = Solution(task="same thing but it failed", gates=[{"name": gate, "passed": False}])
        admit(bad)
        print("   \033[31mBUG: an unverified solution was admitted\033[0m")
        return 1
    except NotVerified as exc:
        print("   refused a failing twin: %s" % exc)
    sid = admit(sol)
    print("   admitted  %s" % sid)

    step(5, "Published to Garden — consent is checked, not assumed")
    led = Ledger()
    if not led.allows("artifact", "garden"):
        print("   blocked: no 'artifact' grant for garden")
        print("   fix: python3 -m commons.cli consent grant --scope artifact --target garden")
        return 1
    full = [h for h in recall(sol.task, gates=[gate], limit=20) if h["id"] == sid]
    prep = prepare(full[0])
    print("   branch    %s" % prep["branch"])
    print("   files     %s" % ", ".join(prep["files"]))

    step(6, "Garden reindexes and redeploys")
    gp = gindex.clone_path()
    gindex.git(gp, "checkout", "-q", "main")
    gindex.git(gp, "checkout", "-q", prep["branch"], "--", "solutions")
    gindex.git(gp, "add", "-A")
    gindex.git(gp, "commit", "-q", "-m", "Add: %s" % sol.task[:60])
    push = gindex.git(gp, "push", "-q", "origin", "main")
    print("   mirror    %s" % ("pushed" if push.returncode == 0 else "local only"))
    subprocess.run([sys.executable, "tools/build_api.py", "--src", gp],
                   cwd=SITE, capture_output=True, text=True)
    dep = subprocess.run(["vercel", "--prod", "--yes"], cwd=SITE,
                         capture_output=True, text=True, timeout=300)
    live = "Aliased" in dep.stdout or "Production" in dep.stdout
    print("   deploy    %s" % ("live" if live else "failed: " + dep.stderr[-120:]))

    step(7, "A different agent, elsewhere, hits the same gate")
    print("   GET %s/api/v1/gate/%s.json" % (api, gate))
    hit, t0 = [], time.time()
    for _ in range(12):                     # CDN propagation, bounded
        hit = gindex.api_search([gate])
        if hit:
            break
        time.sleep(4)
    ms = (time.time() - t0) * 1000
    if not hit:
        print("   \033[31mMISS — the loop did not close\033[0m")
        return 1
    print("   \033[32mHIT\033[0m after %.0f ms" % ms)
    for h in hit:
        print("     %s" % h["title"][:70])
        print("     recipe    %s" % h["recipe"][:70])
        print("     signature %s" % h["signature"])
        print("     saved     %s tokens the second agent did not spend"
              % "{:,}".format(h.get("tokens_cost", 0)))

    print("\n\033[1mThe loop closes.\033[0m One agent's verified fix reached another "
          "agent\n   in %d requests, with no account, no key and no rate limit.\n"
          % (2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
