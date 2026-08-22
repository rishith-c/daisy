"""
Durable storage for conversations, messages and runs — one SQLite file.

WHY THIS EXISTS
---------------
Daisy could do exactly one thing before this file: take a brief and run the
factory. Close the app and the run history, the prompts and the conversation
were gone, because they only ever existed in a browser tab and a `runs/`
directory nobody indexed. A tool a person uses twice has to remember the first
time.

THE ONE RULE THAT SHAPES EVERYTHING HERE
----------------------------------------
**The prompt is written before the model is called, never after.**

That is not a nicety. A factory run is minutes long and drives real CLIs that
can hang, rate-limit, or take the whole process down with them; a conversation
turn can be interrupted by a quit. If the write happens after the reply, every
one of those outcomes silently eats what the person typed — and what they typed
is the only part of the exchange they cannot regenerate. So `add_message` for
the user's turn commits before `session.send` goes anywhere near an executor,
and an unanswered turn is a first-class, queryable state (`unanswered`) rather
than a hole.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
    IS      one file at ~/.daisy/daisy.db, WAL so two processes can write,
            forward-only numbered migrations, FTS5 search with a real LIKE
            fallback, and JSON export/import
    IS NOT  a sync engine, a vector index, a message queue, or an ORM. There is
            no server, no daemon, and no migration-down. Rolling a schema back
            is a restore-from-export, which is the honest answer for a
            single-file local database.

Token counts stored on a message are an ESTIMATE (`len // 4`), flagged as such
in `meta`, because `lab/executors.py` drives these CLIs in text mode and none of
them report usage there. A fabricated exact count would be worse than an
admitted approximation.

Zero third-party dependencies.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time

# Same environment override garden/link.py uses, so one variable relocates
# every piece of Daisy's local state during a test or a dry run.
def home() -> str:
    return os.environ.get("DAISY_HOME") or os.path.join(os.path.expanduser("~"), ".daisy")


def default_db() -> str:
    return os.path.join(home(), "daisy.db")


# ---------------------------------------------------------------------------
# schema
#
# Migrations are a list, applied in order, each in its own transaction, each
# recorded in `meta`. Re-running is a no-op because the version row is checked
# first and every statement is additive-and-guarded anyway.
#
# v1 is the schema as specified: conversations, messages, runs.
# v2 adds the two columns this implementation needs on top of it, kept separate
#    precisely so the ALTER path — the one that actually breaks in the wild —
#    is exercised on every existing database rather than only on fresh ones.
# ---------------------------------------------------------------------------

MIGRATIONS = [
    (1, [
        """CREATE TABLE IF NOT EXISTS conversations (
             id        TEXT PRIMARY KEY,
             title     TEXT NOT NULL DEFAULT '',
             created   REAL NOT NULL,
             updated   REAL NOT NULL,
             model     TEXT NOT NULL DEFAULT 'auto',
             archived  INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS messages (
             id              TEXT PRIMARY KEY,
             conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
             role            TEXT NOT NULL,
             content         TEXT NOT NULL,
             model           TEXT NOT NULL DEFAULT '',
             tokens          INTEGER NOT NULL DEFAULT 0,
             created         REAL NOT NULL,
             run_id          TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS runs (
             id              TEXT PRIMARY KEY,
             conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
             brief           TEXT NOT NULL DEFAULT '',
             lanes           TEXT NOT NULL DEFAULT '[]',
             gates_total     INTEGER NOT NULL DEFAULT 0,
             gates_failed    INTEGER NOT NULL DEFAULT 0,
             artifacts_dir   TEXT NOT NULL DEFAULT '',
             created         REAL NOT NULL,
             status          TEXT NOT NULL DEFAULT 'queued'
           )""",
        "CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id)",
        "CREATE INDEX IF NOT EXISTS msg_run  ON messages(run_id)",
        "CREATE INDEX IF NOT EXISTS run_conv ON runs(conversation_id)",
        "CREATE INDEX IF NOT EXISTS conv_arch ON conversations(archived, updated)",
    ]),
    (2, [
        # `created` is a float from time.time(). Two messages written in the
        # same microsecond — or by two processes whose clocks disagree — would
        # sort arbitrarily, and a conversation whose turns reorder is a
        # conversation that cannot be replayed. seq is per-conversation and
        # allocated inside the same IMMEDIATE transaction as the insert.
        "ALTER TABLE messages ADD COLUMN seq INTEGER NOT NULL DEFAULT 0",
        # Backfill, or every row written before this migration ties at 0 and an
        # upgraded conversation replays in arbitrary order. rowid is insertion
        # order, which is the only ordering a v1 database actually recorded.
        """UPDATE messages SET seq = (SELECT COUNT(*) FROM messages m2
             WHERE m2.conversation_id = messages.conversation_id
               AND m2.rowid <= messages.rowid)""",
        # How this message was classified, what signals fired, whether the
        # model was substituted, whether history was trimmed. The brief demands
        # the classification be visible rather than silent; after a restart the
        # only place visibility can come from is the row itself.
        "ALTER TABLE messages ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS msg_seq ON messages(conversation_id, seq)",
    ]),
]

LATEST = MIGRATIONS[-1][0]

# FTS5 is a *capability*, not a schema version. A build without it is a working
# build — the search just answers with LIKE and says so. Tying it to the version
# counter would mean two machines running identical code disagreed about what
# "version 2" meant.
FTS_SQL = [
    "CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='rowid')",
    """CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
         INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
       END""",
    """CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
         INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
       END""",
    """CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
         INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
         INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
       END""",
]

ROLES = ("user", "assistant", "system")


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

def connect(db: str = None, migrate_to: int = None) -> sqlite3.Connection:
    """Open (creating if needed), migrate, and return a connection.

    WAL is on because two writers is the normal case here, not the exotic one:
    the desktop shell holds a connection while `python3 -m chat.cli` is run in a
    terminal against the same file. busy_timeout turns the resulting contention
    into a short wait instead of an immediate "database is locked".
    """
    path = db or default_db()
    if path != ":memory:":
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, mode=0o700, exist_ok=True)
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    # Explicit transactions: seq allocation is a read-modify-write and the
    # driver's implicit BEGIN DEFERRED would let two writers read the same max.
    con.isolation_level = None
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA foreign_keys=ON")
    migrate(con, target=migrate_to)
    return con


@contextlib.contextmanager
def _tx(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _ident() -> str:
    h = hashlib.blake2b(digest_size=6)
    h.update(b"%d" % time.time_ns())
    h.update(os.urandom(8))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

def _meta_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def schema_version(con: sqlite3.Connection) -> int:
    _meta_table(con)
    r = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(r[0]) if r else 0


def migrate(con: sqlite3.Connection, target: int = None) -> list:
    """Apply pending migrations. Returns the versions that ran, [] if none did.

    Safe to call on every connect and safe to call twice: the version row is
    the gate, and a version already recorded is skipped without opening a
    transaction.
    """
    _meta_table(con)
    at = schema_version(con)
    want = LATEST if target is None else target
    ran = []
    for version, stmts in MIGRATIONS:
        if version <= at or version > want:
            continue
        with _tx(con):
            for s in stmts:
                con.execute(s)
            con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                        (str(version),))
            con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        ("migrated_%d" % version, "%.6f" % time.time()))
        ran.append(version)
    if want >= 1:
        _ensure_fts(con)
    return ran


def _ensure_fts(con: sqlite3.Connection) -> bool:
    """Create the FTS index if this SQLite build has FTS5. Never fatal.

    Checked, not assumed: FTS5 is a compile-time option and Python's bundled
    SQLite is whatever the platform shipped. A build without it still stores and
    still searches; `search` reports which engine answered.
    """
    if fts_ready(con):
        return True
    fresh = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'messages_fts'").fetchone()[0] == 0
    try:
        with _tx(con):
            for s in FTS_SQL:
                con.execute(s)
            if fresh:
                # A database created on a build without FTS5 and opened later on
                # one that has it would otherwise carry an empty index.
                con.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    except sqlite3.Error:
        return False
    return fts_ready(con)


def fts_ready(con: sqlite3.Connection) -> bool:
    try:
        con.execute("SELECT rowid FROM messages_fts LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------

def new_conversation(title: str = "", model: str = "auto", db: str = None,
                     con: sqlite3.Connection = None) -> str:
    own = con is None
    con = con or connect(db)
    try:
        cid, now = _ident(), time.time()
        with _tx(con):
            con.execute("INSERT INTO conversations (id, title, created, updated, model, archived)"
                        " VALUES (?,?,?,?,?,0)", (cid, title, now, now, model))
        return cid
    finally:
        if own:
            con.close()


def get_conversation(cid: str, db: str = None, con: sqlite3.Connection = None) -> dict:
    own = con is None
    con = con or connect(db)
    try:
        r = con.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if r is None:
            r = con.execute("SELECT * FROM conversations WHERE id LIKE ? ORDER BY updated DESC",
                            (cid + "%",)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            con.close()


def list_conversations(include_archived: bool = False, limit: int = 200, db: str = None,
                       con: sqlite3.Connection = None) -> list:
    own = con is None
    con = con or connect(db)
    try:
        sql = ("SELECT c.*,"
               " (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS messages,"
               " (SELECT COUNT(*) FROM runs r WHERE r.conversation_id = c.id) AS runs,"
               " (SELECT m.content FROM messages m WHERE m.conversation_id = c.id"
               "   ORDER BY m.seq DESC LIMIT 1) AS last_content"
               " FROM conversations c")
        if not include_archived:
            sql += " WHERE c.archived = 0"
        sql += " ORDER BY c.updated DESC LIMIT ?"
        return [dict(r) for r in con.execute(sql, (limit,)).fetchall()]
    finally:
        if own:
            con.close()


def rename(cid: str, title: str, db: str = None, con: sqlite3.Connection = None) -> bool:
    return _touch(cid, {"title": title}, db, con)


def set_model(cid: str, model: str, db: str = None, con: sqlite3.Connection = None) -> bool:
    return _touch(cid, {"model": model}, db, con)


def archive(cid: str, on: bool = True, db: str = None, con: sqlite3.Connection = None) -> bool:
    return _touch(cid, {"archived": 1 if on else 0}, db, con)


def _touch(cid, fields, db, con) -> bool:
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(cid, con=con)
        if not c:
            return False
        sets = ", ".join("%s = ?" % k for k in fields) + ", updated = ?"
        with _tx(con):
            con.execute("UPDATE conversations SET %s WHERE id = ?" % sets,
                        list(fields.values()) + [time.time(), c["id"]])
        return True
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Characters over four. An estimate, and labelled as one everywhere.

    These CLIs are driven in text mode and report no usage, so the choice is
    between an admitted approximation and a fabricated exact number.
    """
    return (len(text) + 3) // 4


def add_message(conversation_id: str, role: str, content: str, model: str = "",
                tokens: int = None, run_id: str = None, meta: dict = None,
                db: str = None, con: sqlite3.Connection = None) -> dict:
    """Write one turn and commit it. This is the function the whole file is for.

    Returns the stored row. The caller may then go and do something slow and
    failure-prone; whatever happens, this is already on disk.
    """
    if role not in ROLES:
        raise ValueError("role must be one of %s, got %r" % (", ".join(ROLES), role))
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(conversation_id, con=con)
        if not c:
            raise KeyError("no conversation %r" % conversation_id)
        m = dict(meta or {})
        if tokens is None:
            tokens = estimate_tokens(content)
            m.setdefault("tokens_estimated", True)
        mid, now = _ident(), time.time()
        with _tx(con):
            seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages"
                              " WHERE conversation_id = ?", (c["id"],)).fetchone()[0]
            con.execute("INSERT INTO messages (id, conversation_id, role, content, model,"
                        " tokens, created, run_id, seq, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (mid, c["id"], role, content, model, int(tokens), now, run_id, seq,
                         json.dumps(m, ensure_ascii=False)))
            con.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, c["id"]))
            if not c["title"] and role == "user":
                con.execute("UPDATE conversations SET title = ? WHERE id = ?",
                            (title_from(content), c["id"]))
        return get_message(mid, con=con)
    finally:
        if own:
            con.close()


