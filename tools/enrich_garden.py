#!/usr/bin/env python3
"""
Backfill Garden's entries with the runbook detail they were missing.

The six entries in this index are real failures this project's own gates
caught, so the detail below is recalled, not invented — the load cases, the
numbers and the commands are the ones that actually ran. Where something is
not known it is left empty rather than filled with something plausible; an
index of confident guesses is the failure this whole project argues against.

    python3 tools/enrich_garden.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from garden.detail import Detail, Step, completeness  # noqa: E402
from garden import index as gindex                    # noqa: E402

D = {}

D["physics.bend"] = Detail(
    symptom="A cantilever part fails its bending gate: computed stress exceeds the "
            "material allowable, and the reported factor of safety is below the target.",
    context="A bracket or arm carrying a tip load, sized by eye or copied from a "
            "part with a different load case.",
    detected_by="physics.bend — closed-form bending margin in hardware/margins.py",
    severity="blocking",
    root_cause="The web is too thin for the moment it carries. Bending stress goes as "
               "1/t^2, so a section that looks 'about right' is often out by a factor "
               "of two in margin.",
    why_it_happens="Thickness is usually chosen for printability or for how it looks in "
                   "CAD, not derived from the load. Nothing catches it until something "
                   "computes the moment.",
    confirm_with="Recompute sigma = 6M/(b*t^2) by hand for the stated load case. If it "
                 "matches the gate's value, the geometry is the problem and not the gate.",
    fix_summary="Do not guess a thicker web — invert the bending equation for t at the "
                "target factor of safety, patch that single parameter, regenerate the "
                "geometry, and re-run the gate.",
    steps=[
        Step("run", "python3 -m hardware.margins", "--load 2.4 --arm 90 --width 18 --fos 1.5",
             "get the solved thickness rather than picking one"),
        Step("edit", "hardware/bracket.py", "web_thickness = <solved value>",
             "one parameter; everything downstream is derived from it"),
        Step("run", "python3 -m hardware.bracket", "--thickness <solved> --out bracket.stl",
             "regenerate geometry from the patched parameter"),
        Step("check", "physics.bend", "margin >= target FoS",
             "the repair must clear the gate that asked for it"),
    ],
    parameters={"web_thickness": {"type": "float", "unit": "mm", "from": 3.2, "to": 4.61},
                "target_fos": {"type": "float", "unit": "-", "value": 1.5}},
    code=("import math\n"
          "def solve_thickness(load_kg, arm_mm, width_mm, fos, allow_pa):\n"
          "    moment = load_kg * 9.80665 * (arm_mm / 1000.0)\n"
          "    t = math.sqrt(6.0 * moment / ((width_mm/1000.0) * allow_pa / fos))\n"
          "    # round UP: rounding to nearest can land the repaired part\n"
          "    # fractionally under target and fail the gate that asked for it\n"
          "    return math.ceil(t * 1000.0 * 100.0) / 100.0"),
    load_case={"tip_load_kg": 2.4, "arm_mm": 90.0, "width_mm": 18.0,
               "material": "PETG", "yield_mpa": 50.0, "target_fos": 1.5},
    verify_commands=["python3 -m hardware.test_margins",
                     "python3 labctl.py run --brief '...' --lane hardware"],
    expected="physics.bend margin >= 1.5 after the repair; the run reports "
             "'repair: web_thickness 3.2 -> 4.61 mm (solved, not guessed)'.",
    applies_when=["a cantilever section fails a bending margin",
                  "the load case is static and known",
                  "thickness is a free parameter you are allowed to change"],
    does_not_apply_when=[
        "the load is cyclic — this is a static yield check and says nothing about fatigue",
        "the section buckles rather than yields; slender webs fail by buckling first "
        "and this equation will report a comfortable margin on a part that folds",
        "the load is an impact — dynamic amplification is not in the closed form",
        "thickness is fixed by fit or by stock and cannot be changed; widen b or "
        "shorten the arm instead",
    ],
    preconditions=["the material yield strength is known",
                   "the moment arm and load are stated, not assumed"],
    related_gates=["physics.mass", "physics.thermal", "geometry.closed"],
    tags=["hardware", "structures", "closed-form", "repair-by-algebra"],
)

D["physics.mass"] = Detail(
    symptom="Reported part mass is materially lower than the part actually weighs.",
    context="Mass computed from geometry during a design gate.",
    detected_by="physics.mass, cross-checked against the mesh",
    severity="degraded",
    root_cause="Mass was computed from the bounding box rather than the solid.",
    why_it_happens="A bounding box is the easiest volume to reach and is right only for "
                   "a rectangular prism. Any L-shape, cut-out or hole makes it wrong, and "
                   "wrong in the flattering direction.",
    confirm_with="Compute the volume twice — analytically from the solid, and from the mesh "
                 "by the divergence theorem. If they disagree, one of them is lying.",
    fix_summary="Take volume from the analytic solid minus the holes, and cross-check it "
                "against the mesh. Two independent derivations that agree is the test.",
    steps=[
        Step("edit", "hardware/bracket.py", "volume = web + foot - holes",
             "count the corner overlap once, subtract hole volume"),
        Step("check", "geometry.closed", "mesh_volume - solid_volume == hole_volume",
             "the difference must be exactly the holes, not approximately"),
    ],
    parameters={"reported_mass_g": {"type": "float", "unit": "g", "from": 6.6, "to": 8.3}},
    code=("def volume_mm3(self):\n"
          "    web  = self.width * self.thickness * self.arm\n"
          "    foot = self.width * self.thickness * (self.foot - self.thickness)\n"
          "    holes = self.hole_count * math.pi * (self.hole_dia/2)**2 * self.thickness\n"
          "    return web + foot - holes"),
    verify_commands=["python3 -m hardware.test_margins"],
    expected="mesh volume exceeds the solid by exactly the hole volume, to 0.01 mm^3.",
    applies_when=["mass or volume is derived from geometry",
                  "the part is not a simple prism"],
    does_not_apply_when=[
        "the mesh has fillets, lofts or booleans — hand-built geometry is honest only "
        "for convex slabs; use a kernel and say so",
        "density is uncertain, in which case the volume is not the error source",
    ],
    preconditions=["the mesh is closed", "density is known for the material"],
    related_gates=["physics.bend", "geometry.closed"],
    tags=["hardware", "geometry", "cross-validation"],
)

D["taste.t2"] = Detail(
    symptom="Text that looks fine on screen fails a contrast check — sometimes text a "
            "human has already 'fixed' by eye.",
    context="A design system with tokenised colours, rendered in light and dark.",
    detected_by="taste.t2 — computed WCAG contrast over every declared token pair",
    severity="blocking",
    root_cause="Contrast was judged by looking. Human eyes adapt to a screen; the ratio "
               "does not.",
    why_it_happens="rgba() and color-mix() only resolve against the surface behind them, "
                   "so a colour that is legible on one ground is not on another, and no "
                   "amount of staring reveals the number.",
    confirm_with="Compute the ratio: resolve the token over its real surface in both "
                 "themes and compare against 4.5:1 for body text, 3.0:1 for hints.",
    fix_summary="Stop judging and start computing. Give every checked surface its own "
                "token so the stylesheet and the checker cannot silently disagree.",
    steps=[
        Step("edit", "tokens", "--pass-chip, --fail-chip, --warn-chip",
             "a dedicated token per checked surface"),
        Step("edit", "checker PAIRS table", "(fg_token, bg_token, role, min_ratio)",
             "register the pair so it is actually checked"),
        Step("run", "python3 -m taste.contrast index.html", "",
             "exit code is the number of failing pairs"),
    ],
    parameters={"body_text_min": {"type": "float", "value": 4.5},
                "hint_text_min": {"type": "float", "value": 3.0}},
    code=("# resolve rgba()/color-mix() over the real surface, both themes,\n"
          "# then WCAG 2.1: (L1 + 0.05) / (L2 + 0.05)"),
    verify_commands=["python3 -m taste.contrast index.html"],
    expected="0 failing pairs; the tightest ratio is reported so regressions are visible "
             "before they cross the line.",
    applies_when=["a UI declares colours as tokens",
                  "both light and dark themes are shipped"],
    does_not_apply_when=[
        "colour is decorative and carries no information — contrast rules are about "
        "legibility, not aesthetics",
        "the surface behind the text is an image or a gradient; a single ratio is "
        "meaningless there and needs a different treatment",
    ],
    preconditions=["colours are declared as tokens, not inline literals"],
    related_gates=["taste.t1"],
    tags=["frontend", "accessibility", "wcag", "computed-not-judged"],
)

D["scrape.schema"] = Detail(
    symptom="A scraper returns HTTP 200 and the expected number of rows, but fields are "
            "silently missing from every row.",
    context="A collector pointed at a vendor page that was restructured overnight.",
    detected_by="scrape.schema — required-field and type conformance, not HTTP status",
    severity="blocking",
    root_cause="Selectors were anchored to markup. The markup changed; the data did not.",
    why_it_happens="This is the failure mode that matters and the one nobody catches: the "
                   "request succeeds, so error handling never fires. A missing price is "
                   "not an exception, it is a None that flows downstream.",
    confirm_with="Compare row count and per-field fill rate against the last good "
                 "baseline. A 200 with fewer keys per row is the signature.",
    fix_summary="Re-derive selectors by anchoring on the last-good field *values* rather "
                "than on tags or classes, then refuse the repair unless health passes on "
                "the new spec.",
    steps=[
        Step("run", "python3 -m scrape.cli check", "--fixture <page>",
             "exit 1 means drift, not a network error"),
        Step("run", "python3 -m scrape.cli repair", "--fixture <page>",
             "proposes a new spec; writes nothing"),
        Step("check", "selector diff", "review old -> new",
             "a human sees the change before it lands"),
        Step("run", "python3 -m scrape.cli repair", "--fixture <page> --accept",
             "only after the diff is reviewed"),
    ],
    parameters={"freshness_ttl_s": {"type": "int", "unit": "s", "value": 900}},
    code=("# anchor on data, not markup: find the element whose text equals a\n"
          "# known-good value, then derive its selector from that position"),
    verify_commands=["python3 -m scrape.cli check --fixture <page>",
                     "python3 -m scrape.test_scrape"],
    expected="check exits 0 with the original row count restored and every required "
             "field present.",
    applies_when=["a scraper's output shape changed without an error",
                  "a last-good extraction exists to anchor on"],
    does_not_apply_when=[
        "there is no last-good baseline — there is nothing to anchor to and the "
        "repair has no ground truth",
        "the site genuinely stopped publishing the field; re-deriving a selector for "
        "data that no longer exists will produce a confident empty column",
        "the page is behind a login or renders client-side only",
    ],
    preconditions=["a baseline with per-field fill rates", "rules are version-controlled"],
    related_gates=["scrape.freshness", "physics.fastener"],
    tags=["data", "self-healing", "value-anchored", "bright-data"],
)

D["taste.t1"] = Detail(
    symptom="Generated UI carries the tells of a template: colours declared outside the "
            "token system, one radius everywhere, emoji standing in for icons.",
    context="An agent asked to produce a frontend without a design system in front of it.",
    detected_by="taste.t1 — 20 named design tells",
    severity="degraded",
    root_cause="Nothing named the standard, so the model reached for its priors.",
    why_it_happens="'Make it look good' is not a specification. A model given no "
                   "constraints produces the median of its training data, which is "
                   "exactly the look people recognise as generated.",
    confirm_with="Run the linter. Each finding names the tell and the line, so it is "
                 "checkable rather than arguable.",
    fix_summary="Feed the named findings back into the retry verbatim. 'Make it better' "
                "is not a repair instruction; a named tell with a line number is.",
    steps=[
        Step("run", "python3 -m taste.lint <file>", "", "exit code is the finding count"),
        Step("note", "retry prompt", "include each finding's name, line and excerpt",
             "the specificity is the whole mechanism"),
        Step("check", "taste.t1", "0 findings", ""),
    ],
    parameters={"max_findings": {"type": "int", "value": 0}},
    code="",
    verify_commands=["python3 -m taste.lint index.html"],
    expected="0 findings across 20 tells.",
    applies_when=["an agent generated UI and it must match an existing design language"],
    does_not_apply_when=[
        "the project has no design system to conform to — the tells encode one house "
        "style and imposing it elsewhere is just a different arbitrary taste",
        "the file is a fixture or a test that quotes a tell on purpose",
    ],
    preconditions=["a token system exists to conform to"],
    related_gates=["taste.t2"],
    tags=["frontend", "design", "retry-with-findings"],
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    path = gindex.clone_path()
    base = os.path.join(path, "solutions")
    if not os.path.isdir(base):
        print("no local garden at %s" % path); return 1

    touched = 0
    for slug in sorted(os.listdir(base)):
        mf = os.path.join(base, slug, "manifest.json")
        if not os.path.exists(mf):
            continue
        m = json.load(open(mf, encoding="utf-8"))
        sig = (m.get("verified") or {}).get("gate_signature", "")
        gates = [s.split("=")[0] for s in sig.split("|") if s]
        det = next((D[g] for g in gates if g in D), None)
        if det is None:
            print("  no detail written for %s (%s) — left thin rather than invented"
                  % (slug[:44], ",".join(gates) or "no gates"))
            continue
        m["detail"] = det.to_manifest()
        m["completeness"] = completeness(m["detail"])
        if not a.dry_run:
            json.dump(m, open(mf, "w", encoding="utf-8"), indent=1)
        touched += 1
        print("  %-46s %s  %d/%d blocks"
              % (slug[:46], ",".join(gates)[:22],
                 m["completeness"]["have"], m["completeness"]["of"]))
    print("\n%s %d entries" % ("would enrich" if a.dry_run else "enriched", touched))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
