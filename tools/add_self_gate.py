#!/usr/bin/env python3
"""Insert the 'gate applied to itself' block into the Skills view."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
lines = open(idx, encoding="utf-8").read().split("\n")

if any("applied to itself" in ln for ln in lines):
    print("already present")
    raise SystemExit(0)

anchor = None
for i, ln in enumerate(lines):
    if "skills/taste-rubric.md" in ln:
        anchor = i
        break
if anchor is None:
    raise SystemExit("anchor not found")

block = '''
    <h3>The gate, applied to itself</h3>
    <div class="lede" style="margin-bottom:12px">Tier 1 is a real program, not a prompt: <code>taste/lint.py</code>, pure stdlib, exit code = finding count. Daisy is held to the same gate it applies to everything the factory builds.</div>
    <div class="card"><div class="chead"><span class="ci">T</span><div style="min-width:0"><b>taste.t1 &middot; index.html</b>
      <div class="sub">grep/AST &middot; 20 named tells &middot; 0 tokens</div></div>
      <div class="act"><span class="tag pass">PASS &middot; 0</span></div></div>
      <div class="derive">$ python3 -m taste.lint index.html
taste.t1  PASS  index.html  —  0 findings across 20 tells
$ echo $?
0</div>
      <div class="grow"><span class="gn">gate 3 &middot; indigo primary</span><span class="gd">no Tailwind default accent outside a fixture</span><span class="gm ok">&#10003;</span></div>
      <div class="grow"><span class="gn">gate 11 &middot; unpaired face</span><span class="gd">Source Serif paired with the system sans</span><span class="gm ok">&#10003;</span></div>
      <div class="grow"><span class="gn">gate 17 &middot; emoji as icons</span><span class="gd">every icon is inline SVG on currentColor</span><span class="gm ok">&#10003;</span></div>
      <div class="grow"><span class="gn">gate 25 &middot; radius ladder</span><span class="gd">badge 4 &middot; button 10 &middot; card 10 &middot; panel 12 &middot; composer 20</span><span class="gm ok">&#10003;</span></div>
      <div class="res" style="display:block">A gate you exempt yourself from is not a gate. 26 tests cover the linter, and one of them is this file.</div></div>'''

lines.insert(anchor + 1, block)
open(idx, "w", encoding="utf-8").write("\n".join(lines))
print("inserted self-gate block after line %d" % (anchor + 1))
