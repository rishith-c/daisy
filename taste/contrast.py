"""
Tier-2 taste gate — computed contrast.

Tier 1 greps for tells. Tier 2 computes things a human cannot eyeball reliably,
and contrast is the clearest case: three real WCAG failures shipped in this very
file before this checker existed, and all three looked fine on screen.

It reads the design tokens straight out of the stylesheet, resolves `rgba()`
over its surface, composites `color-mix(in lab, ...)` the way the browser will,
and computes the WCAG 2.1 contrast ratio for every declared text-on-surface
pair — in both themes.

    python3 -m taste.contrast index.html

Exit code is the number of failing pairs. Zero third-party dependencies.

Known limitation: the pair table below is maintained by hand, so a component
that stops using the token it is listed against will be checked against the
wrong ground. The mitigation is to give every checked surface its own token
(--pass-chip, --fail-chip, ...) and use only that token in the stylesheet, so
the CSS and this table cannot silently disagree.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# Which token sits on which surface, and at what size it is used.
# "small" = under 18.66px bold / 24px regular, so AA wants 4.5:1.
PAIRS = [
    # (text token,  surface token,      role,                       min ratio)
    ("--ink",       "--bg",             "body text",                4.5),
    ("--ink",       "--surface",        "text on a card",           4.5),
    ("--ink-2",     "--bg",             "secondary text",           4.5),
    ("--ink-3",     "--bg",             "tertiary text",            4.5),
    ("--ink-3",     "--surface",        "tertiary on a card",       4.5),
    ("--ink-3",     "--sidebar",        "sidebar labels",           4.5),
    ("--tip-ink",   "--tip-bg",         "tooltip label",            4.5),
    ("--tip-ink",   "--tip-kbd",        "tooltip shortcut badge",   4.5),
    ("--pass",      "--pass-chip",      "PASS chip",                4.5),
    ("--fail",      "--fail-chip",      "FAIL chip",                4.5),
    ("--warn",      "--warn-chip",      "warning chip",             4.5),
    ("--info",      "--info-chip",      "info chip",                4.5),
    ("--daisy-ink", "--daisy",          "primary button label",     4.5),
    ("--add-ink",   "--surface",        "diff additions",           4.5),
    ("--del-ink",   "--surface",        "diff deletions",           4.5),
    ("--ink-4",     "--bg",             "disabled / hint text",     3.0),
]


# ---------------------------------------------------------------------------
# colour resolution
# ---------------------------------------------------------------------------

def _srgb(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def ratio(fg: tuple, bg: tuple) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


HEX = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
RGBA = re.compile(r"^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?\s*\)$", re.I)
MIX = re.compile(r"^color-mix\(\s*in\s+[\w-]+\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*(.+?)\s*\)$", re.I)
VAR = re.compile(r"^var\(\s*(--[\w-]+)\s*\)$")


def parse_hex(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def composite(fg: tuple, alpha: float, bg: tuple) -> tuple:
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


def resolve(value: str, tokens: dict, over: tuple, depth: int = 0):
    """Resolve a token value to concrete RGB, compositing over `over`."""
    value = value.strip()
    if depth > 8:
        return None
    m = VAR.match(value)
    if m:
        return resolve(tokens.get(m.group(1), ""), tokens, over, depth + 1)
    if HEX.match(value):
        return parse_hex(value)
    m = RGBA.match(value)
    if m:
        rgb = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return composite(rgb, a, over)
    m = MIX.match(value)
    if m:
        a = resolve(m.group(1), tokens, over, depth + 1)
        pct = float(m.group(2)) / 100.0
        b = resolve(m.group(3), tokens, over, depth + 1)
        if a is None or b is None:
            return None
        return tuple(x * pct + y * (1 - pct) for x, y in zip(a, b))
    if value in ("transparent",):
        return over
    return None


# ---------------------------------------------------------------------------
# token extraction
# ---------------------------------------------------------------------------

DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def tokens_for(src: str, theme: str) -> dict:
    """Collect tokens as the cascade would resolve them for one theme."""
    out: dict[str, str] = {}

    # base :root block(s) — the light palette
    for m in re.finditer(r":root\s*\{([^}]*)\}", src):
        for k, v in DECL.findall(m.group(1)):
            out[k] = v.strip()

    if theme == "dark":
        # both the media-query and the explicit-attribute blocks
        for pat in (r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root[^{]*\{([^}]*)\}",
                    r':root\[data-theme="dark"\]\s*\{([^}]*)\}'):
            for m in re.finditer(pat, src):
                for k, v in DECL.findall(m.group(1)):
                    out[k] = v.strip()
    return out


@dataclass
class Result:
    theme: str
    role: str
    fg_tok: str
    bg_tok: str
    value: float
    need: float

    @property
    def ok(self) -> bool:
        return self.value >= self.need


def check(src: str) -> list[Result]:
    results: list[Result] = []
    for theme in ("light", "dark"):
        tk = tokens_for(src, theme)
        page = resolve(tk.get("--bg", "#fff"), tk, (255, 255, 255)) or (255, 255, 255)
        for fg_tok, bg_tok, role, need in PAIRS:
            if fg_tok not in tk or bg_tok not in tk:
                continue
            bg = resolve(tk[bg_tok], tk, page)
            if bg is None:
                continue
            fg = resolve(tk[fg_tok], tk, bg)
            if fg is None:
                continue
            results.append(Result(theme, role, fg_tok, bg_tok, ratio(fg, bg), need))
    return results


def report(results: list[Result], path: str) -> str:
    bad = [r for r in results if not r.ok]
    head = ("taste.t2  %s  %s  —  %d pair%s checked, %d failing"
            % ("PASS" if not bad else "FAIL", path, len(results),
               "" if len(results) == 1 else "s", len(bad)))
    rows = [head]
    for r in bad:
        rows.append("  %-5s %-22s %-11s on %-14s  %.2f:1  (needs %.1f)"
                    % (r.theme, r.role, r.fg_tok, r.bg_tok, r.value, r.need))
    if not bad:
        worst = min(results, key=lambda r: r.value - r.need)
        rows.append("  tightest: %s %s  %.2f:1 (needs %.1f)"
                    % (worst.theme, worst.role, worst.value, worst.need))
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for path in argv[1:]:
        src = open(path, encoding="utf-8").read()
        res = check(src)
        print(report(res, path))
        total += sum(1 for r in res if not r.ok)
    return total


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
