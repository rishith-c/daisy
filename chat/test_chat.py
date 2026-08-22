"""Tests for conversation storage and the chat-vs-run boundary.

    python3 -m chat.test_chat

Every database is a tempdir and every agent is a stand-in built from the real
`lab.executors.Executor` dataclass. No model is called, no process is spawned,
and DAISY_HOME is redirected at import so the suite cannot reach the operator's
own ~/.daisy even by accident.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading

TMP = tempfile.TemporaryDirectory()
os.environ["DAISY_HOME"] = TMP.name

from lab.executors import Executor           # noqa: E402
from . import store, session                 # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


_n = [0]


def _db(tag="t"):
    _n[0] += 1
    return os.path.join(TMP.name, "%s-%d.db" % (tag, _n[0]))


# ---------------------------------------------------------------------------
# stand-ins — a real Executor record, with no process behind it
# ---------------------------------------------------------------------------

def agent(name, ok=True, detail="responded to the probe"):
    return Executor(name, name, [name, "run", "{prompt}"], ok=ok, detail=detail)


def picker(*agents):
    def pick(prefer="auto", cwd=None):
        for a in agents:
            if a.ok and (prefer == "auto" or a.name == prefer):
                return a, list(agents)
        return None, list(agents)
    return pick


def runner(reply="ack", seen=None, boom=None):
    def run(ex, prompt, **kw):
        if seen is not None:
            seen.append(prompt)
        if boom is not None:
            raise boom
        return {"agent": ex.name, "ok": True, "reason": "", "ms": 1.0,
                "stdout": reply, "stderr": ""}
    return run


def failing(reason="credential expired"):
    def run(ex, prompt, **kw):
        return {"agent": ex.name, "ok": False, "reason": reason, "ms": 1.0,
                "stdout": "", "stderr": reason}
    return run


OK = picker(agent("opencode"))


# ---------------------------------------------------------------------------

def test_empty_store():
    print("\nan empty database — every read has an honest answer")
    con = store.connect(_db("empty"))
    check("no conversations", store.list_conversations(con=con) == [])
    check("an unknown id is None, not a stub", store.get_conversation("nope", con=con) is None)
    check("messages of a missing conversation is empty", store.messages("nope", con=con) == [])
    check("nothing is pending", store.unanswered("nope", con=con) is None)
    check("no runs", store.runs(con=con) == [])
    r = store.search("anything", con=con)
    check("search returns no hits and names its engine", r["hits"] == [] and r["engine"] in ("fts5", "like"),
          str(r["engine"]))
    check("export of a missing conversation is None", store.export_conversation("nope", con=con) is None)
    s = store.stats(con=con)
    check("stats are zeros, not blanks", s["conversations"] == 0 and s["messages"] == 0 and s["runs"] == 0)
    out = session.send("nope", "hello", con=con, pick=OK, run=runner())
    check("sending to a missing conversation fails cleanly", not out["ok"] and "no conversation" in out["reason"])
    check("an empty message is refused before anything is written",
          not session.send("nope", "   ", con=con)["ok"])
    con.close()


def test_migrations():
    print("\nmigrations — repeatable, and the upgrade path is the one that breaks")
    p = _db("mig")
    con = store.connect(p, migrate_to=1)
    check("stops at the requested version", store.schema_version(con) == 1)
    cols1 = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    check("v1 is the schema as specified",
          set(cols1) == {"id", "conversation_id", "role", "content", "model", "tokens",
                         "created", "run_id"}, str(sorted(cols1)))

    # a database written by the older code, then upgraded
    now = 1.0
    con.execute("BEGIN")
    con.execute("INSERT INTO conversations VALUES ('c1','old',?,?,'auto',0)", (now, now))
    for i in range(4):
        con.execute("INSERT INTO messages (id, conversation_id, role, content, model, tokens,"
                    " created, run_id) VALUES (?,?,?,?,'',0,?,NULL)",
                    ("m%d" % i, "c1", "user", "old message %d" % i, now))
    con.execute("COMMIT")
    con.close()

    con = store.connect(p)
    check("reopening applies the pending migration", store.schema_version(con) == store.LATEST)
    cols2 = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    check("v2 adds seq and meta", "seq" in cols2 and "meta" in cols2, str(sorted(cols2)))
    check("running migrate again does nothing", store.migrate(con) == [])
    check("and again", store.migrate(con) == [])
    got = [m["content"] for m in store.messages("c1", con=con)]
    check("rows written at v1 survive", len(got) == 4, str(got))
    check("and are backfilled into their original order",
          got == ["old message %d" % i for i in range(4)], str(got))
    check("seq is 1..n after backfill",
          [m["seq"] for m in store.messages("c1", con=con)] == [1, 2, 3, 4])
    check("a v1 row parses as empty meta rather than crashing",
          store.messages("c1", con=con)[0]["meta"] == {})
    check("the version row records when it was applied",
          con.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'migrated_%'").fetchone()[0] >= 1)

    fresh = store.connect(_db("mig2"))
    check("a fresh database lands directly on the latest version",
          store.schema_version(fresh) == store.LATEST)
    fresh.close(); con.close()


def test_write_before_model():
    print("\ndurability — the prompt is on disk before the model is called")
    p = _db("crash")
    con = store.connect(p)
    cid = store.new_conversation(model="opencode", con=con)
    con.close()

    seen = []
    out = session.send(cid, "what does the taste gate check?", db=p, pick=OK,
                       run=runner("it greps for 20 named tells", seen=seen))
    check("a normal turn is answered", out["ok"] and out["reply"]["role"] == "assistant")
    check("the user turn was written before the runner saw the prompt", len(seen) == 1)

    # a hard kill mid-call: KeyboardInterrupt is not an Exception, so nothing
    # inside send() catches it — exactly what a ^C or a SIGINT does.
    boom = False
    try:
        session.send(cid, "and the physics gate?", db=p, pick=OK,
                     run=runner(boom=KeyboardInterrupt()))
    except KeyboardInterrupt:
        boom = True
    check("the interrupt escapes rather than being swallowed", boom)

    after = store.connect(p)      # a brand-new connection: this is the restart
    msgs = store.messages(cid, con=after)
    check("the interrupted prompt survived the crash",
          msgs[-1]["content"] == "and the physics gate?", str([m["content"] for m in msgs]))
    check("it is the user's turn that survived, not a placeholder answer",
          msgs[-1]["role"] == "user")
    pend = store.unanswered(cid, con=after)
    check("the store reports it as unanswered", pend is not None and pend["id"] == msgs[-1]["id"])
    check("its classification survived too",
          pend["meta"]["classification"]["mode"] == "chat")
    after.close()

    again = session.resume(cid, db=p, pick=OK, run=runner("deterministic margins"))
    check("resume answers the pending turn", again["ok"])
    check("resume did not duplicate the prompt",
          [m["content"] for m in store.messages(cid, db=p)].count("and the physics gate?") == 1)
    check("nothing is pending afterwards", store.unanswered(cid, db=p) is None)
    check("resume on a settled conversation says so",
          not session.resume(cid, db=p, pick=OK, run=runner())["ok"])

    # an agent that answers with a failure must not fabricate a reply either
    out = session.send(cid, "one more question?", db=p, pick=OK, run=failing("rate limited"))
    check("an agent failure is reported, not invented", not out["ok"] and "rate limited" in out["reason"])
    check("but the prompt is still stored", store.unanswered(cid, db=p) is not None)
    out = session.send(cid, "and another?", db=p, pick=OK, run=runner(boom=RuntimeError("pipe died")))
    check("an executor exception is caught and named",
          not out["ok"] and "pipe died" in out["reason"], out.get("reason", ""))


def test_classification():
    print("\nclassification — which side of the line, and why")
    runs = [
        "build me a fleet dashboard",
        "can you build me a dashboard?",
        "A parts dashboard for my sensor fleet, plus a mounting bracket for the SR-11. "
        "Cantilever mount, 2.4 kg tip load, $30 for fasteners.",
        "same brief at an $18 ceiling on fasteners",
        "fix the crash in lab/executors.py",
        "add an endpoint that returns fleet health in under 200 ms",
        "design an enclosure for the SR-11, must survive a 1.2 m drop",
    ]
    chats = [
        "hi",
        "thanks",
        "what does the physics gate check?",
        "why did run 1039 fail?",
        "how do I build a dashboard?",
        "explain the difference between tier 1 and tier 2 taste gates",
        "what's the FoS on the bracket?",
        "show me the last run",
        "is opencode usable on this machine",
    ]
    for t in runs:
        c = session.classify(t)
        check("run: %s" % t[:46], c.mode == "run", "got %s at %.1f" % (c.mode, c.score))
    for t in chats:
        c = session.classify(t)
        check("chat: %s" % t[:46], c.mode == "chat", "got %s at %.1f" % (c.mode, c.score))

    c = session.classify("A dashboard plus a bracket, 2.4 kg tip load")
    check("a two-lane brief names both lanes", c.lanes == ["hardware", "web"], str(c.lanes))
    check("the heaviest signal is the threshold, not the verb",
          max(s["weight"] for s in c.fired) == 2.0)

    print("\nclassification — the override, and its visibility")
    c = session.classify("/run fix the crash")
    check("a /run prefix forces a run", c.mode == "run" and c.override == "/run")
    check("the override strips the prefix from the brief", c.text == "fix the crash")
    check("and admits what the table would have said", "would have chosen chat" in c.why, c.why)
    c = session.classify("/chat build me a dashboard with a $30 budget")
    check("a /chat prefix forces a conversation", c.mode == "chat" and c.override == "/chat")
    check("even against a score well over the threshold", c.score >= session.RUN_THRESHOLD)
    c = session.classify("build me a dashboard", mode="chat")
    check("the mode argument overrides too", c.mode == "chat" and c.override == "mode=chat")
    c = session.classify("/run build me a dashboard")
    check("an override that agrees says it agreed", "agreed" in c.why, c.why)
    c = session.classify("/nonsense build me a dashboard")
    check("an unknown slash word is not an override", c.override == "" and c.mode == "run")
    check("and is left in the text rather than eaten", "/nonsense" in c.text)

    print("\nclassification — nothing is decided silently")
    for t in ("hi", "build me a dashboard", "what's the FoS?"):
        c = session.classify(t).as_dict()
        check("%r carries a reason" % t[:20], bool(c["why"]))
        check("%r carries the score and the bar" % t[:20],
              "score" in c and c["threshold"] == session.RUN_THRESHOLD)
        check("%r offers the other mode explicitly" % t[:20],
              c["counter"]["mode"] != c["mode"] and c["counter"]["how"].startswith("/"))
    c = session.classify("build me a dashboard").as_dict()
    check("every fired signal names itself and what it matched",
          all(s["name"] and s["why"] and s["matched"] for s in c["signals"]))
    check("the classifier is deterministic",
          session.classify("fix the bug").as_dict() == session.classify("fix the bug").as_dict())


def test_handoff():
    print("\nhandoff — a run is recorded, not launched")
    p = _db("run")
    cid = store.new_conversation(model="opencode", db=p)
    out = session.send(cid, "build a dashboard and a bracket, 2.4 kg tip load, $30 budget",
                       db=p, pick=picker(agent("opencode", ok=False, detail="down")),
                       run=runner())
    check("a brief hands off even with every agent down", out["ok"] and out["mode"] == "run")
    check("nothing was started", out["started"] is False)
    check("the command to start it is returned", out["run"]["command"].startswith("python3 labctl.py"))
    rs = store.runs(cid, db=p)
    check("a run row exists", len(rs) == 1 and rs[0]["status"] == "queued")
    check("with both lanes recorded", rs[0]["lanes"] == ["hardware", "web"], str(rs[0]["lanes"]))
    msgs = store.messages(cid, db=p)
    check("the user turn is linked to the run", msgs[0]["run_id"] == rs[0]["id"])
    check("and the handoff is explained in the thread", msgs[1]["role"] == "system"
          and "Queued as a run" in msgs[1]["content"])
    check("a handed-off conversation is not pending", store.unanswered(cid, db=p) is None)
    store.update_run(rs[0]["id"], status="passed", gates_total=11, gates_failed=0, db=p)
    check("run progress can be recorded", store.runs(cid, db=p)[0]["gates_total"] == 11)
    try:
        store.update_run(rs[0]["id"], nonsense=1, db=p); bad = False
    except ValueError:
        bad = True
    check("an unknown run column is refused", bad)


def test_multi_turn():
    print("\nmulti-turn — the prompt carries the conversation")
    p = _db("turns")
    cid = store.new_conversation(model="opencode", db=p)
    seen = []
    r = runner("noted", seen=seen)
    session.send(cid, "what is a lane?", db=p, pick=OK, run=r)
    session.send(cid, "and a gate?", db=p, pick=OK, run=r)
    session.send(cid, "which one runs first?", db=p, pick=OK, run=r)
    last = seen[-1]
    check("three turns produced three calls", len(seen) == 3)
    check("the first prompt had no history", "user: what is a lane?" in seen[0]
          and seen[0].count("user:") == 1)
    check("the newest turn is last in the prompt", last.rstrip().endswith("which one runs first?"))
    check("both earlier user turns are present",
          "what is a lane?" in last and "and a gate?" in last)
    check("earlier answers are present too", last.count("assistant: noted") == 2)
    check("turns are in the order they happened",
          last.index("what is a lane?") < last.index("and a gate?") < last.index("which one runs first?"))
    check("nothing was dropped", "earlier turns omitted" not in last)
    stored = store.messages(cid, db=p)
    check("six rows stored, alternating", [m["role"] for m in stored] ==
          ["user", "assistant"] * 3, str([m["role"] for m in stored]))
    check("seq is strictly increasing", [m["seq"] for m in stored] == [1, 2, 3, 4, 5, 6])
    check("the assistant rows record which agent answered",
          all(m["model"] == "opencode" for m in stored if m["role"] == "assistant"))
    check("a handoff does not pollute the next prompt's history",
          "system:" not in last)


def test_trimming():
    print("\ntrimming — history is dropped loudly or not at all")
    hist = [{"role": "user", "content": "u%02d %s" % (i, "x" * 200)} for i in range(20)]
    prompt, rep = session.assemble(hist, "the new question", budget=session.CHAR_BUDGET)
    check("a small history fits whole", rep["dropped"] == 0 and rep["kept"] == 20)
    check("and says nothing about omissions", "earlier turns omitted" not in prompt)

    prompt, rep = session.assemble(hist, "the new question", budget=1200)
    check("a tight budget drops the oldest turns", rep["dropped"] > 0, str(rep))
    check("kept + dropped is the whole history", rep["kept"] + rep["dropped"] == 20)
    check("the report says how much went", rep["dropped_chars"] > 0)
    check("the prompt itself admits the gap", "earlier turns omitted" in prompt)
    check("the omission line counts them", "%d messages" % rep["dropped"] in prompt, prompt[:200])
    check("what survived is the newest, not the oldest", "u19" in prompt and "u00" not in prompt)
    check("the budget was respected", rep["used"] <= 1200, str(rep))
    check("the report names the budget it was given", rep["budget"] == 1200)

    prompt, rep = session.assemble([], "x" * 5000, budget=1000)
    check("the newest turn is never truncated", prompt.endswith("x" * 5000))
    check("and the overrun is admitted rather than hidden", rep["over_budget"] is True)
    check("with nothing falsely reported as dropped", rep["dropped"] == 0)

    p = _db("trim")
    cid = store.new_conversation(model="opencode", db=p)
    seen = []
    for i in range(12):
        session.send(cid, "question %02d %s" % (i, "y" * 400), db=p, pick=OK,
                     run=runner("answer %02d" % i, seen=seen))
    out = session.send(cid, "final question", db=p, pick=OK, run=runner("done", seen=seen),
                       budget=1500)
    check("send reports the trim in its payload", out["trimmed"]["dropped"] > 0, str(out.get("trimmed")))
    check("and in a sentence a person can read", "did not fit" in out.get("trim_note", ""),
          out.get("trim_note", ""))
    check("the trim is stored on the reply, so it survives a restart",
          store.messages(cid, db=p)[-1]["meta"]["trimmed"]["dropped"] > 0)


def test_search():
    print("\nsearch — FTS5, and the fallback that has to exist")
    p = _db("search")
    con = store.connect(p)
    check("this build's FTS5 support was probed, not assumed",
          store.fts_ready(con) in (True, False))
    a = store.new_conversation(title="bracket", con=con)
    b = store.new_conversation(title="dashboard", con=con)
    store.add_message(a, "user", "the bending margin failed at 3.2 mm", con=con)
    store.add_message(a, "assistant", "solve for thickness: 4.61 mm clears FoS 1.5", con=con)
    store.add_message(b, "user", "the fleet dashboard needs a freshness header", con=con)

    r = store.search("bending", con=con)
    check("a word is found", len(r["hits"]) == 1 and "bending" in r["hits"][0]["content"], str(r))
    if store.fts_ready(con):
        check("by FTS5 when the build has it", r["engine"] == "fts5", r["engine"])
        r2 = store.search("bending margin", con=con)
        check("two words are ANDed", len(r2["hits"]) == 1)
        check("word order does not matter", len(store.search("margin bending", con=con)["hits"]) == 1)
        r3 = store.search('thickness" OR (', con=con)
        check("punctuation cannot produce a syntax error", r3["hits"] or r3["engine"] in ("fts5", "like"))
    check("a substring inside a word falls through to LIKE",
          store.search("reshness", con=con)["engine"] == "like")
    check("and finds it", len(store.search("reshness", con=con)["hits"]) == 1)
    check("an explicit LIKE search is honoured",
          store.search("bending", engine="like", con=con)["engine"] == "like")
    check("search can be scoped to one conversation",
          len(store.search("mm", conversation_id=a, engine="like", con=con)["hits"]) == 2)
    check("a LIKE wildcard is escaped, not executed",
          store.search("%", engine="like", con=con)["hits"] == [])
    check("an underscore is escaped too", store.search("_", engine="like", con=con)["hits"] == [])
    check("an empty query returns nothing and says so",
          store.search("   ", con=con)["engine"] == "none")

    store.archive(b, con=con)
    check("archived conversations drop out of search",
          len(store.search("freshness", engine="like", con=con)["hits"]) == 0)
    check("unless asked for",
          len(store.search("freshness", engine="like", include_archived=True, con=con)["hits"]) == 1)
    store.archive(b, on=False, con=con)
    con.close()

    # A SQLite build with no FTS5 at all: drop the index and search the same file
    # through a connection that will not recreate it.
    raw = sqlite3.connect(p)
    raw.row_factory = sqlite3.Row
    raw.executescript("DROP TRIGGER IF EXISTS messages_ai; DROP TRIGGER IF EXISTS messages_ad;"
                      " DROP TRIGGER IF EXISTS messages_au; DROP TABLE IF EXISTS messages_fts;")
    check("the probe now reports no FTS5", store.fts_ready(raw) is False)
    r = store.search("bending", con=raw)
    check("auto still answers", len(r["hits"]) == 1, str(r))
    check("and says LIKE did it", r["engine"] == "like" and r["fts5"] is False)
    r = store.search("bending", engine="fts", con=raw)
    check("an explicit FTS search refuses rather than silently substituting",
          r["engine"] == "unavailable" and r["hits"] == [])
    raw.close()

    con = store.connect(p)
    check("reopening rebuilds the index rather than leaving it empty",
          not store.fts_ready(con) or len(store.search("bending", engine="fts", con=con)["hits"]) == 1)
    store.add_message(a, "user", "one more about bending", con=con)
    check("the triggers were restored with it",
          len(store.search("bending", engine="fts", con=con)["hits"]) == 2
          if store.fts_ready(con) else True)
    con.close()


def test_text():
    print("\ntext — unicode, and messages far larger than a message should be")
    p = _db("text")
    con = store.connect(p)
    cid = store.new_conversation(con=con)
    samples = [
        "曲げ応力は 69.0 MPa で、許容値を超えています",
        "الحمل 2.4 كجم على الطرف",
        "Ωμέγα ± 3.2 mm · naïve café résumé",
        "combining: é vs é — different bytes, same glyph",
        "emoji in the content is fine: \U0001F33C daisy",
        "tabs\tand\nnewlines\r\nand a NUL-ish \\x00 literal",
    ]
    for s in samples:
        store.add_message(cid, "user", s, con=con)
    got = [m["content"] for m in store.messages(cid, con=con)]
    check("every sample round-trips byte-for-byte", got == samples,
          str([a == b for a, b in zip(got, samples)]))
    check("a CJK substring is findable via LIKE",
          len(store.search("許容値", engine="like", con=con)["hits"]) == 1)
    check("an ASCII token inside a CJK message is findable",
          len(store.search("MPa", con=con)["hits"]) >= 1)
    check("titles derived from unicode do not split a codepoint",
          isinstance(store.get_conversation(cid, con=con)["title"], str))

    big = "bending " + ("z" * 300000) + " margin"
    m = store.add_message(cid, "user", big, con=con)
    back = store.get_message(m["id"], con=con)
    check("a 300 KB message stores whole", len(back["content"]) == len(big))
    check("and reads back identical", back["content"] == big)
    check("its token count is an estimate, and flagged as one",
          back["tokens"] == store.estimate_tokens(big) and back["meta"]["tokens_estimated"] is True)
    check("it is still searchable", len(store.search("margin", con=con)["hits"]) >= 1)

    long_title = store.new_conversation(con=con)
    store.add_message(long_title, "user", "a " * 400, con=con)
    t = store.get_conversation(long_title, con=con)["title"]
    check("a derived title is clipped, not stored whole", len(t) <= 58, "%d chars" % len(t))
    check("and marked as clipped", t.endswith("…"))
    check("an empty-ish title is not invented",
          store.get_conversation(cid, con=con)["title"] == store.title_from(samples[0]))
    check("a bad role is refused", _raises(lambda: store.add_message(cid, "robot", "x", con=con),
                                          ValueError))
    check("a message to a missing conversation is refused",
          _raises(lambda: store.add_message("nope", "user", "x", con=con), KeyError))
    con.close()


def test_concurrency():
    print("\nconcurrency — two connections writing the same conversation")
    p = _db("conc")
    setup = store.connect(p)
    cid = store.new_conversation(con=setup)
    setup.close()

    errors, N = [], 25

    def writer(tag):
        con = store.connect(p)          # its own connection, as a second process would have
        try:
            for i in range(N):
                store.add_message(cid, "user", "%s-%02d" % (tag, i), con=con)
        except Exception as e:                        # noqa: BLE001 - reported, not swallowed
            errors.append("%s: %r" % (tag, e))
        finally:
            con.close()

    ts = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    check("neither writer errored", not errors, "; ".join(errors))
    con = store.connect(p)
    msgs = store.messages(cid, con=con)
    check("every message landed", len(msgs) == 2 * N, "%d of %d" % (len(msgs), 2 * N))
    seqs = [m["seq"] for m in msgs]
    check("no two messages share a seq", len(set(seqs)) == len(seqs))
    check("seq is a dense 1..n with no gaps", seqs == list(range(1, 2 * N + 1)), str(seqs[:8]))
    check("each writer's own messages kept their order",
          all(_ordered([m["content"] for m in msgs if m["content"].startswith(tag)])
              for tag in ("a", "b")))
    check("the conversation's updated time moved", store.get_conversation(cid, con=con)["updated"] > 0)
    check("a reader on a third connection sees all of it",
          store.connect(p).execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2 * N)
    con.close()


def _ordered(xs):
    return xs == sorted(xs)


def test_archive_and_export():
    print("\narchive and export — a conversation you can put down and pick up elsewhere")
    p = _db("exp")
    con = store.connect(p)
    cid = store.new_conversation(title="SR-11 bracket", model="opencode", con=con)
    con.close()
    session.send(cid, "why did the web fail at 3.2 mm?", db=p, pick=OK,
                 run=runner("sigma = 6M/(b t^2) puts it at 69 MPa"))
    session.send(cid, "design a 4.61 mm bracket, FoS 1.5, $30 of fasteners", db=p,
                 pick=OK, run=runner())

    con = store.connect(p)
    check("the conversation starts unarchived", store.get_conversation(cid, con=con)["archived"] == 0)
    check("archiving works", store.archive(cid, con=con))
    check("and hides it from the default list",
          all(c["id"] != cid for c in store.list_conversations(con=con)))
    check("but not from --all", any(c["id"] == cid for c in store.list_conversations(True, con=con)))
    check("unarchiving restores it", store.archive(cid, on=False, con=con)
          and any(c["id"] == cid for c in store.list_conversations(con=con)))
    check("archiving something that does not exist is False", not store.archive("nope", con=con))

    doc = store.export_conversation(cid, con=con)
    check("the export names its format", doc["format"] == store.EXPORT_FORMAT)
    check("it carries every message", len(doc["messages"]) == 4, str(len(doc["messages"])))
    check("and the run the brief produced", len(doc["runs"]) == 1)
    check("it is JSON, not a repr", json.loads(json.dumps(doc, default=str))["format"] == doc["format"])
    check("classification travelled with it",
          doc["messages"][0]["meta"]["classification"]["mode"] == "chat")
    con.close()

    other = store.connect(_db("exp2"))
    back = store.import_conversation(doc, con=other)
    check("it imports into an empty database", back == cid)
    check("with the same messages, in the same order",
          [m["content"] for m in store.messages(back, con=other)]
          == [m["content"] for m in doc["messages"]])
    check("the model survived", store.get_conversation(back, con=other)["model"] == "opencode")
    check("the run survived and still points at its conversation",
          store.runs(back, con=other)[0]["conversation_id"] == back)
    check("the run link on the message was remapped, not left dangling",
          all(m["run_id"] is None or m["run_id"] in {r["id"] for r in store.runs(back, con=other)}
              for m in store.messages(back, con=other)))
    check("re-importing the same document does not collide",
          store.import_conversation(doc, con=other) != back)
    check("a foreign document is refused",
          _raises(lambda: store.import_conversation({"format": "something-else"}, con=other), ValueError))
    other.close()

    same = store.connect(p)
    round2 = store.export_conversation(cid, con=same)
    check("exporting twice gives the same content",
          [m["content"] for m in round2["messages"]] == [m["content"] for m in doc["messages"]])
    same.close()


def test_model_gone():
    print("\nexecutors — a conversation whose model is no longer there")
    session.reset_probe_cache()
    p = _db("gone")
    cid = store.new_conversation(model="claude", db=p)
    gone = picker(agent("claude", ok=False, detail="credential expired — sign in once"),
                  agent("opencode", ok=True))

    out = session.send(cid, "what changed?", db=p, pick=gone, run=runner("hi"))
    check("the turn is not answered", not out["ok"])
    check("the reason names the missing model", "'claude'" in out["reason"], out["reason"])
    check("and why it is missing", "credential expired" in out["reason"])
    check("and what is usable instead", "opencode" in out["reason"])
    check("no answer was fabricated", out["reply"] is None)
    check("the prompt is stored anyway", store.unanswered(cid, db=p) is not None)
    check("it is flagged recoverable, with the way out named",
          out["recoverable"] and "set-model" in out["recovery"])

    out = session.send(cid, "try anything", db=p, pick=gone, run=runner("answered"),
                       allow_substitute=True)
    check("opting in lets another agent answer", out["ok"])
    check("the substitution is reported, not silent",
          out["substituted"]["from"] == "claude" and out["substituted"]["to"] == "opencode")
    check("the reply records who really answered", out["reply"]["model"] == "opencode")
    check("and the row remembers the substitution after a restart",
          store.messages(cid, db=p)[-1]["meta"]["substituted"]["to"] == "opencode")

    store.set_model(cid, "opencode", db=p)
    out = session.send(cid, "and now?", db=p, pick=gone, run=runner("fine"))
    check("pointing the conversation at a usable model fixes it", out["ok"])
    check("with no substitution note, because none happened", out["substituted"] is None)

    none = picker(agent("claude", ok=False, detail="expired"),
                  agent("codex", ok=False, detail="CLI too old"))
    cid2 = store.new_conversation(model="auto", db=p)
    out = session.send(cid2, "anyone home?", db=p, pick=none, run=runner())
    check("with nothing usable, auto fails honestly", not out["ok"])
    check("naming every agent and its reason",
          "claude: expired" in out["reason"] and "codex: CLI too old" in out["reason"], out["reason"])

    cid3 = store.new_conversation(model="gemini", db=p)
    out = session.send(cid3, "hello?", db=p, pick=OK, run=runner())
    check("a model this machine never had is a different message",
          not out["ok"] and "no executor named" in out["reason"], out["reason"])

    print("\nexecutors — a brief does not need a model at all")
    out = session.send(cid2, "build a dashboard, $30 budget", db=p, pick=none, run=runner())
    check("the handoff succeeds with every agent down", out["ok"] and out["mode"] == "run")

    print("\nexecutors — streaming is not faked")
    out = session.send(cid, "one line please", db=p, pick=gone, run=runner("whole answer"))
    check("send declares that it does not stream", out["streaming"] is False)
    check("and says why", "no token stream exists" in out["streaming_note"])
    check("the whole answer arrives at once", out["reply"]["content"] == "whole answer")


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


def main():
    print("chat — conversation and durable storage")
    try:
        test_empty_store()
        test_migrations()
        test_write_before_model()
        test_classification()
        test_handoff()
        test_multi_turn()
        test_trimming()
        test_search()
        test_text()
        test_concurrency()
        test_archive_and_export()
        test_model_gone()
    finally:
        TMP.cleanup()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
