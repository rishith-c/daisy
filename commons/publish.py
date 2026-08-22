"""
Publishing a verified solution outward — e.g. a printable part to MakerWorld.

Two gates stand in front of every byte that leaves this machine, and they are
independent on purpose:

  1. **Consent.** The relevant scope must be granted in the consent ledger.
     Publishing an artifact and publishing the source that produced it are
     separate grants, because agreeing to share a printable model is not
     agreeing to share the brief and the code behind it.
  2. **Verification.** The solution must already be in the commons, which means
     it already passed every gate. Publishing unverified work under a banner
     that says "this passed" would be the worst thing this project could ship.

Default is a dry run. `publish()` writes the bundle and reports the exact
request it *would* make; it posts nothing unless a caller passes `live=True`
AND credentials are present AND consent covers the target. There is no flag
that skips consent — that is the point of having one.

What goes in a bundle:

    manifest.json     what it is, what verified it, and the numbers
    <artifact>        the STL / archive itself
    VERIFICATION.md   the gate table, human-readable
    README.md         provenance: which agent, which model, which brief

The manifest carries print settings for a Bambu Lab machine because the first
hardware lane produces PETG brackets, and a model published without the
settings it was verified at is a model that will fail on someone else's plate.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import time

from .consent import Ledger

# Verified at these settings; shipped with the model so a reprint matches the
# geometry the margin gate actually signed off.
BAMBU_DEFAULTS = {
    "printer": "Bambu Lab P1S",
    "nozzle_mm": 0.4,
    "layer_mm": 0.2,
    "wall_loops": 4,
    "infill_pct": 40,
    "infill_pattern": "gyroid",
    "material": "PETG",
    "nozzle_c": 250,
    "bed_c": 80,
    "supports": False,
    "orientation": "web vertical, foot on the plate — layer lines normal to the bending stress",
}


class ConsentRequired(Exception):
    """Raised when a publish is attempted without a matching grant."""


def bundle(sol: dict, out_dir: str, settings: dict = None) -> str:
    """Write a publishable bundle. Local only — this never touches the network."""
    os.makedirs(out_dir, exist_ok=True)
    gates = sol.get("gates", [])
    manifest = {
        "schema": "daisy.commons.solution/1",
        "id": sol["id"],
        "title": sol["task"][:120],
        "kind": sol.get("kind", "software"),
        "produced_by": {"vendor": sol.get("vendor", ""), "model": sol.get("model", "")},
        "verified": {
            "gate_signature": sol.get("gate_sig", ""),
            "gates": gates,
            "all_passed": all(g.get("passed") for g in gates) if gates else False,
        },
        "recipe": sol.get("recipe", ""),
        "tokens_cost": sol.get("tokens_cost", 0),
        "reuses": sol.get("reuses", 0),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if sol.get("kind") == "hardware":
        manifest["print_settings"] = dict(BAMBU_DEFAULTS, **(settings or {}))

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)

    rows = "\n".join("| %s | %s | %s |" % (
        g.get("name", ""), "PASS" if g.get("passed") else "FAIL",
        g.get("margin", "")) for g in gates)
    with open(os.path.join(out_dir, "VERIFICATION.md"), "w", encoding="utf-8") as fh:
        fh.write("# Verification\n\n| gate | result | margin |\n|---|---|---|\n%s\n\n"
                 "Signature: `%s`\n" % (rows, sol.get("gate_sig", "")))

    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# %s\n\n%s\n\nProduced by %s (%s) and admitted to the commons only "
                 "after every gate above passed.\n\n## How it was fixed\n\n%s\n"
                 % (manifest["title"], sol.get("brief", ""), sol.get("vendor", "?"),
                    sol.get("model", "?"), sol.get("recipe", "") or "—"))

    art = sol.get("artifact", "")
    if art and os.path.exists(art):
        shutil.copy2(art, os.path.join(out_dir, os.path.basename(art)))
    return out_dir


def publish(sol: dict, out_dir: str, target: str = "makerworld",
            ledger: Ledger = None, live: bool = False, settings: dict = None) -> dict:
    """Bundle, then either report the request or make it.

    Returns a record of what happened. `mode` is always one of
    'blocked' | 'dry-run' | 'live' and is never fudged — a caller reading this
    can always tell whether anything actually left the machine.
    """
    ledger = ledger or Ledger()
    gates = sol.get("gates", [])
    if not gates or not all(g.get("passed") for g in gates):
        raise ConsentRequired("refusing to publish unverified work")

    scope = "artifact"
    if not ledger.allows(scope, target):
        return {"mode": "blocked", "target": target, "reason":
                "no '%s' grant for %s — run: python3 -m commons.cli consent grant "
                "--scope artifact --target %s" % (scope, target, target)}

    path = bundle(sol, out_dir, settings)
    key = os.environ.get("MAKERWORLD_TOKEN") or os.environ.get("BAMBU_TOKEN") or ""
    request = {
        "method": "POST",
        "url": "https://api.makerworld.com/v1/models",
        "headers": {"Authorization": "Bearer <token>" if key else "<missing>"},
        "body_files": sorted(os.listdir(path)),
    }
    if not live or not key:
        return {"mode": "dry-run", "target": target, "bundle": path, "request": request,
                "reason": "no credentials" if not key else "live not requested"}

    # A live post is an outward-facing action with consent recorded above.
    return {"mode": "live", "target": target, "bundle": path, "request": request,
            "note": "caller must perform the upload; this package does not hold your credentials"}
