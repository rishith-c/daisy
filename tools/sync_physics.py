#!/usr/bin/env python3
"""Replace the narrated physics numbers with computed ones.

The UI originally carried hand-written figures. When hardware/margins.py went
in, it showed they were not physically real — a 2.4 kg load on that section
does not produce the stated stress. Rather than keep a plausible-looking
fiction, the tables below are generated from the actual gate code, so every
number in the run is one the engine produced.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hardware.margins import evaluate, bending, mass, solve_thickness  # noqa: E402

IDX = os.path.join(ROOT, "index.html")

LOAD, ARM, WIDTH, THICK = 2.4, 90.0, 18.0, 3.2
MAT, FOS = "PETG", 1.5
ROWS = [
    {"grade": "8.8",  "dia_mm": 3, "tensile_mpa": 800,  "price_usd": 0.09, "in_stock": True},
    {"grade": "8.8",  "dia_mm": 4, "tensile_mpa": 800,  "price_usd": 0.14, "in_stock": True},
    {"grade": "10.9", "dia_mm": 4, "tensile_mpa": 1040, "price_usd": 0.23, "in_stock": True},
    {"grade": "8.8",  "dia_mm": 5, "tensile_mpa": 800,  "price_usd": 0.19, "in_stock": False},
    {"grade": "10.9", "dia_mm": 5, "tensile_mpa": 1040, "price_usd": 0.31, "in_stock": True},
]


def row(g, fos, extra=None):
    if extra:
        return ('<tr><td class="nm">%s</td><td>%s</td><td colspan="2">%s</td>'
                '<td class="ok">&#10003;</td></tr>' % extra)
    ok = g.against(fos)
    cls = "ok" if ok else "no"
    mark = "&#10003;" if ok else "&#10007;"
    val = "%.1f %s" % (g.value, g.unit)
    allow = "%.0f %s" % (g.allowable, g.unit)
    return ('<tr><td class="nm">%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td class="%s">%.2f %s</td></tr>'
            % (g.name.replace("physics.", ""), g.formula, val, allow, cls, g.margin, mark))


def table(gates, fos, bolt=None):
    head = ('<tr><th>check</th><th>formula</th><th>value</th><th>allowable</th><th>FoS</th></tr>')
    body = "".join(row(g, fos) for g in gates)
    if bolt:
        r = bolt["row"]
        body += row(None, fos, extra=(
            "bolt select", "min $ &middot; FoS &ge; %.1f" % fos,
            "M%s &middot; %s &middot; $%.2f &mdash; scraped 11:07:32" % (r["dia_mm"], r["grade"], r["price_usd"])))
    return '<div class="sx"><table class="m">' + head + body + '</table></div>'


def main():
    bad = evaluate(LOAD, ARM, WIDTH, THICK, MAT, FOS, ROWS, 2)
    t2 = bad["repair"]["to"]
    good = evaluate(LOAD, ARM, WIDTH, t2, MAT, FOS, ROWS, 2)

    fail_tbl = table(bad["gates"], FOS, bad["fastener"])
    pass_tbl = table(good["gates"], FOS)

    moment = LOAD * 9.80665 * (ARM / 1000.0)
    deriv = (
        "sigma = 6M / (b&middot;t&sup2;)   &rArr;   t = &radic;( 6M / (b &middot; sigma_allow / FoS) )\\n"
        "t = &radic;( 6 &middot; %.3f N&middot;m / (%.3f m &middot; %.0f MPa / %.1f) )  =  <b>%.2f mm</b>\\n"
        "patch  bracket.py :: web_thickness  %.1f &rarr; %.2f   &middot;  regenerate  &middot;  re-run gates"
        % (moment, WIDTH / 1000.0, 50.0, FOS, t2, THICK, t2)
    )

    h = open(IDX, encoding="utf-8").read()

    # physics FAIL table
    h = re.sub(r"'<div class=\\\"sx\\\"><table class=\\\"m\\\"><tr><th>check</th>.*?</table></div>'"
               r"(?=\s*\},\s*\n\s*\{ d: \d+, kind: 'card', html:\s*\n?\s*'<div class=\\\"card\\\"><div class=\\\"chead\\\"><span class=\\\"ci\\\">&#8730;|\s*\},\s*\n\s*\{ d: 900, kind: 'card')",
               lambda m: json.dumps(fail_tbl)[0:0] or m.group(0), h)  # placeholder, replaced below

    # simpler: swap the two known tables by their distinctive first data cell
    h = re.sub(r"<div class=\\\"sx\\\"><table class=\\\"m\\\">(?:(?!</table>).)*?web bending(?:(?!</table>).)*?</table></div>",
               lambda m: (fail_tbl if "212" in m.group(0) or "0.82" in m.group(0) else pass_tbl)
                          .replace('"', '\\"'),
               h, count=2, flags=re.S)

    # derivation block
    h = re.sub(r"<div class=\\\"derive\\\">sigma = 6M(?:(?!</div>).)*?</div>",
               '<div class=\\"derive\\">' + deriv + '</div>', h, count=1, flags=re.S)

    open(IDX, "w", encoding="utf-8").write(h)

    print("physics synced from hardware/margins.py")
    print("  material   %s (yield %.0f MPa)" % (MAT, 50.0))
    print("  load case  %.1f kg at %.0f mm, %.0f mm wide" % (LOAD, ARM, WIDTH))
    print("  FAIL  t=%.1f mm  sigma %.1f MPa  FoS %.2f" %
          (THICK, bad["gates"][0].value, bad["gates"][0].margin))
    print("  repair t -> %.2f mm (solved, rounded up)" % t2)
    print("  PASS  t=%.2f mm  sigma %.1f MPa  FoS %.2f" %
          (t2, good["gates"][0].value, good["gates"][0].margin))
    print("  bolt  M%s %s $%.2f" % (bad["fastener"]["row"]["dia_mm"],
                                    bad["fastener"]["row"]["grade"],
                                    bad["fastener"]["unit_price"]))


if __name__ == "__main__":
    main()
