#!/usr/bin/env python3
"""Bake the discovered model inventory into index.html."""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.models import inventory  # noqa: E402

IDX = os.path.join(ROOT, "index.html")
MARK = "var MODEL_INVENTORY = "

def main():
    inv = inventory()
    h = open(IDX, encoding="utf-8").read()
    start = h.index(MARK) + len(MARK)
    end = h.index("\n", start)
    open(IDX, "w", encoding="utf-8").write(h[:start] + json.dumps(inv) + ";" + h[end:])
    by = {}
    for m in inv["models"]:
        by[m["vendor"]] = by.get(m["vendor"], 0) + 1
    print("baked %d models: %s" % (len(inv["models"]),
          ", ".join("%s %d" % kv for kv in sorted(by.items()))))

if __name__ == "__main__":
    main()
