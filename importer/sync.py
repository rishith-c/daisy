"""
Autosync — the toggle from the reference, with a watermark behind it.

Default off, and off means off: `sync_once` on a machine that never turned it
on reports that and does nothing. When it is on, it re-scans and pulls what
changed. Two rules keep it from becoming a surprise:

    it only syncs what you already imported. A source you never selected is
    never pulled in by a background pass — that would turn one click into a
    standing grant over every tool on the machine.

    it says when it last ran. `last_run` is a real timestamp and "never" is a
    real answer. A sync UI that implies freshness it cannot demonstrate is the
    dishonest version of this feature.

The watermark is one cursor per source: a digest over the digests of that
source's items. If the cursor has not moved, the source is skipped whole —
no per-item work, no ledger read, no diff. If it has, only the items whose own
digest changed are re-imported.

IS NOT a daemon. There is no thread, no timer and no launchd plist; `sync_once`
is a function the app calls. A background process that quietly rewrites config
while nobody is looking is a much bigger promise than this feature has earned.

Zero third-party dependencies.
"""

from __future__ import annotations

import time

from . import ingest
from .detect import by_id
from .state import State, digest


def source_cursor(src: dict) -> str:
    """A single value standing for the current content of a whole source."""
    return digest(*sorted(i["digest"] for i in src.get("items") or []))


def status(st: State = None) -> dict:
    st = st or State()
    s = st.sync()
    marks = s.get("watermarks") or {}
    return {
        "enabled": bool(s.get("enabled")),
        "last_run": s.get("last_run"),
        "last_run_human": _ago(s.get("last_run")),
        "last_result": s.get("last_result"),
        "watermarks": marks,
        "tracked": sorted(marks),
    }


def set_enabled(on: bool, st: State = None) -> dict:
    st = st or State()
    s = st.sync()
    s["enabled"] = bool(on)
    s["toggled"] = round(time.time())
    st.write_json("sync.json", s)
    return status(st)


def imported_sources(st: State = None) -> list:
    """Sources with at least one item in the ledger. Autosync's whole scope."""
    st = st or State()
    return sorted({v.get("source") for v in (st.ledger().get("items") or {}).values()
                   if v.get("source")})


def sync_once(det: dict, st: State = None, dry_run: bool = True,
              force: bool = False) -> dict:
    """One pass. Returns what moved, per source, and updates the watermarks."""
    st = st or State()
    s = st.sync()
    if not s.get("enabled") and not force:
        return {"ran": False, "reason": "autosync is off",
                "enabled": False, "dry_run": dry_run, "sources": []}

    marks = s.setdefault("watermarks", {})
    out, moved = [], 0
    for sid in imported_sources(st):
        src = by_id(det, sid)
        if not src:
            out.append({"source": sid, "skipped": "no longer detected on this machine"})
            continue
        cur = source_cursor(src)
        if not force and (marks.get(sid) or {}).get("cursor") == cur:
            out.append({"source": sid, "skipped": "unchanged since last sync",
                        "cursor": cur})
            continue
        rep = ingest.run(sid, det, st, dry_run=dry_run, changed_only=True)
        n = rep.tally("added") + rep.tally("updated")
        moved += n
        out.append({"source": sid, "moved": n, "summary": rep.summary(),
                    "writes": rep.writes, "conflicts": rep.conflicts})
        if not dry_run:
            marks[sid] = {"cursor": cur, "at": round(time.time()),
                          "items": len(src.get("items") or [])}

    result = {"ran": True, "enabled": True, "dry_run": dry_run,
              "moved": moved, "sources": out}
    if not dry_run:
        s["last_run"] = round(time.time())
        s["last_result"] = {"moved": moved, "sources": len(out)}
        st.write_json("sync.json", s)
    result["last_run_human"] = _ago(s.get("last_run"))
    return result


def _ago(ts) -> str:
    if not ts:
        return "never"
    d = max(0, time.time() - float(ts))
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if d >= n:
            return "%d%s ago" % (d // n, unit)
    return "just now"
