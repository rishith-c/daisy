#!/usr/bin/env python3
"""Generate the small in-app Daisy brand mark.

Same seeded hand-drawn flower as the app icon, but:
  - no background plate
  - strokes/fills use CSS custom properties so it reads in light AND dark
  - fewer, chunkier petals so it survives at 22px
Writes mark.svg and injects it into index.html at the __MARK__ placeholder.
"""
import math, random, json, os, re

random.seed(11)
cx, cy = 50.0, 50.0

def j(m=1.2):
    return random.uniform(-m, m)

petals, veins = [], []
n = 9
for i in range(n):
    a = math.radians(i * 360.0 / n + random.uniform(-6, 6))
    L = random.uniform(41, 46)
    W = random.uniform(9.5, 12.0)
    r0 = 15.0
    ux, uy = math.cos(a), math.sin(a)
    px, py = -uy, ux

    def pt(d, w, jx=0.0, jy=0.0):
        return (cx + ux * d + px * w + jx, cy + uy * d + py * w + jy)

    bl = pt(r0, -W * 0.45, j(.8), j(.8))
    ml = pt(r0 + (L - r0) * 0.55, -W, j(), j())
    tp = pt(L, random.uniform(-2, 2), j(.9), j(.9))
    mr = pt(r0 + (L - r0) * 0.55, W, j(), j())
    br = pt(r0, W * 0.45, j(.8), j(.8))
    petals.append(
        "M{:.1f} {:.1f}Q{:.1f} {:.1f} {:.1f} {:.1f}Q{:.1f} {:.1f} {:.1f} {:.1f}".format(
            bl[0], bl[1], ml[0], ml[1], tp[0], tp[1], mr[0], mr[1], br[0], br[1]))

pts = []
m = 9
for k in range(m):
    ang = math.radians(k * 360.0 / m)
    r = 16.5 + random.uniform(-1.4, 1.4)
    pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
disc = "M{:.1f} {:.1f}".format(*pts[0])
for k in range(1, m + 1):
    p, prev = pts[k % m], pts[k - 1]
    mid = ((prev[0] + p[0]) / 2 + j(.7), (prev[1] + p[1]) / 2 + j(.7))
    disc += "Q{:.1f} {:.1f} {:.1f} {:.1f}".format(mid[0], mid[1], p[0], p[1])
disc += "Z"

out = ['<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">']
for d in petals:
    out.append('<path d="{}" fill="var(--bg)" stroke="var(--ink-3)" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
out.append('<path d="{}" fill="var(--daisy)" stroke="var(--daisy-deep)" stroke-width="3.2" stroke-linejoin="round"/>'.format(disc))
out.append('</svg>')
svg = "".join(out)

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "mark.svg"), "w") as f:
    f.write(svg + "\n")

idx = os.path.join(here, "..", "index.html")
html = open(idx).read()
if "__MARK__" in html:
    html = html.replace("'__MARK__'", json.dumps(svg))
    open(idx, "w").write(html)
    print("injected mark into index.html ({} bytes)".format(len(svg)))
else:
    # replace an already-injected mark
    html2 = re.sub(r"function DAISY_MARK\(\) \{ return .*?; \}",
                   "function DAISY_MARK() { return " + json.dumps(svg) + "; }", html, flags=re.S)
    open(idx, "w").write(html2)
    print("re-injected mark ({} bytes)".format(len(svg)))
