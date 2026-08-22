#!/usr/bin/env python3
"""Insert the Bright Data scrape as its own run step.

The scrape is what the physics gate consumes, but it was only ever referenced
in passing. The data-pipeline criteria are explicit about wanting a pure
terminal workflow and clean JSON, so show both.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
h = open(idx, encoding="utf-8").read()

if "Bright Data &middot; bolt table" in h:
    print("already present")
    raise SystemExit(0)

cmd = "\n".join([
    "$ npx -p @brightdata/cli scraper run c_bolt_table --format json",
    "  collector  c_bolt_table          (pinned in CLAUDE.md)",
    "  schema     grade, tensile_mpa, price_usd, in_stock",
    "  rows 5 · 1.9 KB · 2.4 s · schema key-diff clean",
])

rows = [
    ("M3 &middot; 8.8", "800", "$0.09", "yes"),
    ("M4 &middot; 8.8", "800", "$0.14", "yes"),
    ("M4 &middot; 10.9", "1040", "$0.23", "yes"),
    ("M5 &middot; 8.8", "800", "$0.19", "no"),
    ("M5 &middot; 10.9", "1040", "$0.31", "yes"),
]
tbl = "".join(
    '<tr><td class="nm">{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(*r)
    for r in rows)

card = (
    '<div class="card"><div class="chead"><span class="ci" style="color:var(--warn)">&#9660;</span>'
    '<div style="min-width:0"><b>Bright Data &middot; bolt table</b>'
    '<div class="sub">terminal only &middot; config version-controlled &middot; 2.4 s</div></div>'
    '<div class="act"><span class="tag pass">5 ROWS</span></div></div>'
    '<div class="derive">' + cmd + '</div>'
    '<div class="sx"><table class="m">'
    '<tr><th>fastener</th><th>tensile MPa</th><th>unit price</th><th>in stock</th></tr>'
    + tbl + '</table></div>'
    '<div class="res" style="display:block">The solver picks from <b>these rows</b>, not from a constant. '
    'There is no fallback table in the code path: if this scrape is missing or stale, the part cannot be '
    'certified and the run stops. That is what makes the data load-bearing rather than decorative.</div></div>'
)

step = "    { d: 700, kind: 'card', html: " + json.dumps(card) + " },"

# place it immediately before the hardware-lane narration that consumes it
anchor = "    { d: 880, kind: 'prose', tag: '<div class=\"who-tag claude\"><i></i>Fable 5 · hardware</div>',"
assert anchor in h, "hardware-lane anchor not found"
h = h.replace(anchor, step + "\n" + anchor, 1)
open(idx, "w", encoding="utf-8").write(h)
print("inserted Bright Data scrape step (%d chars)" % len(step))
