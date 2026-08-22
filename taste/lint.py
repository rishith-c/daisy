"""
Tier-1 design lint — the taste gate, as an actual program.

"Beautiful" is treated as a failable gate, not an aspiration. This is the cheap
tier: pure grep/AST over the source, milliseconds, zero tokens, no model call.
It catches the ~20 named tells that make a UI read as generated rather than
designed. Tiers 2 (computed: contrast, overflow) and 3 (a vision judge scoring
named rubric gates) sit above it; this one runs on every save.

Findings are NAMED and carry file:line, because "your design scores 6.5/10" is
useless to an agent and a named gate is actionable:

    gate 3  indigo primary        tokens.css:12
    gate 11 unpaired default face layout.tsx:8

Run it on yourself:

    python3 -m taste.lint index.html

Exit code is the finding count, so it drops straight into a gate runner.
Zero third-party dependencies.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass


@dataclass
class Finding:
    gate: int
    name: str
    line: int
    excerpt: str
    why: str


# ---------------------------------------------------------------------------
# the tells
#
# Each is (gate number, name, compiled pattern, why it matters, optional
# exemption predicate). Order is stable so gate numbers mean something across
# runs and can be cited in a precedent record.
# ---------------------------------------------------------------------------

SLOP_HEX = re.compile(
    r"#(?:6366f1|818cf8|4f46e5|8b5cf6|a78bfa|7c3aed|6d28d9|c026d3)\b", re.I)
GRADIENT_TEXT = re.compile(r"bg-clip-text|background-clip:\s*text", re.I)
PURPLE_GRADIENT = re.compile(
    r"linear-gradient\([^)]*(?:#6366f1|#8b5cf6|indigo|violet|purple)[^)]*\)", re.I)
DEFAULT_FACE = re.compile(
    r"font-family:\s*(?:['\"]?)(Inter|Poppins|Geist|Space Grotesk|Montserrat)\1?\s*,?\s*(?:sans-serif)?\s*;", re.I)
CREAM_TERRACOTTA = re.compile(r"#(?:f4f1ea|faf7f0|e07a5f|e2725b|cc5500)\b", re.I)
TRANSITION_ALL = re.compile(r"transition:\s*all\b", re.I)
EASE_IN_UI = re.compile(r"transition[^;:]*:\s*[^;]*\bease-in\b(?!-out)", re.I)
# Pictographic emoji only. U+2713 CHECK MARK and friends are typographic
# symbols: they inherit currentColor and scale with the type, so they are not
# the tell. The tell is a colour-baked pictograph standing in for an icon.
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF]|.\uFE0F")
SOFT_SHADOW = re.compile(r"box-shadow:[^;]*rgba\(0,\s*0,\s*0,\s*0?\.1\)", re.I)
CENTERED = re.compile(r"text-align:\s*center", re.I)
RAW_PX_FONT = re.compile(r"font-size:\s*\d+px", re.I)

# Lines that are allowed to contain emoji: content that *is* the emoji, and
# generator/rule metadata that names them in order to ban them.
EMOJI_OK = re.compile(r"favicon|aria-label", re.I)

# Inline suppression. A file that documents the tells will necessarily contain
# them; a linter without an escape hatch just trains people to ignore it.
SUPPRESS = re.compile(r"taste-ok", re.I)


def _lines(src: str) -> list[str]:
    return src.split("\n")


def lint(src: str, path: str = "<src>") -> list[Finding]:
    out: list[Finding] = []
    lines = _lines(src)

    def scan(gate, name, pattern, why, guard=None):
        for i, ln in enumerate(lines, 1):
            if SUPPRESS.search(ln):
                continue
            if pattern.search(ln) and not (guard and guard(ln)):
                out.append(Finding(gate, name, i, ln.strip()[:96], why))

    scan(3, "indigo primary", SLOP_HEX,
         "the Tailwind default accent; it is the single most common generated-UI tell")
    scan(4, "purple gradient", PURPLE_GRADIENT,
         "indigo-to-violet gradients read as a template, not a decision")
    scan(5, "gradient text", GRADIENT_TEXT,
         "gradient headlines are decoration standing in for hierarchy")
    scan(7, "cream + terracotta", CREAM_TERRACOTTA,
         "the other default palette; distinctive only in that everyone uses it")
    scan(11, "unpaired default face", DEFAULT_FACE,
         "a lone Inter/Poppins stack with no paired display face")
    scan(13, "transition: all", TRANSITION_ALL,
         "animates properties you did not choose, including layout ones")
    scan(14, "ease-in on UI", EASE_IN_UI,
         "delays motion at the exact moment the user is watching; use ease-out")
    scan(15, "mean drop shadow", SOFT_SHADOW,
         "rgba(0,0,0,.1) is the statistical average shadow; layer hairlines instead")
    scan(17, "emoji as icons", EMOJI,
         "emoji render differently per platform and cannot inherit currentColor",
         guard=lambda ln: bool(EMOJI_OK.search(ln)))
    scan(19, "centered everything", CENTERED,
         "centre alignment used as a layout default rather than a choice")

    # ---- structural checks (whole-document, not line-level) ----
    if not re.search(r":focus-visible", src):
        out.append(Finding(21, "no focus-visible", 0, "",
                           "keyboard users get no visible focus state"))
    if not re.search(r"prefers-reduced-motion", src):
        out.append(Finding(22, "no reduced-motion", 0, "",
                           "motion is not opt-out for people who need it"))
    if re.search(r"<table", src, re.I) and not re.search(r"tabular-nums", src):
        out.append(Finding(23, "no tabular numerals", 0, "",
                           "numeric columns will jitter as digits change width"))
    if re.search(r"<h[12]", src, re.I) and not re.search(r"text-wrap:\s*balance", src):
        out.append(Finding(24, "unbalanced headings", 0, "",
                           "headings will rag badly without text-wrap: balance"))

    # a radius ladder: one radius for everything is a decision not made
    radii = set(re.findall(r"border-radius:\s*([\d.]+)px", src))
    if len(radii) == 1:
        out.append(Finding(25, "single radius", 0, sorted(radii)[0] + "px",
                           "every element sharing one radius means no size hierarchy"))

    # weight jumps: 400 -> 700 with nothing between
    weights = set(int(w) for w in re.findall(r"font-weight:\s*(\d{3})", src))
    if weights and 700 in weights and not (weights & {500, 550, 600}):
        out.append(Finding(26, "weight jump 400 to 700", 0, str(sorted(weights)),
                           "no mid weights; hierarchy is being carried by bold alone"))

    out.sort(key=lambda f: (f.gate, f.line))
    return out


def report(findings: list[Finding], path: str) -> str:
    if not findings:
        return "taste.t1  PASS  %s  —  0 findings across 20 tells" % path
    w = max(len(f.name) for f in findings)
    rows = ["taste.t1  FAIL  %s  —  %d finding%s"
            % (path, len(findings), "" if len(findings) == 1 else "s")]
    for f in findings:
        loc = "%s:%d" % (path, f.line) if f.line else path
        rows.append("  gate %-3d %-*s  %-28s  %s" % (f.gate, w, f.name, loc, f.why))
        if f.excerpt:
            rows.append("           %s" % f.excerpt)
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for path in argv[1:]:
        try:
            src = open(path, encoding="utf-8").read()
        except OSError as e:
            print("cannot read %s: %s" % (path, e))
            return 2
        f = lint(src, path)
        print(report(f, path))
        total += len(f)
    return total


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