def title_from(text: str, width: int = 58) -> str:
    """First line, collapsed, clipped. Titles are derived, never invented."""
    t = " ".join(text.strip().split())
    return t if len(t) <= width else t[:width - 1].rstrip() + "…"


def get_message(mid: str, db: str = None, con: sqlite3.Connection = None) -> dict:
    own = con is None
    con = con or connect(db)
    try:
        r = con.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        return _msg(r) if r else None
    finally:
        if own:
            con.close()


def _msg(r) -> dict:
    d = dict(r)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except ValueError:
        d["meta"] = {}
    return d


def messages(conversation_id: str, limit: int = None, db: str = None,
             con: sqlite3.Connection = None) -> list:
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(conversation_id, con=con)
        if not c:
            return []
        # rowid breaks the tie for rows backfilled by migration 2 in the rare
        # case two of them landed in the same conversation at the same seq.
        sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC, rowid ASC"
        rows = con.execute(sql, (c["id"],)).fetchall()
        if limit:
            rows = rows[-limit:]
        return [_msg(r) for r in rows]
    finally:
        if own:
            con.close()


def unanswered(conversation_id: str, db: str = None, con: sqlite3.Connection = None) -> dict:
    """The user turn that never got a reply, or None.

    Derived, not stored. A `status` column would be one more thing that can
    disagree with reality after a crash; "the last message is the user's" cannot.
    """
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(conversation_id, con=con)
        if not c:
            return None
        r = con.execute("SELECT * FROM messages WHERE conversation_id = ?"
                        " ORDER BY seq DESC LIMIT 1", (c["id"],)).fetchone()
        return _msg(r) if r and r["role"] == "user" else None
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def new_run(conversation_id: str, brief: str, lanes: list = None, artifacts_dir: str = "",
            status: str = "queued", db: str = None, con: sqlite3.Connection = None) -> str:
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(conversation_id, con=con) if conversation_id else None
        rid = _ident()
        with _tx(con):
            con.execute("INSERT INTO runs (id, conversation_id, brief, lanes, gates_total,"
                        " gates_failed, artifacts_dir, created, status) VALUES (?,?,?,?,0,0,?,?,?)",
                        (rid, c["id"] if c else None, brief,
                         json.dumps(list(lanes or []), ensure_ascii=False),
                         artifacts_dir, time.time(), status))
        return rid
    finally:
        if own:
            con.close()


