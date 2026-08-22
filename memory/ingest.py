"""
Ingestion — real sessions and real runs, not a synthetic corpus.

Two sources, because they carry different halves of what an agent needs to
remember and neither is sufficient alone:

    a Claude Code .jsonl session   what was read, run, written and broken
    a Daisy run directory          which gates went red, what was repaired,
                                   what was approved, what escalated

Sessions are located through agents/discover.py rather than by re-globbing
~/.claude — that module already knows the three vendors' layouts, already reads
strictly read-only, and already refuses to load a 120 MB transcript into memory.
Duplicating its glob here would give us a second thing to keep correct.

Reading is bounded and streaming. A single session in this repo runs to nine
figures of bytes; ingestion holds one line at a time and stops at a byte budget,
recording that it stopped. A memory system that OOMs while remembering is not a
memory system.

What this deliberately does NOT do:

  * It does not infer facts from prose. A `decision` is a Tier-1 fact only when
    the source recorded one — a regex that promoted confident-sounding sentences
    to decisions would fill Tier 1 with exactly the unverified material the
    tier exists to keep out.
  * It does not keep thinking blocks. They are not part of what the agent
    later has to answer for, and they are the bulkiest thing in a transcript.
  * It does not write to, lock, or rewrite any source file. Sessions belong to
    the tool that made them.
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import discover  # noqa: E402
from . import store  # noqa: E402

# Bounded read. Enough to cover a long working session; small enough that
# ingesting every session on a laptop stays a background-noise operation.
MAX_BYTES = 8 * 1024 * 1024

# Tool names whose call *is* a file write. Anything else that happens to touch
# the filesystem does so through Bash, which we record as a tool event and do
# not pretend to parse.
WRITERS = {"Write": "created", "Edit": "modified", "MultiEdit": "modified",
           "NotebookEdit": "modified"}


# ---------------------------------------------------------------------------
# claude code sessions
# ---------------------------------------------------------------------------

def sessions(limit: int = None) -> list:
    """Real Claude Code sessions on this machine, newest first, with paths.

    discover.scan_claude() supplies identity and state; the path is resolved
    back from the session id because a Session is a UI record, not a file
    handle. Anything it cannot resolve is dropped rather than guessed at.
    """
    root = os.path.join(discover.HOME, ".claude", "projects")
    out = []
    for s in sorted(discover.scan_claude(), key=lambda s: -s.mtime):
        hits = glob.glob(os.path.join(root, "*", s.id + ".jsonl"))
        if hits:
            out.append((s, hits[0]))
        if limit and len(out) >= limit:
            break
    return out


def _blocks(rec: dict) -> list:
    m = rec.get("message")
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in (c or []) if isinstance(b, dict)]


def claude_events(path: str, run_id: str, source: str,
                  max_bytes: int = MAX_BYTES) -> list:
    """One session file to Tier-0 events. Streaming, bounded, read-only."""
    out, seq, read_bytes, truncated = [], 0, 0, False

    def add(kind, text, body):
        nonlocal seq
        seq += 1
        out.append(store.Event(run_id=run_id, source=source, seq=seq, kind=kind,
                               ts=_ts(body.get("_ts")), text=text or "", body=body))

    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            read_bytes += len(line)
            if read_bytes > max_bytes:
                truncated = True
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue        # a partial or malformed line is not a reason to stop
            if not isinstance(rec, dict):
                continue
            ts = rec.get("timestamp")
            kind = rec.get("type")

            for b in _blocks(rec):
                bt = b.get("type")
                if bt == "text":
                    t = (b.get("text") or "").strip()
                    if t:
                        add("prose", t, {"role": kind, "_ts": ts})
                elif bt == "tool_use":
                    name = b.get("name") or ""
                    inp = b.get("input") or {}
                    if name in WRITERS and inp.get("file_path"):
                        add("diff", "%s %s" % (WRITERS[name], inp["file_path"]),
                            {"files": [inp["file_path"]], "verb": WRITERS[name],
                             "tool": name, "_ts": ts})
                    elif name == "Read" and inp.get("file_path"):
                        add("read", "read %s" % inp["file_path"],
                            {"path": inp["file_path"], "tool": name, "_ts": ts})
                    else:
                        summary = inp.get("command") or inp.get("query") or \
                            inp.get("description") or inp.get("skill") or name
                        add("tool", "%s: %s" % (name, str(summary)[:400]),
                            {"tool": name, "_ts": ts})
                elif bt == "tool_result":
                    text = b.get("content")
                    if isinstance(text, list):
                        text = " ".join(str(x.get("text", "")) for x in text
                                        if isinstance(x, dict))
                    text = str(text or "")[:2000]
                    if b.get("is_error"):
                        add("error", text, {"id": b.get("tool_use_id") or "", "_ts": ts})

    if truncated:
        seq += 1
        out.append(store.Event(
            run_id=run_id, source=source, seq=seq, kind="decision",
            text="ingestion stopped at the %d MB budget for %s"
                 % (max_bytes // (1024 * 1024), os.path.basename(path)),
            body={"truncated": True, "bytes": read_bytes}))
    return out


def _ts(v) -> float:
    """Claude writes ISO-8601 with a Z. Anything else is left to the store."""
    if not isinstance(v, str) or len(v) < 19:
        return 0.0
    import calendar
    try:
        import time as _t
        return calendar.timegm(_t.strptime(v[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def ingest_session(con, path: str, run_id: str = None,
                   max_bytes: int = MAX_BYTES) -> dict:
    sid = os.path.basename(path)[:-6] if path.endswith(".jsonl") else os.path.basename(path)
    run_id = run_id or ("claude/" + sid[:8])
    evs = claude_events(path, run_id, "claude:" + sid, max_bytes=max_bytes)
    res = store.append(con, evs)
    res.update({"source": "claude:" + sid, "run_id": run_id, "path": path})
    return res


# ---------------------------------------------------------------------------
# daisy's own runs
# ---------------------------------------------------------------------------

def run_events(run_dir: str, run_id: str, source: str) -> list:
    """A runs/<id>/ directory to Tier-0 events.

    Everything here was written by the orchestrator as structured JSON, so every
    fact it yields is one the factory actually recorded. Nothing is inferred.
    """
    out, seq = [], 0

    def add(kind, text, body):
        nonlocal seq
        seq += 1
        out.append(store.Event(run_id=run_id, source=source, seq=seq,
                               kind=kind, ts=_mtime(run_dir), text=text, body=body))

    plan = _json(os.path.join(run_dir, "plan.json"))
    if plan:
        add("decision", "brief: %s" % plan.get("brief", ""),
            {"lanes": plan.get("lanes", []), "load_case": plan.get("load_case", {})})
        lc = plan.get("load_case") or {}
        if lc:
            add("decision", "load case %s kg at %s mm, FoS %s, %s"
                % (lc.get("kg"), lc.get("arm_mm"), lc.get("fos"), lc.get("material")), lc)

    summ = _json(os.path.join(run_dir, "summary.json"))
    if summ:
        for lane, d in (summ.get("lanes") or {}).items():
            if d.get("why"):
                add("prose", "%s lane: %s" % (lane, d["why"]), {"lane": lane})
            for g in d.get("gates") or []:
                add("gate", "%s %s" % (g.get("name"), "passed" if g.get("passed") else "FAILED"),
                    {"name": g.get("name"), "passed": bool(g.get("passed")),
                     "margin": g.get("margin"), "detail": g.get("detail", ""), "lane": lane})
            # An artifact is sometimes a bare path and sometimes a record with
            # a path plus measurements. Tier 1 wants the path: a `write` fact
            # whose subject is a dict repr is unmatchable by anyone asking
            # "did I write X".
            arts = [a.get("path") if isinstance(a, dict) else a
                    for a in (d.get("artifacts") or [])]
            arts = [str(a) for a in arts if a]
            if arts:
                add("diff", "%s lane wrote %d artifact(s)" % (lane, len(arts)),
                    {"files": arts, "verb": "produced", "lane": lane})
            if d.get("attempts", 0) > 1:
                add("repair", "%s lane retried %d times" % (lane, d["attempts"]),
                    {"fixes": lane, "by": "resume-findings", "attempts": d["attempts"]})
        for lane in summ.get("blocked_lanes") or []:
            add("escalation", "%s lane blocked, handed to a person" % lane,
                {"what": "%s lane" % lane, "to": "review-queue"})
        for a in summ.get("admitted_to_commons") or []:
            what = a.get("lane") if isinstance(a, dict) else str(a)
            add("approval", "admitted %s to the commons" % what,
                {"what": str(what), "who": "gate-set"})
    return out


def ingest_run(con, run_dir: str) -> dict:
    rid = os.path.basename(os.path.normpath(run_dir))
    evs = run_events(run_dir, rid, "daisy:" + rid)
    res = store.append(con, evs)
    res.update({"source": "daisy:" + rid, "run_id": rid, "path": run_dir})
    return res


def ingest_runs(con, runs_root: str) -> list:
    out = []
    for d in sorted(glob.glob(os.path.join(runs_root, "*"))):
        if os.path.isdir(d) and (os.path.exists(os.path.join(d, "summary.json"))
                                 or os.path.exists(os.path.join(d, "plan.json"))):
            out.append(ingest_run(con, d))
    return out


def _json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
