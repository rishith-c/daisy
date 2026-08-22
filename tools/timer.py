#!/usr/bin/env python3
"""Six-hour refinement timer for the Daisy UI loop.

Writes elapsed/remaining state to timer_state.json every 30s so the agent
can check progress cheaply without blocking.
"""
import json, os, time, datetime

DUR = 6 * 60 * 60
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "timer_state.json")

start = time.time()
started_iso = datetime.datetime.now().isoformat(timespec="seconds")

while True:
    now = time.time()
    elapsed = now - start
    remaining = max(0.0, DUR - elapsed)
    state = {
        "started": started_iso,
        "elapsed_s": round(elapsed),
        "remaining_s": round(remaining),
        "elapsed_hms": time.strftime("%H:%M:%S", time.gmtime(elapsed)),
        "remaining_hms": time.strftime("%H:%M:%S", time.gmtime(remaining)),
        "done": remaining <= 0,
        "pct": round(min(100.0, elapsed / DUR * 100), 1),
    }
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    if remaining <= 0:
        break
    time.sleep(30)

print("SIX HOUR TIMER COMPLETE")
