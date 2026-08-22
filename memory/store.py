"""
Super Memory — four tiers on one SQLite file, with the fourth one being the point.

The failure this package fixes is not "the agent could not find the fact". It is
that the agent did not know the fact was gone. Context is compacted, something
load-bearing goes with it, and nobody notices until a decision is made on the
hole where it used to be.

Ordinary retrieval does not fix that, and it is worth being precise about why:
a vector store answers "here is the most similar thing I hold". It has no way to
say "the thing you are asking about is one I dropped at 14:02". Handing back a
plausible summary of what you forgot is, from the inside, indistinguishable from
never having forgotten it. That is the whole failure mode, reproduced by the
mitigation.

So the store keeps four tiers and — crucially — keeps the fourth:

    T0  event     the raw log. Append-only, content-addressed, never rewritten.
    T1  fact      decisions, approvals, gate verdicts, file writes, repairs,
                  escalations. Extracted as rows at the moment they happen and
                  never summarised. precedent/compact.py already argues why:
                  these are exactly the facts a summariser loses.
    T2  summary   the compacted prose, probe-validated.
    T3  residue   what compaction DROPPED — a claim, a reason, and a pointer
                  back to the T0 row that still holds the original.

T3 is why this is not a vector store with extra steps. A recall that lands there
returns "I compacted this at 14:02; the original is event a41f09c2" and then
goes and gets it. The system is allowed to say it forgot, and can prove where
the forgotten thing went.

What this deliberately does NOT do:

  * It does not re-implement retrieval. The 512-d signed-hash embedding, the
    64-byte binary quantisation and the exact rescore come from
    precedent/engine.py unchanged.
  * It does not re-implement compaction. precedent/compact.py already runs a
    probe-validated ladder; memory/boundary.py wraps it and writes down what it
    dropped, rather than forking it.
  * It does not reconstruct a dropped event from its residue. The residue claim
    is a truncated excerpt on purpose — a residue that reads like a full memory
    would put us back where we started. The pointer is the recovery path.
  * It does not delete. Nothing in this module removes a row, so "forgotten"
    always means "out of context", never "gone".

Zero third-party dependencies; sqlite3 is stdlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precedent.engine import embed, pack_bits, BYTES, DIM  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db")

# precedent/compact.py's STRUCTURED names the *event* kinds a summariser must
# not touch. These are the *fact* kinds they become. `write` and `escalation`
# are added because "which file did I change" and "what did I hand to a human"
# are the two holes that cost the most downstream.
FACT_KINDS = ("decision", "approval", "gate", "write", "repair", "escalation", "next")

# A residue claim is a headline, not a backup. 96 characters is enough to
# recognise what was dropped and nowhere near enough to act on it without
# following the pointer — which is the behaviour we want to force.
CLAIM_CHARS = 96

SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
  id      TEXT PRIMARY KEY,
  run_id  TEXT NOT NULL,
  source  TEXT NOT NULL,
  seq     INTEGER NOT NULL,
  ts      REAL NOT NULL,
  kind    TEXT NOT NULL,
  text    TEXT NOT NULL DEFAULT '',
  body    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS event_src_seq ON event(source, seq);
CREATE INDEX IF NOT EXISTS event_run ON event(run_id);

CREATE TABLE IF NOT EXISTS fact (
  id       TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  run_id   TEXT NOT NULL,
  ts       REAL NOT NULL,
  kind     TEXT NOT NULL,
  subject  TEXT NOT NULL,
  value    TEXT NOT NULL DEFAULT '',
  detail   TEXT NOT NULL DEFAULT '',
  vec      BLOB NOT NULL,
  bits     BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS fact_run ON fact(run_id);
CREATE INDEX IF NOT EXISTS fact_kind ON fact(kind);
-- Exact subject match is the strongest leg of recall, so it gets its own index
-- rather than riding on a scan.
CREATE INDEX IF NOT EXISTS fact_subject ON fact(subject);

CREATE TABLE IF NOT EXISTS summary (
  id           TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL,
  ts           REAL NOT NULL,
  prose        TEXT NOT NULL DEFAULT '',
  essence      TEXT NOT NULL DEFAULT '{}',
  probe_score  REAL NOT NULL DEFAULT 0,
  retried      INTEGER NOT NULL DEFAULT 0,
  bytes_before INTEGER NOT NULL DEFAULT 0,
  bytes_after  INTEGER NOT NULL DEFAULT 0,
  span_lo      INTEGER NOT NULL DEFAULT 0,
  span_hi      INTEGER NOT NULL DEFAULT 0,
  source       TEXT NOT NULL DEFAULT '',
  vec          BLOB NOT NULL,
  bits         BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS summary_run ON summary(run_id);

CREATE TABLE IF NOT EXISTS residue (
  id         TEXT PRIMARY KEY,
  summary_id TEXT NOT NULL,
  event_id   TEXT NOT NULL,
  run_id     TEXT NOT NULL,
  ts         REAL NOT NULL,
  dropped_at REAL NOT NULL,
  kind       TEXT NOT NULL DEFAULT '',
  claim      TEXT NOT NULL,
  reason     TEXT NOT NULL,
  vec        BLOB NOT NULL,
  bits       BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS residue_summary ON residue(summary_id);
CREATE INDEX IF NOT EXISTS residue_event ON residue(event_id);
"""


