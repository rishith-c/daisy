#!/usr/bin/env python3
"""Generate the hand-drawn Daisy app icon as SVG (seeded wobble, reproducible).

Two rings of petals, not one: a real daisy is full, and a single ring at 12
petals leaves visible gaps wherever the angular wobble happens to widen a pair.
The back ring is shorter, offset half a step, and a shade deeper, so it fills
the holes without reading as clutter at 16px.

The plate carries no stroke. A 6px outline looked like a considered edge at
1024px and like a pale ring around the app at 32px, which is the size it is
actually seen at.
"""
import math, random

random.seed(7)
cx, cy = 512.0, 512.0          # centred; the flower used to sit low and right

def j(m=5.0):
    return random.uniform(-m, m)


def ring(n, phase, lo, hi, w_lo, w_hi, r0=116.0, wobble=3.0):
    """One ring of petals + their centre veins. Angular jitter stays small:
    +/-7 degrees on a 30 degree pitch can open a 44 degree gap, which is the
    hole that made the old icon read as a partial flower."""
    petals, veins = [], []
    for i in range(n):
        a = math.radians(i * 360.0 / n + phase + random.uniform(-wobble, wobble))
        L = random.uniform(lo, hi)
        W = random.uniform(w_lo, w_hi)
        ux, uy = math.cos(a), math.sin(a)
        px, py = -uy, ux

        def pt(d, w, jx=0.0, jy=0.0):
            return (cx + ux * d + px * w + jx, cy + uy * d + py * w + jy)

        base_l = pt(r0, -W * 0.42, j(4), j(4))
        mid_l  = pt(r0 + (L - r0) * 0.55, -W, j(), j())
        tip    = pt(L, random.uniform(-10, 10), j(5), j(5))
        mid_r  = pt(r0 + (L - r0) * 0.55, W, j(), j())
        base_r = pt(r0, W * 0.42, j(4), j(4))
        petals.append(
            "M {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f}".format(
                base_l[0], base_l[1], mid_l[0], mid_l[1], tip[0], tip[1],
                mid_r[0], mid_r[1], base_r[0], base_r[1]))
        vb = pt(r0 + 14, j(5))
        vm = pt(r0 + (L - r0) * 0.5, j(8))
        vt = pt(L - 44, j(6))
        veins.append("M {:.1f} {:.1f} Q {:.1f} {:.1f} {:.1f} {:.1f}".format(
            vb[0], vb[1], vm[0], vm[1], vt[0], vt[1]))
    return petals, veins


# back ring first (shorter, offset half a step), then the front ring over it
back_p, _        = ring(12, 15.0, 268, 292, 66, 78)
front_p, front_v = ring(12,  0.0, 334, 358, 74, 88)

# wobbly centre disc: closed loop of quadratic segments
pts = []
m = 11
for k in range(m):
    ang = math.radians(k * 360.0 / m)
    r = 128 + random.uniform(-8, 8)
    pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
disc = "M {:.1f} {:.1f} ".format(*pts[0])
for k in range(1, m + 1):
    p, prev = pts[k % m], pts[k - 1]
    mxp = ((prev[0] + p[0]) / 2 + j(4), (prev[1] + p[1]) / 2 + j(4))
    disc += "Q {:.1f} {:.1f} {:.1f} {:.1f} ".format(mxp[0], mxp[1], p[0], p[1])
disc += "Z"

# sketchy hatch strokes inside the disc (hand shading, lower-left)
hatch = []
for k in range(4):
    x0 = cx - 88 + k * 26 + j(3)
    y0 = cy + 40 + k * 10 + j(3)
    hatch.append("M {:.1f} {:.1f} q {:.1f} {:.1f} {:.1f} {:.1f}".format(
        x0, y0, 26 + j(3), -34 + j(3), 54 + j(4), -60 + j(4)))

svg = ['<svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">']
svg.append('  <rect x="60" y="60" width="904" height="904" rx="202" fill="#17171A"/>')
for d in back_p:
    svg.append('  <path d="{}" fill="#DCD6C4" stroke="#C4BCA4" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
for d in front_p:
    svg.append('  <path d="{}" fill="#F4F0E4" stroke="#D6CFB8" stroke-width="11" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
for d in front_v:
    svg.append('  <path d="{}" fill="none" stroke="#E2DCC6" stroke-width="6" stroke-linecap="round"/>'.format(d))
svg.append('  <path d="{}" fill="#E9C64A" stroke="#C7A339" stroke-width="11" stroke-linejoin="round"/>'.format(disc))
for d in hatch:
    svg.append('  <path d="{}" fill="none" stroke="#C7A339" stroke-width="8" stroke-linecap="round"/>'.format(d))
svg.append('</svg>')

out = "\n".join(svg) + "\n"
with open("icon/daisy-icon.svg", "w") as f:
    f.write(out)
print("wrote icon/daisy-icon.svg ({} bytes, {}+{} petals, no plate stroke)".format(
    len(out), len(back_p), len(front_p)))
