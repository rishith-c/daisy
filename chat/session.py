"""
The conversation — and the one decision that makes it a product rather than a box.

THE BOUNDARY QUESTION
---------------------
A person types into one field. Sometimes that is a question and sometimes it is
a brief. Get it wrong in either direction and the thing is annoying: answering
"what does the physics gate check?" by spawning two worktrees is absurd, and
answering a real brief with a paragraph of prose is worse, because the person
asked for a bracket and got an essay.

THE RULE, AND WHY IT IS THIS ONE
--------------------------------
**Classify on what the message asks Daisy to PRODUCE, not on how it is phrased.
If it names something Daisy would have to build and then put through a gate, it
is a run. Otherwise it is a conversation.**

Phrasing is the obvious signal and it is the wrong one. "Can you build me a
dashboard?" is grammatically a question and is plainly a run; the brief this app
demos — "A parts dashboard for my sensor fleet ... 2.4 kg tip load, $30 for
fasteners" — contains no imperative verb at all and is plainly a run. Both fall
out correctly the moment you score the *object* rather than the sentence mood.

The single strongest signal is not a verb, it is a **threshold**. A budget, a
load case, a factor of safety, a count of endpoints, a deadline. People state
numbers like that only when they expect something to be checked against them —
which is the literal definition of a gate, and a gate is what a run is for. So
`acceptance` carries the heaviest weight in the table below, heavier than any
verb.

Interrogatives are split, because English uses one form for two acts. `can /
could / would / please` is a request wearing a question mark and gets no
penalty; `what / why / how / when / which / explain` asks for an answer and
does. Without that split, politeness would silently downgrade briefs.

WHICH WAY AMBIGUITY FALLS, AND WHY THAT IS SAFE
-----------------------------------------------
A tie goes to conversation. Not because under-triggering is the cheaper error —
it usually is not, the brief is right that a brief answered with chat is the more
irritating failure — but because of what the two errors cost *to undo*. An
unwanted run has already made worktrees, spent tokens and touched disk before
anyone can object; an unwanted chat has spent one cheap turn and is one
keystroke from the run. Errors are ranked by their recovery cost, so the
irreversible one needs the higher bar.

That only holds if the recovery really is one keystroke, so it is built in
rather than assumed: every classification comes back with the score, the named
signals that produced it, and an explicit `counter` — the other mode and the
exact way to get it. Nothing here is silent, and `/run` or `/chat` in front of
the message overrides the table outright and says so in the payload.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
    NOT a model-based classifier. Asking a model whether to call a model is
        circular, costs a round trip before every message, and is not
        reproducible — the same sentence must classify the same way twice or
        the visible explanation is a lie.
    NOT streaming. These CLIs are driven with subprocess.run through
        lab/executors.py and hand back one string at the end. Faking a
        typewriter would be inventing a property the transport does not have;
        the whole answer is returned and the UI may animate its arrival.
    NOT the factory. A run handoff writes a `runs` row and returns the command.
        labctl owns worktrees, and a chat box is not the place to spawn them.

Zero third-party dependencies.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import store                               # noqa: E402
from lab import executors                            # noqa: E402


# ---------------------------------------------------------------------------
# the signal table
#
# Weights are on a single scale and the threshold sits at 2.5, which is one
# artifact plus one verb — the smallest thing that is unambiguously a brief.
# Every entry carries the sentence shown to the person when it fires, because a
# score with no explanation is exactly the silent classification this is meant
# to avoid.
# ---------------------------------------------------------------------------

# Artifact vocabulary, grouped by the lane that would build it. The grouping is
# load-bearing twice: it detects a two-lane brief, and it is what populates
# `runs.lanes` on handoff.
ARTIFACTS = {
    "web": r"app|application|dashboard|website|web ?site|landing page|web page|webpage|"
           r"api|endpoint|route|service|micro-?service|server|backend|front-?end|"
           r"component|cli|command-line tool|script|scraper|crawler|parser|"
           r"module|library|package|schema|migration|test suite|pipeline|integration",
    "hardware": r"bracket|mount|enclosure|housing|chassis|fixture|adapter|gimbal|airframe|"
                r"pcb|circuit board|motor mount|standoff|fastener|bolt|propeller|prop|"
                r"spacer|plate|linkage|hinge|coupler|heatsink|heat sink",
}

BUILD = (r"build|make|create|implement|design|generate|write|add|refactor|port|scaffold|"
         r"ship|deploy|fix|wire up|hook up|set up|rewrite|prototype|spec out|draw|"
         r"parameteri[sz]e|model up")

# A number something could be measured against. See the docstring: this is the
# single best predictor, so it is weighted above every verb.
ACCEPTANCE = (r"\$\s?\d"
              r"|\b\d+(?:\.\d+)?\s?(?:kg|kgs|g|lb|lbs|n|kn|newtons?|nm|mm|cm|"
              r"in|inch|inches|ms|s|sec|seconds?|kb|mb|gb|fps|hz|%)\b"
              r"|\bfos\b|\bfactor of safety\b|\bsafety factor\b"
              r"|\b\d+\s+(?:endpoints?|routes?|pages?|screens?|tests?|columns?|fields?|"
              r"lanes?|files?|items?|rows?)\b"
              r"|\b(?:under|below|within|at most|no more than|less than|at least|"
              r"greater than|over)\s+\$?\d"
              r"|\bmust\b|\bshall\b|\bbudget\b|\bceiling\b|\bdeadline\b|\bwithin budget\b")

FILE_TARGET = r"\b[\w./-]+\.(?:py|js|mjs|ts|tsx|jsx|html|css|json|md|sh|toml|ya?ml|step|stl|sql|rs|go|swift)\b"

# "can you ..." is a request in interrogative clothing. It must not be penalised.
REQUEST_Q = r"^\s*(?:can|could|would|will|please|pls|go ahead|i need you to|i want you to|let'?s)\b"
INFO_Q_LEAD = (r"^\s*(?:what|why|how|when|where|which|who|whose|whom|is|are|was|were|"
               r"do|does|did|should|am|have|has)\b")
INFO_Q_BODY = (r"\b(?:explain|summari[sz]e|summary of|describe|tell me about|"
               r"walk me through|remind me|what'?s the difference|any idea)\b")

# Questions about work already done, rather than work to be done.
RETROSPECTIVE = (r"\b(?:last|previous|earlier|prior|that)\s+(?:run|build|conversation|message|brief)\b"
                 r"|\brun\s+\d{3,}\b|\bwhat happened\b|\bwhy did\b|\bwhat did (?:you|it|daisy)\b"
                 r"|\bshow me (?:the|my|all)\b|\bhow many\b|\bhistory\b")

PLEASANTRY = (r"^\s*(?:hi|hey|hello|yo|sup|thanks|thank you|ta|ok|okay|k|got it|nice|cool|"
              r"neat|sure|yes|yeah|yep|no|nope|nah|sounds good|good morning|good afternoon|"
              r"good evening|morning|night)\b[\s!.,?—-]*$")

RUN_THRESHOLD = 2.5


@dataclass
class Signal:
    name: str
    weight: float
    why: str
    pattern: str = ""
    fn: object = None            # for signals a regex cannot express

    def hits(self, text: str) -> list:
        if self.fn is not None:
            return self.fn(text)
        return [m.group(0) for m in re.finditer(self.pattern, text, re.I)][:4]


def _artifact_lanes(text: str) -> list:
    """Which build lanes this message names. Also feeds runs.lanes."""
    return sorted(lane for lane, vocab in ARTIFACTS.items()
                  if re.search(r"\b(?:%s)s?\b" % vocab, text, re.I))


def _artifact_hits(text: str) -> list:
    out = []
    for vocab in ARTIFACTS.values():
        out += [m.group(0) for m in re.finditer(r"\b(?:%s)s?\b" % vocab, text, re.I)]
    return out[:4]


def _two_lane_hits(text: str) -> list:
    lanes = _artifact_lanes(text)
    return lanes if len(lanes) > 1 else []


def _question_hits(text: str) -> list:
    if re.search(REQUEST_Q, text, re.I):
        return []                      # a request, not a question
    m = re.search(INFO_Q_LEAD, text, re.I) or re.search(INFO_Q_BODY, text, re.I)
    if m:
        return [m.group(0).strip()]
    return ["?"] if text.rstrip().endswith("?") else []


SIGNALS = [
    Signal("acceptance", +2.0,
           "states a threshold — a number a gate could fail", pattern=ACCEPTANCE),
    Signal("artifact", +1.5,
           "names something Daisy would have to build and then gate", fn=_artifact_hits),
    Signal("build_verb", +1.5,
           "asks for work to be done rather than for an answer", pattern=BUILD),
    Signal("file_target", +1.0,
           "points at a specific file to change", pattern=FILE_TARGET),
    Signal("two_lanes", +1.0,
           "spans more than one build lane, which only a run can coordinate", fn=_two_lane_hits),
    Signal("question", -2.0,
           "asks for an answer, not an artifact", fn=_question_hits),
    Signal("retrospective", -1.5,
           "asks about work already done rather than new work", pattern=RETROSPECTIVE),
    Signal("pleasantry", -3.0,
           "conversational, with nothing named to build", pattern=PLEASANTRY),
]

OVERRIDES = {"/run": "run", "/build": "run", "/chat": "chat", "/ask": "chat"}


@dataclass
class Classification:
    mode: str
    score: float
    threshold: float
    text: str                        # the message with any override prefix removed
    lanes: list = field(default_factory=list)
    fired: list = field(default_factory=list)
    override: str = ""
    why: str = ""
    counter: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "score": round(self.score, 2), "threshold": self.threshold,
                "lanes": self.lanes, "signals": self.fired, "override": self.override,
                "why": self.why, "counter": self.counter}


def strip_override(text: str) -> tuple:
    """Split a leading /run or /chat off the message. Returns (mode, remainder)."""
    m = re.match(r"^\s*(/[a-z]+)\s*(.*)$", text, re.S | re.I)
    if not m:
        return "", text
    word = m.group(1).lower()
    if word not in OVERRIDES:
        return "", text
    return OVERRIDES[word], m.group(2)


def classify(text: str, mode: str = None) -> Classification:
    """Decide whether this message is a conversation turn or a run.

    Deterministic and offline: the same sentence classifies the same way every
    time, which is the precondition for showing a person *why*.
    """
    prefix_mode, body = strip_override(text)
    body = body.strip() or text.strip()
    lanes = _artifact_lanes(body)

    fired, score = [], 0.0
    for sig in SIGNALS:
        hits = sig.hits(body)
        if not hits:
            continue
        score += sig.weight
        fired.append({"name": sig.name, "weight": sig.weight, "why": sig.why,
                      "matched": sorted(set(h.strip() for h in hits))})

    decided = "run" if score >= RUN_THRESHOLD else "chat"
    override = ""
    if mode in ("run", "chat"):
        override = "mode=%s" % mode
    elif prefix_mode:
        override = "/%s" % prefix_mode
    final = mode if mode in ("run", "chat") else (prefix_mode or decided)

    c = Classification(mode=final, score=score, threshold=RUN_THRESHOLD, text=body,
                       lanes=lanes if final == "run" else [], fired=fired, override=override)
    c.why = _why(c, decided)
    c.counter = _counter(c)
    return c


def _why(c: Classification, decided: str) -> str:
    pos = [f for f in c.fired if f["weight"] > 0]
    neg = [f for f in c.fired if f["weight"] < 0]
    if c.override:
        agreed = decided == c.mode
        return ("%s, because you asked for it (%s). The table %s"
                % ("Running this" if c.mode == "run" else "Answering this", c.override,
                   "agreed: %s at %.1f." % (decided, c.score) if agreed
                   else "would have chosen %s at %.1f." % (decided, c.score)))
    if c.mode == "run":
        return ("Run: %s. Score %.1f, threshold %.1f."
                % ("; ".join(f["why"] for f in pos) or "run signals outweighed the rest",
                   c.score, c.threshold))
    if not c.fired:
        return ("Conversation: nothing here names something to build. "
                "Score 0.0, threshold %.1f." % c.threshold)
    return ("Conversation: %s. Score %.1f, below the %.1f a run needs."
            % ("; ".join(f["why"] for f in (neg or pos)), c.score, c.threshold))


def _counter(c: Classification) -> dict:
    other = "chat" if c.mode == "run" else "run"
    return {"mode": other,
            "how": "/%s " % other,
            "label": "Answer it instead" if other == "chat" else "Build it instead"}


# ---------------------------------------------------------------------------
# prompt assembly
# ---------------------------------------------------------------------------

# Characters, not tokens: nothing here can count tokens honestly (see
# store.estimate_tokens), so the budget is stated in the unit that is actually
# measured. Roughly 3k tokens of history, which leaves a CLI's context free for
# the answer without truncating anything a person is likely to still care about.
CHAR_BUDGET = 12000

SYSTEM = ("You are Daisy — a build system that can also hold a conversation. "
          "This message was classified as a question, not a brief, so answer it "
          "directly and briefly in prose. Do not create files, do not run "
          "commands, and do not start work. If the answer is that this really "
          "needs a build, say so in one line.")

OMITTED = "--- earlier turns omitted: %d message%s, %d characters ---"


def assemble(history: list, text: str, budget: int = CHAR_BUDGET) -> tuple:
    """Build the prompt from prior turns, newest first, within a budget.

    Returns (prompt, report). The newest turn is never trimmed — truncating what
    someone just typed to make room for what they typed last week is the wrong
    trade in every case.

    When history is dropped, it is dropped loudly: the report says how much, and
    the prompt itself carries a line telling the model that earlier turns are
    missing. A model that thinks it has the whole conversation will confidently
    contradict the part it cannot see.
    """
    head = "%s\n\n" % SYSTEM
    tail = "user: %s" % text

    def fit(reserve):
        """Pack newest-first into the room left after `reserve`."""
        room = budget - len(head) - len(tail) - reserve
        kept, used = [], 0
        for m in reversed(history):
            line = "%s: %s" % (m["role"], m["content"])
            if used + len(line) + 1 > room:
                break
            kept.append(line)
            used += len(line) + 1
        kept.reverse()
        return kept

    # Two passes, because the omission notice is only added when something is
    # omitted — and it occupies budget it was never charged for. Packing first
    # and prepending the notice afterwards overshot by exactly the notice's
    # length (measured: 1227 against a 1200 budget). The second pass re-packs
    # with that cost reserved, so the notice can never push the prompt over the
    # ceiling it exists to explain.
    kept = fit(0)
    dropped = len(history) - len(kept)
    if dropped:
        notice = OMITTED % (dropped, "" if dropped == 1 else "s", 0) + "\n"
        kept = fit(len(notice) + 16)      # slack for a wider count in the notice
        dropped = len(history) - len(kept)

    dropped_chars = sum(len(m["content"]) + len(m["role"]) + 2 for m in history[:dropped])

    body = ""
    if dropped:
        body += OMITTED % (dropped, "" if dropped == 1 else "s", dropped_chars) + "\n"
    if kept:
        body += "\n".join(kept) + "\n"
    prompt = head + body + tail

    # The newest turn is never trimmed, so a single enormous message can still
    # exceed the budget on its own. That is reported, not silently truncated —
    # cutting what someone just typed is the wrong trade in every case.
    report = {"budget": budget, "used": len(prompt), "kept": len(kept), "dropped": dropped,
              "dropped_chars": dropped_chars,
              "over_budget": len(prompt) > budget}
    return prompt, report


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------

# Probing costs up to three real CLI invocations. Doing that before every
# message would put 75 seconds of worst case in front of "hello", so the result
# is held briefly. Only the real prober is cached — an injected one is a test
# double and caching it would leak state between cases.
PROBE_TTL = 300.0
_CACHE = {"at": 0.0, "probed": []}


def reset_probe_cache() -> None:
    _CACHE["at"], _CACHE["probed"] = 0.0, []


def probed(pick=None, refresh: bool = False) -> list:
    pick = pick or executors.pick
    real = pick is executors.pick
    if real and not refresh and _CACHE["probed"] and (time.time() - _CACHE["at"]) < PROBE_TTL:
        return _CACHE["probed"]
    _, all_probed = pick("auto")
    if real:
        _CACHE["at"], _CACHE["probed"] = time.time(), all_probed
    return all_probed


def choose(model: str, pick=None, allow_substitute: bool = False) -> tuple:
    """Return (executor_or_None, note). Never raises.

    A conversation records which agent answered it. When that agent stops being
    usable — an expired credential, an upgraded CLI — the honest default is to
    stop and say which one is missing and why, not to answer as a different
    model under the old one's name. `allow_substitute` opts into the swap and
    the swap is reported.
    """
    all_probed = probed(pick=pick)
    by_name = {e.name: e for e in all_probed}
    want = (model or "auto").strip() or "auto"

    if want != "auto":
        ex = by_name.get(want)
        if ex is None:
            return None, {"kind": "unknown", "model": want,
                          "detail": "no executor named %r; this machine has %s"
                                    % (want, ", ".join(sorted(by_name)) or "none")}
        if ex.ok:
            return ex, {}
        if not allow_substitute:
            usable = [e.name for e in all_probed if e.ok]
            return None, {"kind": "unavailable", "model": want, "detail": ex.detail,
                          "available": usable}
        for e in all_probed:
            if e.ok:
                return e, {"kind": "substituted", "from": want, "to": e.name,
                           "detail": "%s is unusable (%s)" % (want, ex.detail)}
        return None, {"kind": "unavailable", "model": want, "detail": ex.detail, "available": []}

    for e in all_probed:
        if e.ok:
            return e, {}
    return None, {"kind": "none", "model": "auto", "available": [],
                  "detail": "; ".join("%s: %s" % (e.name, e.detail) for e in all_probed)
                            or "no coding CLIs found on this machine"}


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

LABCTL = "python3 labctl.py run --brief %r"


def send(conversation_id: str, text: str, mode: str = None, db: str = None,
         con=None, pick=None, run=None, allow_substitute: bool = False,
         budget: int = CHAR_BUDGET, timeout: int = None) -> dict:
    """One turn. Classify, persist, then either answer or hand off to a run.

    The order is the contract: the user's message is committed before anything
    that can fail, hang, or be killed. Everything after that point is recovery,
    and `store.unanswered` plus `resume` are what recovery looks like.
    """
    if not (text or "").strip():
        return {"ok": False, "reason": "empty message", "conversation_id": conversation_id}

    own = con is None
    con = con or store.connect(db)
    try:
        conv = store.get_conversation(conversation_id, con=con)
        if not conv:
            return {"ok": False, "reason": "no conversation %r" % conversation_id,
                    "conversation_id": conversation_id}

        c = classify(text, mode=mode)

        # ---- the write that must not be skipped -------------------------
        user_msg = store.add_message(conv["id"], "user", text, con=con,
                                     meta={"classification": c.as_dict()})

        base = {"ok": True, "conversation_id": conv["id"], "mode": c.mode,
                "classification": c.as_dict(), "message": user_msg,
                "streaming": False,
                "streaming_note": "these CLIs return one string at the end; "
                                  "no token stream exists to forward"}

        if c.mode == "run":
            return _handoff(con, conv, c, user_msg, base)
        return _answer(con, conv, c, user_msg, base, pick, run, allow_substitute,
                       budget, timeout)
    finally:
        if own:
            con.close()


def _handoff(con, conv, c, user_msg, base) -> dict:
    rid = store.new_run(conv["id"], brief=c.text, lanes=c.lanes, status="queued", con=con)
    con.execute("UPDATE messages SET run_id = ? WHERE id = ?", (rid, user_msg["id"]))
    note = ("Queued as a run%s. %s\n\nStart it with:\n    %s"
            % (" across the %s lane%s" % (" and ".join(c.lanes), "" if len(c.lanes) == 1 else "s")
               if c.lanes else "",
               c.why, LABCTL % c.text))
    reply = store.add_message(conv["id"], "system", note, run_id=rid, con=con,
                              meta={"handoff": True, "run_id": rid})
    base["run"] = {"id": rid, "status": "queued", "lanes": c.lanes,
                   "command": LABCTL % c.text}
    base["reply"] = reply
    # Deliberate: this writes the row and hands back the command. Spawning
    # worktrees belongs to labctl, and a chat box that quietly starts a factory
    # is the surprise this whole module exists to prevent.
    base["started"] = False
    return base


def _answer(con, conv, c, user_msg, base, pick, run, allow_substitute, budget, timeout) -> dict:
    ex, note = choose(conv["model"], pick=pick, allow_substitute=allow_substitute)
    if ex is None:
        base.update(ok=False, reply=None, agent=None, model_note=note,
                    reason=_unavailable_reason(conv["model"], note),
                    recoverable=True,
                    recovery="the message is stored; `chat.cli send --resume` retries it, "
                             "and `set-model` points the conversation at a usable agent")
        return base

    prior = [m for m in store.messages(conv["id"], con=con) if m["id"] != user_msg["id"]
             and m["role"] in ("user", "assistant")]
    prompt, trim = assemble(prior, c.text, budget=budget)

    runner = run or executors.run
    kw = {"timeout": timeout} if timeout else {}
    try:
        res = runner(ex, prompt, **kw)
    except Exception as exc:                      # an executor must never take the turn down
        base.update(ok=False, reply=None, agent=ex.name, trimmed=trim,
                    reason="%s raised %s: %s" % (ex.name, type(exc).__name__, str(exc)[:160]),
                    recoverable=True)
        return base

    if not res.get("ok"):
        base.update(ok=False, reply=None, agent=ex.name, trimmed=trim,
                    reason=res.get("reason") or "the agent returned nothing usable",
                    ms=res.get("ms"), recoverable=True)
        return base

    answer = (res.get("stdout") or "").strip()
    meta = {"trimmed": trim, "ms": res.get("ms"), "prompt_chars": len(prompt)}
    if note.get("kind") == "substituted":
        meta["substituted"] = note
    reply = store.add_message(conv["id"], "assistant", answer, model=ex.name, con=con, meta=meta)
    base.update(reply=reply, agent=ex.name, ms=res.get("ms"), trimmed=trim,
                substituted=note if note.get("kind") == "substituted" else None)
    if trim["dropped"]:
        base["trim_note"] = ("%d earlier message%s did not fit the %d-character budget and "
                             "were left out of the prompt; the model was told so."
                             % (trim["dropped"], "" if trim["dropped"] == 1 else "s", budget))
    return base


def _unavailable_reason(model: str, note: dict) -> str:
    if note.get("kind") == "unknown":
        return note["detail"]
    if note.get("kind") == "none":
        return "no coding agent on this machine can be driven right now — %s" % note["detail"]
    avail = note.get("available") or []
    return ("this conversation runs on %r, which is not usable right now: %s. %s"
            % (model, note.get("detail") or "no reason reported",
               ("Usable here: %s." % ", ".join(avail)) if avail
               else "Nothing else is usable either."))


def resume(conversation_id: str, db: str = None, con=None, **kw) -> dict:
    """Answer the turn a crash left hanging. The payoff of writing first.

    Re-sends the stored user message rather than asking for it again. It is
    classified afresh, because the message is the same message and the rule is
    deterministic — the answer will match what was recorded before the crash.
    """
    own = con is None
    con = con or store.connect(db)
    try:
        pending = store.unanswered(conversation_id, con=con)
        if not pending:
            return {"ok": False, "reason": "nothing pending in this conversation",
                    "conversation_id": conversation_id}
        prev = (pending.get("meta") or {}).get("classification") or {}
        con.execute("DELETE FROM messages WHERE id = ?", (pending["id"],))
        return send(conversation_id, pending["content"], con=con,
                    mode=prev.get("mode") if prev.get("override") else None, **kw)
    finally:
        if own:
            con.close()
