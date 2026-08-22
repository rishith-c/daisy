#!/usr/bin/env python3
"""Generate the hand-drawn Daisy app icon as SVG (seeded wobble, reproducible).

Two rings of petals, not one: a real daisy is full, and a single ring at 12
petals leaves visible gaps wherever the angular wobble happens to widen a pair.
The back ring is shorter, offset half a step, and a shade deeper, so it fills
the holes without reading as clutter at 16px.

No plate at all. A rounded square behind the flower is a border by another
name, and it forces one ground on an icon that has to sit on both a light and
a dark Dock. Instead the daisy is drawn free, white-filled with a warm grey
outline: the outline is what lets white petals read against white, and the
white fill is what lets them read against black. Scaled up to fill the canvas
that the plate used to occupy.
"""
import math, random

random.seed(7)
cx, cy = 512.0, 512.0
S = 1.17                       # the flower now owns the canvas the plate had          # centred; the flower used to sit low and right

def j(m=5.0):
    return random.uniform(-m, m)


def ring(n, phase, lo, hi, w_lo, w_hi, r0=116.0 * S, wobble=3.0):
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
back_p, _        = ring(12, 15.0, 268 * S, 292 * S, 66 * S, 78 * S)
front_p, front_v = ring(12,  0.0, 334 * S, 358 * S, 74 * S, 88 * S)

# wobbly centre disc: closed loop of quadratic segments
pts = []
m = 11
for k in range(m):
    ang = math.radians(k * 360.0 / m)
    r = (128 + random.uniform(-8, 8)) * S
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
    x0 = cx - 88 * S + k * 26 * S + j(3)
    y0 = cy + 40 * S + k * 10 * S + j(3)
    hatch.append("M {:.1f} {:.1f} q {:.1f} {:.1f} {:.1f} {:.1f}".format(
        x0, y0, 26 * S + j(3), -34 * S + j(3), 54 * S + j(4), -60 * S + j(4)))

svg = ['<svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">']
for d in back_p:
    svg.append('  <path d="{}" fill="#F2EFE6" stroke="#A9A294" stroke-width="11" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
for d in front_p:
    svg.append('  <path d="{}" fill="#FFFFFF" stroke="#A9A294" stroke-width="12" stroke-linejoin="round" stroke-linecap="round"/>'.format(d))
for d in front_v:
    svg.append('  <path d="{}" fill="none" stroke="#CFC8B6" stroke-width="7" stroke-linecap="round"/>'.format(d))
svg.append('  <path d="{}" fill="#E9C64A" stroke="#B4922C" stroke-width="12" stroke-linejoin="round"/>'.format(disc))
for d in hatch:
    svg.append('  <path d="{}" fill="none" stroke="#C7A339" stroke-width="8" stroke-linecap="round"/>'.format(d))
svg.append('</svg>')

out = "\n".join(svg) + "\n"
with open("icon/daisy-icon.svg", "w") as f:
    f.write(out)
print("wrote icon/daisy-icon.svg ({} bytes, {}+{} petals, no plate)".format(
    len(out), len(back_p), len(front_p)))
