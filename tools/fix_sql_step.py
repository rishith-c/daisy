#!/usr/bin/env python3
"""Remove any previously injected memory-as-code step so it can be regenerated."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
lines = open(idx).read().split("\n")
out = [ln for ln in lines if "Memory as code" not in ln]
removed = len(lines) - len(out)
open(idx, "w").write("\n".join(out))
print("removed %d step line(s)" % removed)