# ---------------------------------------------------------------------------
# content addressing
# ---------------------------------------------------------------------------

def address(*parts) -> str:
    """Content address for a row.

    Ingestion is re-run constantly — a session file grows, the same run is
    replayed, two lanes report the same gate. Addressing rows by their content
    rather than by an autoincrement makes re-ingestion idempotent without a
    dedup pass: the second write is an INSERT OR IGNORE that changes nothing.
    """
    h = hashlib.blake2b(digest_size=12)
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def connect(db: str = None) -> sqlite3.Connection:
    """Open the store.

    WAL and a busy timeout, because the ingester, the CLI and the UI's stats
    build all run against the same file and a `database is locked` traceback in
    the middle of a recall would be a memory system losing memories to a
    plumbing detail.
    """
    con = sqlite3.connect(db or DEFAULT_DB, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass                      # :memory: and some mounts refuse WAL; harmless
    con.execute("PRAGMA busy_timeout=10000")
    con.executescript(SCHEMA)
    con.commit()
    return con


def _vec(text: str) -> tuple:
    """Both representations of one string, exactly as precedent/engine.py keeps them.

    The 64-byte binary form is what gets scanned; the float32 form is what the
    shortlist is rescored with. Keeping only the binary form and unpacking it
    back to +-1 is 32x cheaper and is what commons/store.py does, but it costs
    most of the usable signal: unpacking turns every zero dimension into -1, and
    on a sparse signed-hash vector that is most of them. commons can absorb
    that because gate containment carries its score. A memory asked "what did I
    forget about the tournament" has no gate to lean on, so the dense leg has to
    actually work, and 2 KB a row is what that costs.
    """
    v = embed(text or " ")
    return b"".join(struct.pack("<f", x) for x in v), pack_bits(v)


def unpack_f32(blob: bytes) -> list:
    return list(struct.unpack("<%df" % (len(blob) // 4), blob))


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """One verbatim thing that happened. Tier 0."""
    run_id: str
    source: str
    seq: int
    kind: str
    ts: float = 0.0
    text: str = ""
    body: dict = field(default_factory=dict)

    def ident(self) -> str:
        # source+seq alone would let an edited log silently overwrite history,
        # so the payload is part of the address.
        return address(self.source, self.seq, self.kind,
                       json.dumps(self.body, sort_keys=True, default=str))


@dataclass
class Fact:
    """One typed, load-bearing claim. Tier 1. Never compacted."""
    run_id: str
    event_id: str
    kind: str
    subject: str
    value: str = ""
    detail: str = ""
    ts: float = 0.0

    def ident(self) -> str:
        return address("fact", self.run_id, self.kind, self.subject, self.value)

    def blob(self) -> str:
        return " ".join(x for x in (self.kind, self.subject, self.value, self.detail) if x)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def append(con: sqlite3.Connection, events, commit: bool = True) -> dict:
    """Append events to Tier 0 and extract Tier-1 facts from them.

    Returns counts rather than ids: callers care whether the log grew, and a
    re-ingest that adds nothing is the expected steady state, not an error.
    """
    added = dupes = facts = 0
    now = time.time()
    for ev in events:
        if ev.ts <= 0:
            ev.ts = now
        eid = ev.ident()
        cur = con.execute(
            "INSERT OR IGNORE INTO event (id, run_id, source, seq, ts, kind, text, body)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (eid, ev.run_id, ev.source, int(ev.seq), float(ev.ts), ev.kind,
             ev.text or "", json.dumps(ev.body, default=str)))
        if cur.rowcount:
            added += 1
        else:
            dupes += 1
        for f in facts_from(ev, eid):
            facts += record_fact(con, f, commit=False)
    if commit:
        con.commit()
    return {"events": added, "duplicates": dupes, "facts": facts}


def record_fact(con: sqlite3.Connection, f: Fact, commit: bool = True) -> int:
    if f.ts <= 0:
        f.ts = time.time()
    cur = con.execute(
        "INSERT OR IGNORE INTO fact (id, event_id, run_id, ts, kind, subject, value,"
        " detail, vec, bits) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f.ident(), f.event_id, f.run_id, float(f.ts), f.kind, f.subject,
         f.value, f.detail) + _vec(f.blob()))
    if commit:
        con.commit()
    return 1 if cur.rowcount else 0


def facts_from(ev: Event, event_id: str) -> list:
    """Type the load-bearing parts of an event.

    The mapping is deliberately narrow. Every kind here is one whose loss
    changes a later decision: which file was touched, which gate went red, who
    approved what, what was handed to a human. Narration is not a fact and does
    not belong in Tier 1 — that is what Tier 2 is for.
    """
    b, out = ev.body, []
    k = ev.kind

    if k == "diff":
        for p in b.get("files", []) or []:
            out.append(Fact(ev.run_id, event_id, "write", str(p),
                            b.get("verb", "modified"), ev.text[:200], ev.ts))
    elif k == "gate":
        margin = b.get("margin")
        out.append(Fact(ev.run_id, event_id, "gate", str(b.get("name", "")),
                        "pass" if b.get("passed") else "fail",
                        "" if margin is None else "margin %s" % margin, ev.ts))
    elif k == "repair":
        out.append(Fact(ev.run_id, event_id, "repair", str(b.get("fixes", "")),
                        str(b.get("by", "")), ev.text[:200], ev.ts))
    elif k == "approval":
        out.append(Fact(ev.run_id, event_id, "approval", str(b.get("what", "")),
                        str(b.get("who", "")), ev.text[:200], ev.ts))
    elif k == "escalation":
        out.append(Fact(ev.run_id, event_id, "escalation", str(b.get("what", "") or ev.text[:80]),
                        str(b.get("to", "")), ev.text[:200], ev.ts))
    elif k in ("decision", "next"):
        t = (ev.text or b.get("text") or "").strip()
        if t:
            out.append(Fact(ev.run_id, event_id, k, t[:120], t[:400], "", ev.ts))

    return [f for f in out if f.subject]


def record_summary(con: sqlite3.Connection, run_id: str, prose: str, essence: dict,
                   probe_score: float, retried: bool, bytes_before: int,
                   bytes_after: int, span: tuple, source: str = "",
                   ts: float = 0.0, commit: bool = True) -> str:
    sid = address("summary", run_id, source, span[0], span[1], prose)
    con.execute(
        "INSERT OR REPLACE INTO summary (id, run_id, ts, prose, essence, probe_score,"
        " retried, bytes_before, bytes_after, span_lo, span_hi, source, vec, bits)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, run_id, ts or time.time(), prose, json.dumps(essence, default=str),
         float(probe_score), int(bool(retried)), int(bytes_before), int(bytes_after),
         int(span[0]), int(span[1]), source) + _vec(prose or run_id))
    if commit:
        con.commit()
    return sid


def record_residue(con: sqlite3.Connection, summary_id: str, ev_row, reason: str,
                   dropped_at: float, commit: bool = True) -> str:
    """Write down one thing compaction removed from context.

    The claim is truncated at CLAIM_CHARS deliberately. A residue row that held
    the whole event would be a second copy of Tier 0 wearing a different hat,
    and an agent reading it would believe it still had the memory. It should
    have to follow `event_id` — that round trip is the honesty.
    """
    claim = (ev_row["text"] or ev_row["kind"] or "").strip().replace("\n", " ")
    if len(claim) > CLAIM_CHARS:
        claim = claim[:CLAIM_CHARS - 1] + "…"
    rid = address("residue", summary_id, ev_row["id"])
    con.execute(
        "INSERT OR REPLACE INTO residue (id, summary_id, event_id, run_id, ts,"
        " dropped_at, kind, claim, reason, vec, bits) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rid, summary_id, ev_row["id"], ev_row["run_id"], float(ev_row["ts"]),
         float(dropped_at), ev_row["kind"], claim, reason)
        + _vec(claim or ev_row["kind"]))
    if commit:
        con.commit()
    return rid


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def event(con: sqlite3.Connection, event_id: str) -> dict:
    """Follow a Tier-0 pointer. This is the recovery path from Tier 3."""
    r = con.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
    if r is None:
        return {}
    d = dict(r)
    try:
        d["body"] = json.loads(d["body"])
    except (ValueError, TypeError):
        # A body we cannot parse is still a real pointer to a real row. Hand
        # back the raw string rather than pretending the event is missing.
        d["body_unparsed"] = True
    return d


