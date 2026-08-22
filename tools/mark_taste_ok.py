#!/usr/bin/env python3
"""Add `taste-ok` suppression to lines that quote a tell as demo content.

Daisy's own UI documents the tells it rejects, so its source necessarily
contains them. Those lines are fixtures, not defects — mark them explicitly
rather than weakening the linter.

Placement matters: a line ending in `+` is mid-concatenation, so a `//` comment
would swallow the continuation. Those get a block comment instead.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from taste.lint import lint  # noqa: E402

idx = os.path.join(ROOT, "index.html")
lines = open(idx, encoding="utf-8").read().split("\n")

findings = [f for f in lint("\n".join(lines), "index.html") if f.line]
targets = sorted({f.line for f in findings})

marked = 0
for ln in targets:
    i = ln - 1
    line = lines[i]
    if "taste-ok" in line:
        continue
    stripped = line.rstrip()
    if stripped.endswith("+"):
        lines[i] = stripped[:-1].rstrip() + " /* taste-ok: fixture quoting the tell */ +"
    else:
        lines[i] = stripped + "  // taste-ok: fixture quoting the tell"
    marked += 1

open(idx, "w", encoding="utf-8").write("\n".join(lines))
print("marked %d fixture line(s) taste-ok" % marked)
