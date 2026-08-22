"""Tests for the tier-1 taste gate.

    python3 -m taste.test_lint
"""

from __future__ import annotations

import os

from .lint import lint, report

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


def gates(src):
    return sorted(f.gate for f in lint(src))


BASE = """
<style>
:root { --primary: var(--dna-accent); }
h1 { text-wrap: balance; font-weight: 600; }
b { font-weight: 700; }
.card { border-radius: 10px; }
.btn { border-radius: 6px; transition: transform 120ms cubic-bezier(.23,1,.32,1); }
:focus-visible { outline: none; }
@media (prefers-reduced-motion: reduce) { * { animation: none; } }
td { font-variant-numeric: tabular-nums; }
</style>
<h1>Title</h1><table><tr><td>1</td></tr></table>
"""


def test_clean():
    print("\na considered file passes")
    check("no findings on a clean file", lint(BASE) == [], gates(BASE))
    check("report says PASS", "PASS" in report(lint(BASE), "x"))


def test_tells():
    print("\neach tell is caught, by name")
    cases = [
        (3,  "--primary: #6366F1;"),
        (4,  "background: linear-gradient(90deg, #6366f1, #8b5cf6);"),
        (5,  "h1 { background-clip: text; }"),
        (7,  "body { background: #F4F1EA; }"),
        (11, "body { font-family: Inter, sans-serif; }"),
        (13, ".x { transition: all 300ms; }"),
        (14, ".x { transition: opacity 200ms ease-in; }"),
        (15, ".x { box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); }"),
        (19, ".x { text-align: center; }"),
    ]
    for gate, snippet in cases:
        found = gates(BASE + "\n" + snippet)
        check("gate %d catches %r" % (gate, snippet[:34]), gate in found, found)


def test_emoji_precision():
    print("\nemoji detection distinguishes pictographs from typography")
    check("a pictographic emoji is a finding", 17 in gates(BASE + '<span>\U0001F680</span>'))
    for glyph, what in [("✓", "check mark"), ("✕", "multiplication x"),
                        ("σ", "sigma"), ("≥", "greater-or-equal"),
                        ("·", "middle dot"), ("→", "right arrow")]:
        check("%s is typography, not a tell" % what,
              17 not in gates(BASE + "<span>%s</span>" % glyph))


def test_structural():
    print("\nstructural checks")
    check("missing focus-visible is caught", 21 in gates("<h1>x</h1><style>a{}</style>"))
    check("missing reduced-motion is caught", 22 in gates("<h1>x</h1><style>a{}</style>"))
    check("table without tabular-nums is caught",
          23 in gates("<style>:focus-visible{}@media (prefers-reduced-motion: reduce){}</style><table></table>"))
    one_radius = "<style>:focus-visible{} .a{border-radius: 8px} .b{border-radius: 8px} @media (prefers-reduced-motion: reduce){}</style>"
    check("a single radius for everything is caught", 25 in gates(one_radius))
    jump = "<style>:focus-visible{} .a{font-weight: 400} .b{font-weight: 700} @media (prefers-reduced-motion: reduce){}</style>"
    check("400->700 weight jump is caught", 26 in gates(jump))


def test_suppression():
    print("\nsuppression")
    line = "--primary: #6366F1;  /* taste-ok: documenting the tell */"
    check("taste-ok suppresses a finding", 3 not in gates(BASE + "\n" + line))
    check("without the marker it still fires", 3 in gates(BASE + "\n--primary: #6366F1;"))


def test_self():
    print("\nthe gate applied to the thing that defines it")
    idx = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
    if not os.path.exists(idx):
        check("index.html present", False, "not found"); return
    f = lint(open(idx, encoding="utf-8").read(), "index.html")
    check("Daisy passes its own taste gate", f == [],
          "; ".join("gate %d %s:%d" % (x.gate, x.name, x.line) for x in f[:6]))


def main():
    print("taste.t1 — test suite")
    test_clean(); test_tells(); test_emoji_precision()
    test_structural(); test_suppression(); test_self()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
