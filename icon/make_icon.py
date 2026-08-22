#!/usr/bin/env python3
"""Generate the hand-drawn Daisy app icon as SVG (seeded wobble, reproducible)."""
import math, random

random.seed(7)
cx, cy = 512.0, 530.0

def j(m=8.0):
    return random.uniform(-m, m)

petals = []
veins = []
n = 12
for i in range(n):
    a = math.radians(i * 360.0 / n + random.uniform(-7, 7))
    L = random.uniform(305, 350)
    W = random.uniform(64, 84)
    r0 = 116.0
    ux, uy = math.cos(a), math.sin(a)
    px, py = -uy, ux

    def pt(d, w, jx=0.0, jy=0.0):
        return (cx + ux * d + px * w + jx, cy + uy * d + py * w + jy)

    base_l = pt(r0, -W * 0.42, j(5), j(5))
    mid_l = pt(r0 + (L - r0) * 0.55, -W, j(), j())
    tip = pt(L, random.uniform(-12, 12), j(6), j(6))
    mid_r = pt(r0 + (L - r0) * 0.55, W, j(), j())
    base_r = pt(r0, W * 0.42, j(5), j(5))
    petals.append(
        "M {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f}".format(
            base_l[0], base_l[1], mid_l[0], mid_l[1], tip[0], tip[1],
            mid_r[0], mid_r[1], base_r[0], base_r[1]))
    vb = pt(r0 + 14, j(6))
    vm = pt(r0 + (L - r0) * 0.5, j(10))
    vt = pt(L - 46, j(8))
    veins.append("M {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f}".format(
        vb[0], vb[1], vm[0], vm[1], vt[0], vt[1]))

# wobbly center disc: closed loop of quadratic segments
pts = []
m = 11
for k in range(m):
    ang = math.radians(k * 360.0 / m)
    r = 128 + random.uniform(-9, 9)
    pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
disc = "M {:.1f} {:.1f} ".format(*pts[0])
for k in range(1, m + 1):
    p = pts[k % m]
    prev = pts[k - 1]
    mxp = ((prev[0] + p[0]) / 2 + j(5), (prev[1] + p[1]) / 2 + j(5))
    disc += "Q {:.1f} {:.1f} {:.1f} {:.1f} ".format(mxp[0], mxp[1], p[0], p[1])
disc += "Z"

# sketchy hatch strokes inside the disc (hand shading, lower-left)
hatch = []
for k in range(4):
    x0 = cx - 88 + k * 26 + j(4)
    y0 = cy + 40 + k * 10 + j(4)
    hatch.append("M {:.1f} {:.1f} q {:.1f} {:.1f} {:.1f} {:.1f}".format(
        x0, y0, 26 + j(4), -34 + j(4), 54 + j(5), -60 + j(5)))

svg = []
svg.append('<svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">')
svg.append('  <rect x="60" y="60" width="904" height="904" rx="202" fill="#17171A" stroke="#2A2A30" stroke-width="6"/>')
for d in petals:
    svg.append('  <path d="{}" fill="#F4F0E4" stroke="#D6CFB8" stroke-width="11" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
for d in veins:
    svg.append('  <path d="{}" fill="none" stroke="#E2DCC6" stroke-width="6" stroke-linecap="round"/>'.format(d))
svg.append('  <path d="{}" fill="#E9C64A" stroke="#C7A339" stroke-width="11" stroke-linejoin="round"/>'.format(disc))
for d in hatch:
    svg.append('  <path d="{}" fill="none" stroke="#C7A339" stroke-width="8" stroke-linecap="round"/>'.format(d))
svg.append('</svg>')

out = "\n".join(svg) + "\n"
with open("daisy-icon.svg", "w") as f:
    f.write(out)
print("wrote daisy-icon.svg ({} bytes, {} petals)".format(len(out), n))
