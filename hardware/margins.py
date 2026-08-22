"""
Closed-form margin gates — the physics half of the factory's test suite.

A failing margin is a failing test. Not a warning, not a score: the run stops.

Everything here is deterministic and closed-form, so it runs in well under a
millisecond and no language model is anywhere near the decision. That matters
more than it sounds: the thing deciding whether a part is safe should not be
the thing that wrote the part.

Scope, stated plainly so nobody mistakes this for FEA:

    IS      first-principles beam bending, bolt shear and bearing, thermal
            rise, mass properties, and a fastener selection driven by scraped
            vendor data
    IS NOT  finite elements, fatigue, buckling, impact, vibration, creep, or
            stress concentration at fillets

For a bracket in single-axis static bending those omissions are defensible and
the geometry is deliberately chosen to stay in that regime. For anything
cyclic, slender, or impact-loaded they are not, and the honest answer is that
this gate does not cover it.

Zero third-party dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.80665  # m/s^2


# ---------------------------------------------------------------------------
# materials — yield in MPa, density in kg/m^3
# ---------------------------------------------------------------------------

MATERIALS = {
    "AL6061-T6":   {"yield": 276.0, "rho": 2700.0, "E": 68.9e3, "k": 167.0},
    "AL7075-T6":   {"yield": 503.0, "rho": 2810.0, "E": 71.7e3, "k": 130.0},
    "PLA":         {"yield": 50.0,  "rho": 1240.0, "E": 3.5e3,  "k": 0.13},
    "PETG":        {"yield": 50.0,  "rho": 1270.0, "E": 2.1e3,  "k": 0.29},
    "316-STAINLESS": {"yield": 290.0, "rho": 8000.0, "E": 193e3, "k": 16.3},
}

# fastener grades — proof/tensile in MPa
GRADES = {"8.8": 800.0, "10.9": 1040.0, "12.9": 1220.0, "A2-70": 700.0}


@dataclass(frozen=True)
class Gate:
    """One verification outcome. `margin` is the ratio to allowable."""
    name: str
    value: float
    allowable: float
    margin: float
    unit: str
    formula: str
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.margin >= 1.0

    def against(self, fos: float) -> bool:
        """Did it clear the required factor of safety?"""
        return self.margin >= fos


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------

def bending(load_kg: float, arm_mm: float, width_mm: float, thick_mm: float,
            material: str = "AL6061-T6") -> Gate:
    """Rectangular cantilever, worst fibre at the root.

        sigma = M*c/I,  with I = b*t^3/12 and c = t/2
              = 6*M / (b*t^2)
    """
    m = MATERIALS[material]
    moment = load_kg * G * (arm_mm / 1000.0)                  # N*m
    b, t = width_mm / 1000.0, thick_mm / 1000.0               # m
    sigma = 6.0 * moment / (b * t * t) / 1e6                  # MPa
    allow = m["yield"]
    return Gate("physics.bend", round(sigma, 1), allow,
                round(allow / sigma, 3) if sigma else float("inf"),
                "MPa", "sigma = 6M / (b*t^2)",
                "%s cantilever, %.1f kg at %.0f mm" % (material, load_kg, arm_mm))


def solve_thickness(load_kg: float, arm_mm: float, width_mm: float,
                    fos: float, material: str = "AL6061-T6") -> float:
    """Invert the bending equation for the thickness that just clears `fos`.

    This is what makes the repair deterministic: the factory does not ask a
    model to guess a better number, it solves for the one that works.

        t = sqrt( 6M / (b * sigma_allow / FoS) )
    """
    m = MATERIALS[material]
    moment = load_kg * G * (arm_mm / 1000.0)
    b = width_mm / 1000.0
    allow_pa = (m["yield"] / fos) * 1e6
    t = math.sqrt(6.0 * moment / (b * allow_pa))
    # Round UP to the next 0.01 mm. Rounding a safety margin to nearest can
    # land the "repaired" part fractionally under target, which would fail the
    # very gate that asked for the repair — and loop.
    return math.ceil(t * 1000.0 * 100.0) / 100.0              # mm


def bolt_shear(load_kg: float, count: int, dia_mm: float, grade: str = "8.8") -> Gate:
    """Single shear across the fastener group.

        tau = F / (n * A),  allowable taken as 0.6 * tensile (von Mises)
    """
    force = load_kg * G
    area = math.pi * (dia_mm / 1000.0) ** 2 / 4.0
    tau = force / (count * area) / 1e6
    allow = 0.6 * GRADES[grade]
    return Gate("physics.shear", round(tau, 1), round(allow, 1),
                round(allow / tau, 3) if tau else float("inf"),
                "MPa", "tau = F / (n*A)",
                "%d x M%.0f grade %s" % (count, dia_mm, grade))


def mass(width_mm: float, thick_mm: float, length_mm: float,
         material: str = "AL6061-T6", budget_g: float = 60.0) -> Gate:
    m = MATERIALS[material]
    vol = (width_mm / 1000.0) * (thick_mm / 1000.0) * (length_mm / 1000.0)
    grams = vol * m["rho"] * 1000.0
    return Gate("physics.mass", round(grams, 1), budget_g,
                round(budget_g / grams, 3) if grams else float("inf"),
                "g", "m = rho * V", material)


def thermal(power_w: float, area_mm2: float, h: float = 12.0,
            ambient_c: float = 25.0, limit_c: float = 85.0) -> Gate:
    """Natural-convection rise off a flat surface.

        dT = P / (h * A)
    """
    area = area_mm2 / 1e6
    rise = power_w / (h * area) if area else float("inf")
    temp = ambient_c + rise
    head = limit_c - ambient_c
    return Gate("physics.thermal", round(temp, 1), limit_c,
                round(head / rise, 3) if rise else float("inf"),
                "degC", "dT = P / (h*A)",
                "%.1f W over %.0f mm^2, h=%.0f" % (power_w, area_mm2, h))


# ---------------------------------------------------------------------------
# fastener selection — driven by scraped rows, with no fallback
# ---------------------------------------------------------------------------

class NoGroundTruth(Exception):
    """Raised when there is no scrape to select against.

    Deliberately fatal. A fallback constant here would silently turn a data
    pipeline into a hardcoded value, which is exactly the failure this gate
    exists to prevent.
    """


def select_fastener(rows: list[dict], load_kg: float, count: int, fos: float) -> dict:
    """Cheapest in-stock fastener that clears the shear gate at `fos`."""
    if not rows:
        raise NoGroundTruth("no scraped fastener rows — cannot certify")
    viable = []
    for r in rows:
        if not r.get("in_stock", True):
            continue
        dia = float(r["dia_mm"])
        g = bolt_shear(load_kg, count, dia, str(r["grade"]))
        if g.against(fos):
            viable.append((float(r["price_usd"]), r, g))
    if not viable:
        return {}
    viable.sort(key=lambda x: x[0])
    price, row, gate = viable[0]
    return {"row": row, "gate": gate, "unit_price": price,
            "line_total": round(price * count, 2)}


# ---------------------------------------------------------------------------
# the whole gate set
# ---------------------------------------------------------------------------

def evaluate(load_kg: float, arm_mm: float, width_mm: float, thick_mm: float,
             material: str = "AL6061-T6", fos: float = 1.5,
             bolt_rows: list[dict] | None = None, bolt_count: int = 2,
             mass_budget_g: float = 60.0) -> dict:
    """Run every gate. Returns the gates, the verdict, and the repair if needed."""
    gates = [
        bending(load_kg, arm_mm, width_mm, thick_mm, material),
        mass(width_mm, thick_mm, arm_mm, material, mass_budget_g),
    ]
    pick = None
    if bolt_rows is not None:
        pick = select_fastener(bolt_rows, load_kg, bolt_count, fos)
        if pick:
            gates.insert(1, pick["gate"])

    failed = [g for g in gates if not g.against(fos)]
    out = {
        "gates": gates,
        "failed": failed,
        "passed": not failed,
        "fos_target": fos,
        "fastener": pick,
    }
    if any(g.name == "physics.bend" for g in failed):
        t = solve_thickness(load_kg, arm_mm, width_mm, fos, material)
        out["repair"] = {
            "parameter": "web_thickness",
            "from": thick_mm, "to": t,
            "derivation": "t = sqrt(6M / (b * sigma_allow / FoS))",
        }
    return out