def events_in_span(con: sqlite3.Connection, source: str, lo: int, hi: int) -> list:
    return con.execute(
        "SELECT * FROM event WHERE source = ? AND seq BETWEEN ? AND ? ORDER BY seq",
        (source, int(lo), int(hi))).fetchall()


def facts_for(con: sqlite3.Connection, run_id: str = None, kind: str = None) -> list:
    sql, args = "SELECT * FROM fact", []
    where = []
    if run_id:
        where.append("run_id = ?"); args.append(run_id)
    if kind:
        where.append("kind = ?"); args.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return con.execute(sql + " ORDER BY ts", args).fetchall()


def stats(con: sqlite3.Connection) -> dict:
    """Tier sizes and the one ratio that matters.

    `context_coverage` is the share of Tier-1 facts an agent still holds after
    every compaction on file. It is below 1.0 whenever compaction has run, and
    the gap is precisely what a system without Tier 3 would have lost silently.
    """
    def one(q, *a):
        r = con.execute(q, a).fetchone()
        return r[0] if r else 0

    n_ev = one("SELECT COUNT(*) FROM event")
    n_fact = one("SELECT COUNT(*) FROM fact")
    n_sum = one("SELECT COUNT(*) FROM summary")
    n_res = one("SELECT COUNT(*) FROM residue")
    live = one("SELECT COUNT(*) FROM residue r JOIN event e ON e.id = r.event_id")
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM fact GROUP BY kind").fetchall())
    reasons = dict(con.execute("SELECT reason, COUNT(*) FROM residue GROUP BY reason").fetchall())
    before = one("SELECT COALESCE(SUM(bytes_before),0) FROM summary")
    after = one("SELECT COALESCE(SUM(bytes_after),0) FROM summary")
    runs = one("SELECT COUNT(DISTINCT run_id) FROM event")
    sources = one("SELECT COUNT(DISTINCT source) FROM event")
    return {
        "events": n_ev, "facts": n_fact, "summaries": n_sum, "residue": n_res,
        "runs": runs, "sources": sources,
        "fact_kinds": kinds, "residue_reasons": reasons,
        "dangling_pointers": n_res - live,
        "bytes_before": before, "bytes_after": after,
        "compression": round(before / after, 1) if after else 0.0,
        "index_bytes": BYTES * (n_fact + n_sum + n_res),
        "float_bytes": DIM * 4 * (n_fact + n_sum + n_res),
    }
