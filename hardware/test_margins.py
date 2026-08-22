"""Tests for the closed-form margin gates.

    python3 -m hardware.test_margins
"""

from __future__ import annotations

import math

from .margins import (MATERIALS, GRADES, bending, solve_thickness, bolt_shear,
                      mass, thermal, select_fastener, evaluate, NoGroundTruth, G)
from .bracket import Bracket

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


ROWS = [
    {"grade": "8.8",  "dia_mm": 3, "tensile_mpa": 800,  "price_usd": 0.09, "in_stock": True},
    {"grade": "8.8",  "dia_mm": 4, "tensile_mpa": 800,  "price_usd": 0.14, "in_stock": True},
    {"grade": "10.9", "dia_mm": 4, "tensile_mpa": 1040, "price_usd": 0.23, "in_stock": True},
    {"grade": "8.8",  "dia_mm": 5, "tensile_mpa": 800,  "price_usd": 0.19, "in_stock": False},
    {"grade": "10.9", "dia_mm": 5, "tensile_mpa": 1040, "price_usd": 0.31, "in_stock": True},
]


def test_bending_against_hand_calc():
    print("\nbending — checked against the hand calculation")
    # M = 2.4 kg * 9.80665 * 0.090 m = 2.1182 N*m
    # sigma = 6M / (b t^2) = 6*2.1182 / (0.018 * 0.0032^2) = 68.9 MPa
    g = bending(2.4, 90, 18, 3.2, "PETG")
    hand = 6 * (2.4 * G * 0.090) / (0.018 * 0.0032 ** 2) / 1e6
    check("sigma matches the closed form to 0.1 MPa", abs(g.value - hand) < 0.1,
          "%.2f vs %.2f" % (g.value, hand))
    check("allowable is the material yield", g.allowable == MATERIALS["PETG"]["yield"])
    check("a 3.2 mm PETG web fails FoS 1.5", not g.against(1.5), "FoS %.2f" % g.margin)
    check("stress scales as 1/t^2",
          abs(bending(2.4, 90, 18, 6.4, "PETG").value - g.value / 4) < 0.1)
    check("stress scales linearly with load",
          abs(bending(4.8, 90, 18, 3.2, "PETG").value - g.value * 2) < 0.1)


def test_repair_is_exact():
    print("\nrepair — solving, not guessing")
    t = solve_thickness(2.4, 90, 18, 1.5, "PETG")
    g = bending(2.4, 90, 18, t, "PETG")
    check("solved thickness clears FoS 1.5 exactly", abs(g.margin - 1.5) < 0.01,
          "t=%.2f mm -> FoS %.3f" % (t, g.margin))
    check("the repair is thicker than the failing part", t > 3.2, "t=%.2f" % t)
    for fos in (1.2, 2.0, 3.0):
        tt = solve_thickness(2.4, 90, 18, fos, "PETG")
        gg = bending(2.4, 90, 18, tt, "PETG")
        check("solves exactly at FoS %.1f" % fos, abs(gg.margin - fos) < 0.01,
              "got %.3f" % gg.margin)


def test_bolts():
    print("\nfastener selection — driven by the scrape")
    pick = select_fastener(ROWS, 2.4, 2, 1.5)
    check("selects the cheapest viable row", pick and pick["unit_price"] == 0.09,
          str(pick.get("unit_price") if pick else None))
    out_of_stock = [dict(r, in_stock=(r["dia_mm"] != 3)) for r in ROWS]
    pick2 = select_fastener(out_of_stock, 2.4, 2, 1.5)
    check("skips out-of-stock rows", pick2 and pick2["row"]["dia_mm"] != 3)
    check("an empty scrape raises rather than defaulting",
          _raises(lambda: select_fastener([], 2.4, 2, 1.5), NoGroundTruth))
    check("shear falls as fastener count rises",
          bolt_shear(2.4, 4, 4).value < bolt_shear(2.4, 2, 4).value)
    check("higher grade raises the allowable",
          bolt_shear(2.4, 2, 4, "10.9").allowable > bolt_shear(2.4, 2, 4, "8.8").allowable)