def update_run(rid: str, db: str = None, con: sqlite3.Connection = None, **fields) -> bool:
    allowed = {"gates_total", "gates_failed", "artifacts_dir", "status", "lanes", "brief"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError("not a run column: %s" % ", ".join(sorted(bad)))
    if not fields:
        return False
    if "lanes" in fields:
        fields["lanes"] = json.dumps(list(fields["lanes"]), ensure_ascii=False)
    own = con is None
    con = con or connect(db)
    try:
        with _tx(con):
            cur = con.execute("UPDATE runs SET %s WHERE id = ?"
                              % ", ".join("%s = ?" % k for k in fields),
                              list(fields.values()) + [rid])
        return cur.rowcount > 0
    finally:
        if own:
            con.close()


def runs(conversation_id: str = None, limit: int = 100, db: str = None,
         con: sqlite3.Connection = None) -> list:
    own = con is None
    con = con or connect(db)
    try:
        if conversation_id:
            c = get_conversation(conversation_id, con=con)
            rows = con.execute("SELECT * FROM runs WHERE conversation_id = ?"
                               " ORDER BY created DESC LIMIT ?",
                               (c["id"] if c else conversation_id, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM runs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["lanes"] = json.loads(d["lanes"])
            except ValueError:
                d["lanes"] = []
            out.append(d)
        return out
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _fts_query(q: str) -> str:
    """Turn free text into an FTS5 MATCH expression that cannot raise.

    Every token becomes a quoted phrase, ANDed. This throws away FTS5's query
    language (NEAR, OR, prefix *) on purpose: the input is whatever a person
    typed into a search box, and a syntax error there is a bug report, not a
    feature. Quoting also makes `"can't"` and `a:b` safe.
    """
    toks = ["".join(ch for ch in t if ch.isalnum() or ch in "_-'") for t in q.split()]
    toks = [t.replace('"', "") for t in toks if t]
    return " ".join('"%s"' % t for t in toks)


def _like_pattern(q: str) -> str:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + esc + "%"


def search(q: str, limit: int = 20, conversation_id: str = None, engine: str = "auto",
           include_archived: bool = False, db: str = None,
           con: sqlite3.Connection = None) -> dict:
    """Full-text search over messages.

    `engine` is "auto" (default), "fts" or "like". Auto prefers FTS5 and falls
    back to LIKE when the build has no FTS5 *or* when FTS5 returns nothing —
    the second case matters because FTS5 is token-based, so a substring inside
    a word, or inside a script the tokenizer treats as one run, is invisible to
    it and perfectly visible to LIKE.

    The engine that actually produced the returned hits is named in the result.
    A search that quietly changed strategy would make an empty result
    indistinguishable from an unsupported one.
    """
    own = con is None
    con = con or connect(db)
    try:
        have = fts_ready(con)
        out = {"query": q, "engine": None, "fts5": have, "hits": []}
        if not q.strip():
            out["engine"] = "none"
            return out

        where, args = [], []
        if conversation_id:
            c = get_conversation(conversation_id, con=con)
            where.append("m.conversation_id = ?")
            args.append(c["id"] if c else conversation_id)
        if not include_archived:
            where.append("c.archived = 0")
        tail = (" AND " + " AND ".join(where)) if where else ""

        if engine in ("auto", "fts") and have:
            expr = _fts_query(q)
            if expr:
                sql = ("SELECT m.*, c.title AS conversation_title FROM messages_fts f"
                       " JOIN messages m ON m.rowid = f.rowid"
                       " JOIN conversations c ON c.id = m.conversation_id"
                       " WHERE messages_fts MATCH ?" + tail +
                       " ORDER BY rank LIMIT ?")
                try:
                    rows = con.execute(sql, [expr] + args + [limit]).fetchall()
                except sqlite3.Error:
                    rows = []
                if rows or engine == "fts":
                    out["engine"] = "fts5"
                    out["hits"] = [_msg(r) for r in rows]
                    return out
        if engine == "fts":
            out["engine"] = "unavailable"
            return out

        sql = ("SELECT m.*, c.title AS conversation_title FROM messages m"
               " JOIN conversations c ON c.id = m.conversation_id"
               " WHERE m.content LIKE ? ESCAPE '\\'" + tail +
               " ORDER BY m.created DESC LIMIT ?")
        rows = con.execute(sql, [_like_pattern(q)] + args + [limit]).fetchall()
        out["engine"] = "like"
        out["hits"] = [_msg(r) for r in rows]
        return out
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------

EXPORT_FORMAT = "daisy-chat-1"


def export_conversation(cid: str, db: str = None, con: sqlite3.Connection = None) -> dict:
    """Everything about one conversation, as plain JSON-able data.

    Ids are carried so an import into a different database round-trips to the
    same shape; `import_conversation` reassigns them when they collide.
    """
    own = con is None
    con = con or connect(db)
    try:
        c = get_conversation(cid, con=con)
        if not c:
            return None
        return {
            "format": EXPORT_FORMAT,
            "schema_version": schema_version(con),
            "exported": time.time(),
            "conversation": c,
            "messages": messages(c["id"], con=con),
            "runs": runs(c["id"], con=con),
        }
    finally:
        if own:
            con.close()


def import_conversation(doc: dict, db: str = None, con: sqlite3.Connection = None) -> str:
    """Restore an exported conversation. Returns the id it landed under."""
    if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT:
        raise ValueError("not a %s document" % EXPORT_FORMAT)
    c = doc["conversation"]
    own = con is None
    con = con or connect(db)
    try:
        cid = c["id"]
        if con.execute("SELECT 1 FROM conversations WHERE id = ?", (cid,)).fetchone():
            cid = _ident()
        with _tx(con):
            con.execute("INSERT INTO conversations (id, title, created, updated, model, archived)"
                        " VALUES (?,?,?,?,?,?)",
                        (cid, c.get("title", ""), c.get("created", time.time()),
                         c.get("updated", time.time()), c.get("model", "auto"),
                         int(c.get("archived", 0))))
            remap = {}
            for r in doc.get("runs", []):
                rid = r["id"]
                if con.execute("SELECT 1 FROM runs WHERE id = ?", (rid,)).fetchone():
                    rid = _ident()
                remap[r["id"]] = rid
                con.execute("INSERT INTO runs (id, conversation_id, brief, lanes, gates_total,"
                            " gates_failed, artifacts_dir, created, status) VALUES (?,?,?,?,?,?,?,?,?)",
                            (rid, cid, r.get("brief", ""),
                             json.dumps(r.get("lanes", []), ensure_ascii=False),
                             int(r.get("gates_total", 0)), int(r.get("gates_failed", 0)),
                             r.get("artifacts_dir", ""), r.get("created", time.time()),
                             r.get("status", "queued")))
            for i, m in enumerate(doc.get("messages", []), 1):
                mid = m["id"]
                if con.execute("SELECT 1 FROM messages WHERE id = ?", (mid,)).fetchone():
                    mid = _ident()
                con.execute("INSERT INTO messages (id, conversation_id, role, content, model,"
                            " tokens, created, run_id, seq, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (mid, cid, m["role"], m["content"], m.get("model", ""),
                             int(m.get("tokens", 0)), m.get("created", time.time()),
                             remap.get(m.get("run_id"), m.get("run_id")),
                             int(m.get("seq") or i),
                             json.dumps(m.get("meta") or {}, ensure_ascii=False)))
        return cid
    finally:
        if own:
            con.close()


def stats(db: str = None, con: sqlite3.Connection = None) -> dict:
    own = con is None
    con = con or connect(db)
    try:
        conv = con.execute("SELECT COUNT(*), COALESCE(SUM(archived),0) FROM conversations").fetchone()
        msg = con.execute("SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM messages").fetchone()
        run = con.execute("SELECT COUNT(*), COALESCE(SUM(gates_failed),0) FROM runs").fetchone()
        return {"path": os.path.abspath(con.execute("PRAGMA database_list").fetchone()[2] or ":memory:"),
                "schema_version": schema_version(con), "fts5": fts_ready(con),
                "conversations": conv[0], "archived": conv[1],
                "messages": msg[0], "tokens_estimated": msg[1],
                "runs": run[0], "gates_failed": run[1]}
    finally:
        if own:
            con.close()
