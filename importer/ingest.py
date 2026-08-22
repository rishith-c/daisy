"""
Bring a selected source into Daisy.

Two properties matter more here than the feature does, and both are enforced by
construction rather than by care:

    idempotent      every item carries a content digest. The ledger remembers
                    it, and an item whose digest has not moved is skipped before
                    anything is serialised. Running an import twice writes zero
                    bytes the second time — `test_importer.py` asserts on the
                    file mtimes, not just on the contents.

    non-destructive nothing already in Daisy's state is overwritten by an
                    import. config.md is append-only inside delimited blocks;
                    the registry is keyed by source, so two tools that both
                    define a server called `github` each keep their own entry
                    and the collision is *reported* rather than resolved. An
                    importer that silently picks a winner is an importer that
                    loses work.

Nothing is imported without an explicit selection. There is no "import
everything": `run()` takes one source id, the CLI requires `--source`, and the
default posture is a dry run that prints the diff and writes nothing.

What each kind becomes:

    sessions    a summarised row in Daisy's run history. The transcript stays
                where it is — copying another tool's chat log into this repo
                would duplicate gigabytes to display a table.
    rules       an appended block in config.md, shown as a unified diff first.
    mcp         a registry entry: name, transport, and the *names* of the env
                vars and headers it needs. No credential value is ever copied.
    skills      a registry entry pointing at the directory it already lives in.
    hooks       a registry entry, listed and not installed. Daisy will not wire
                another tool's shell commands into its own run loop because
                somebody pressed Import.

Zero third-party dependencies.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field, asdict

from .detect import by_id
from .state import BLOCK_CLOSE, BLOCK_OPEN, CONFIG_HEADER, State, digest

# config.md block tags have to survive being read by a human, so the path is
# slugged rather than hashed.
_SLUG = re.compile(r"[^A-Za-z0-9]+")


class UnknownSource(Exception):
    """Asked to import something detection never offered."""


class NotImportable(Exception):
    """The source exists but Daisy has no honest way to bring it in."""


@dataclass
class Change:
    item: str
    label: str
    status: str          # added | updated | unchanged | conflict
    detail: str = ""


@dataclass
class Report:
    source: str
    kind: str
    dry_run: bool
    changes: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    writes: list = field(default_factory=list)
    diff: list = field(default_factory=list)
    effects: list = field(default_factory=list)

    def tally(self, status: str) -> int:
        return sum(1 for c in self.changes if c.status == status)

    def summary(self) -> str:
        return "%d added, %d updated, %d unchanged, %d conflict%s" % (
            self.tally("added"), self.tally("updated"), self.tally("unchanged"),
            len(self.conflicts), "" if len(self.conflicts) == 1 else "s")


def _slug(s: str) -> str:
    return _SLUG.sub("-", s).strip("-").lower()


# ---------------------------------------------------------------------------
# per-kind handlers
#
# Each takes the selected items and the mutable state documents, appends to
# `rep`, and returns nothing. None of them write to disk; run() does that once,
# at the end, and only when it is not a dry run.
# ---------------------------------------------------------------------------

def _ingest_sessions(items, src, st, led, docs, rep):
    runs = docs["runs"]
    index = {r["id"]: r for r in runs["runs"]}
    for it in items:
        p = it["payload"]
        rid = "%s:%s" % (p["vendor"], p["id"])
        status = st.record(led, "%s/%s" % (src["id"], it["key"]), src["id"],
                           "sessions", it["digest"], it["label"])
        if status == "unchanged" and rid in index:
            rep.changes.append(Change(it["key"], it["label"], "unchanged"))
            continue
        # A summary, not a copy: enough to sit in a run-history table and be
        # traced back to the tool that owns the transcript.
        index[rid] = {
            "id": rid, "source": src["id"], "vendor": p["vendor"],
            "title": p["title"] or p["project"] or p["id"],
            "project": p["project"], "cwd": p["cwd"], "model": p["model"],
            "turns": p["messages"], "tokens": p["tokens_in"] + p["tokens_out"],
            "cost_usd": round(p.get("cost_usd") or 0.0, 4),
            "touched": round(p["mtime"]), "digest": it["digest"],
            "transcript": "left in place — %s owns it" % p["vendor"],
        }
        rep.changes.append(Change(it["key"], it["label"], status,
                                  "%s · %s turns" % (p["project"] or "-", p["messages"])))
    runs["runs"] = sorted(index.values(), key=lambda r: -r["touched"])
    docs["dirty"].add("runs")


def _ingest_rules(items, src, st, led, docs, rep):
    text = docs["config"]
    for it in items:
        p = it["payload"]
        # Slug for readability, plus four hex of the path so two files whose
        # names slug identically (`a-b/R.md` and `a/b/R.md`) can never land in
        # the same block and silently swallow one another.
        tag = "rules:%s-%s" % (_slug(it["key"]), digest(it["key"])[:4])
        item_id = "%s/%s" % (src["id"], it["key"])
        prior = st.ledger_get(item_id)
        status = st.record(led, item_id, src["id"], "rules", it["digest"], it["label"])

        open_tag = BLOCK_OPEN % tag
        rev_tag = "%s@%s" % (tag, it["digest"])
        open_rev = BLOCK_OPEN % rev_tag

        if open_tag not in text:
            body = "> imported from `%s` (%s) · digest `%s`\n\n%s" % (
                it["key"], p["scope"], it["digest"], p["text"])
            text = _append_block(text, tag, "%s — %s" % (it["label"], it["key"]), body)
            rep.changes.append(Change(it["key"], it["label"], "added",
                                      "%d lines appended" % p["lines"]))
            continue

        if prior.get("digest") == it["digest"] or open_rev in text:
            rep.changes.append(Change(it["key"], it["label"], "unchanged"))
            continue

        # The file changed since it was imported. Both versions are kept: the
        # original block is left byte-for-byte alone and the new text lands in
        # its own revision block, so nothing a previous import wrote is lost.
        body = "> re-imported from `%s` after it changed. The earlier block above is kept.\n" \
               "> was `%s`, now `%s`\n\n%s" % (
                   it["key"], prior.get("digest", "?"), it["digest"], p["text"])
        text = _append_block(text, rev_tag, "%s — revision %s" % (it["label"], it["digest"]), body)
        rep.changes.append(Change(it["key"], it["label"], "updated",
                                  "changed since import; both kept"))
        rep.conflicts.append({
            "item": it["key"], "kind": "rules",
            "detail": "the file changed after it was imported (%s -> %s). "
                      "The original block is untouched and the new text was appended "
                      "as a revision." % (prior.get("digest", "?"), it["digest"]),
        })

    if text != docs["config"]:
        rep.diff = list(difflib.unified_diff(
            docs["config"].splitlines(), text.splitlines(),
            "config.md (before)", "config.md (after)", lineterm="", n=2))
        docs["config"] = text
        docs["dirty"].add("config")


def _append_block(text: str, tag: str, title: str, body: str) -> str:
    """Append one delimited block. Everything already in the file is carried
    through byte-for-byte; this function has no branch that can delete."""
    head = text if text else CONFIG_HEADER
    return head.rstrip("\n") + "\n\n" + "\n".join([
        BLOCK_OPEN % tag, "## " + title, "",
        body.rstrip("\n"), "", BLOCK_CLOSE % tag, ""])


def _ingest_registry(items, src, st, led, docs, rep, bucket, build):
    reg = docs["registry"].setdefault(bucket, {})
    for it in items:
        # Keyed by source, so two tools defining the same name never collide.
        # The collision is still worth saying out loud, so it is reported.
        key = "%s/%s" % (src["id"], it["key"])
        status = st.record(led, key, src["id"], src["kind"], it["digest"], it["label"])
        rec = build(it, src)
        rec["digest"] = it["digest"]
        rec["source"] = src["id"]
        rec["imported"] = round(time.time())
        if status == "unchanged" and key in reg:
            rep.changes.append(Change(it["key"], it["label"], "unchanged"))
            continue
        if key in reg:
            rec["imported"] = reg[key].get("imported", rec["imported"])
        reg[key] = rec
        rep.changes.append(Change(it["key"], it["label"], status, it["detail"][:70]))

        twins = [k for k in reg
                 if k != key and k.rsplit("/", 1)[-1] == it["key"]]
        for t in twins:
            if reg[t].get("digest") != it["digest"]:
                rep.conflicts.append({
                    "item": it["key"], "kind": bucket,
                    "detail": "also defined by %s with a different config. Both "
                              "are kept, under their own source." % reg[t]["source"],
                })
    docs["dirty"].add("registry")


def _mcp_record(it, src):
    p = it["payload"]
    return {"name": p["name"], "transport": p["transport"], "origin": p["origin"],
            "command": p["command"], "url": p["url"], "args": p["args"],
            # Names only. The value stays in the file the other tool owns.
            "env_keys": p["env_keys"], "header_keys": p["header_keys"],
            "auth": p["auth"], "auth_why": p["auth_why"]}


def _skill_record(it, src):
    p = it["payload"]
    return {"name": p["name"], "dir": p["dir"], "description": p["description"],
            "refs": p["refs"], "missing_refs": p["missing_refs"], "pip": p["pip"]}


def _hook_record(it, src):
    p = it["payload"]
    return {"event": p["event"], "matcher": p["matcher"], "command": p["command"],
            "installed": False,
            "note": "listed for review; Daisy does not run another tool's hooks"}


_HANDLERS = {
    "sessions": _ingest_sessions,
    "rules": _ingest_rules,
    "mcp": lambda i, s, st, l, d, r: _ingest_registry(i, s, st, l, d, r, "mcp", _mcp_record),
    "skills": lambda i, s, st, l, d, r: _ingest_registry(i, s, st, l, d, r, "skills", _skill_record),
    "hooks": lambda i, s, st, l, d, r: _ingest_registry(i, s, st, l, d, r, "hooks", _hook_record),
}


# ---------------------------------------------------------------------------

def run(source_id: str, det: dict, st: State = None, dry_run: bool = True,
        only: list = None, changed_only: bool = False) -> Report:
    """Import one explicitly named source. Writes nothing unless dry_run=False.

    `only` narrows to specific item keys; `changed_only` (used by sync) drops
    items whose digest already matches the ledger before any work is done.
    """
    st = st or State()
    src = by_id(det, source_id)
    if not src:
        raise UnknownSource(source_id)
    if not src.get("importable", True):
        raise NotImportable("%s: %s" % (source_id, src.get("note", "")))

    items = src["items"]
    if only:
        want = set(only)
        items = [i for i in items if i["key"] in want]
    if changed_only:
        items = [i for i in items
                 if st.ledger_get("%s/%s" % (source_id, i["key"])).get("digest") != i["digest"]]

    rep = Report(source_id, src["kind"], dry_run, effects=src.get("effects") or [])
    led = st.ledger()
    docs = {"runs": st.runs(), "registry": st.registry(), "config": st.config_text(),
            "dirty": set()}

    handler = _HANDLERS.get(src["kind"])
    if handler is None:
        raise NotImportable("%s: nothing to import for kind %r" % (source_id, src["kind"]))
    handler(items, src, st, led, docs, rep)

    # A dry run stops here having touched nothing but memory.
    if dry_run:
        if "runs" in docs["dirty"] and rep.tally("added") + rep.tally("updated"):
            rep.writes.append("runs.json")
        if "config" in docs["dirty"]:
            rep.writes.append("config.md")
        if "registry" in docs["dirty"] and rep.tally("added") + rep.tally("updated"):
            rep.writes.append("registry.json")
        if rep.writes:
            rep.writes.append("ledger.json")
        return rep

    if "runs" in docs["dirty"] and st.write_json("runs.json", docs["runs"]):
        rep.writes.append("runs.json")
    if "registry" in docs["dirty"] and st.write_json("registry.json", docs["registry"]):
        rep.writes.append("registry.json")
    if "config" in docs["dirty"] and st.write_config(docs["config"]):
        rep.writes.append("config.md")
    if st.write_json("ledger.json", led):
        rep.writes.append("ledger.json")
    return rep


def as_dict(rep: Report) -> dict:
    d = asdict(rep)
    d["summary"] = rep.summary()
    return d