def test_other_gates():
    print("\nmass and thermal")
    m = mass(18, 3.2, 90, "PETG", 60.0)
    hand = 0.018 * 0.0032 * 0.090 * MATERIALS["PETG"]["rho"] * 1000
    check("mass matches rho*V", abs(m.value - hand) < 0.05, "%.2f vs %.2f" % (m.value, hand))
    check("denser material is heavier",
          mass(18, 3.2, 90, "316-STAINLESS").value > m.value)
    t = thermal(2.0, 1200.0)
    check("thermal rise is P/(hA) above ambient",
          abs(t.value - (25.0 + 2.0 / (12.0 * 1200e-6))) < 0.1, "%.1f" % t.value)
    check("more power is hotter", thermal(4.0, 1200.0).value > t.value)
    check("more area is cooler", thermal(2.0, 2400.0).value < t.value)


def test_evaluate():
    print("\nthe whole gate set")
    bad = evaluate(2.4, 90, 18, 3.2, "PETG", 1.5, ROWS, 2)
    check("the failing design does not pass", not bad["passed"])
    check("bending is the gate that failed",
          [g.name for g in bad["failed"]] == ["physics.bend"],
          str([g.name for g in bad["failed"]]))
    check("a repair is offered", "repair" in bad)
    check("the repair names the parameter", bad["repair"]["parameter"] == "web_thickness")

    fixed = evaluate(2.4, 90, 18, bad["repair"]["to"], "PETG", 1.5, ROWS, 2)
    check("the repaired design passes every gate", fixed["passed"],
          str([g.name for g in fixed["failed"]]))
    check("no repair is offered when nothing failed", "repair" not in fixed)
    check("no scrape means no certification",
          _raises(lambda: evaluate(2.4, 90, 18, 3.2, "PETG", 1.5, [], 2), NoGroundTruth))


def test_geometry():
    print("\ngeometry — the mesh and the solid must agree")
    b = Bracket(width=18.0, thickness=3.2, arm=90.0)
    holes = b.hole_count * math.pi * (b.hole_dia / 2.0) ** 2 * b.thickness
    check("mesh volume exceeds the solid by exactly the hole volume",
          abs((b.mesh_volume_mm3() - b.volume_mm3()) - holes) < 0.01,
          "%.2f vs %.2f" % (b.mesh_volume_mm3() - b.volume_mm3(), holes))
    check("a closed mesh has 12 triangles per box, 24 for two",
          len(b.triangles()) == 24, str(len(b.triangles())))
    check("every triangle has a unit normal",
          all(abs(sum(c * c for c in _norm(t)) - 1.0) < 1e-6 for t in b.triangles()))
    thick = Bracket(width=18.0, thickness=4.61, arm=90.0)
    check("a thicker web is a bigger solid", thick.volume_mm3() > b.volume_mm3())
    check("mass scales with density",
          thick.mass_g(2700.0) > thick.mass_g(1270.0))
    check("volume is linear in width",
          abs(Bracket(width=36.0, thickness=3.2, arm=90.0).volume_mm3()
              - 2 * b.volume_mm3() - holes) < 0.01)

    import tempfile, os, struct
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "b.stl")
        n = b.to_stl(p)
        raw = open(p, "rb").read()
        count = struct.unpack("<I", raw[80:84])[0]
        check("STL header is 80 bytes and declares its triangle count",
              count == len(b.triangles()), str(count))
        check("STL length matches the binary format exactly",
              n == 84 + 50 * count, "%d vs %d" % (n, 84 + 50 * count))


def _norm(tri):
    from .bracket import _normal
    return _normal(*tri)


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


def main():
    print("hardware margins — test suite")
    test_bending_against_hand_calc()
    test_repair_is_exact()
    test_bolts()
    test_other_gates()
    test_evaluate()
    test_geometry()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
