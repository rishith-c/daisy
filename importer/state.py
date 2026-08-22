"""
Where Daisy keeps what it imported, and the ledger that makes a second import
a no-op.

Five files, all JSON or Markdown, all readable without Daisy:

    ledger.json    every item ever imported — id, source, digest, when
    runs.json      sessions from other tools, summarised into run history
    registry.json  skills and MCP servers, registered by name
    config.md      Daisy's project config, with rules from other tools merged in
    sync.json      the autosync toggle and one watermark per source

The ledger is the only reason a second import is safe. It stores a content
digest per item, so the second run compares digests and writes nothing — rather
than re-appending the same rules block or re-summarising the same session under
a fresh id.

Two rules hold everywhere below, and the tests exist to prove them:

    idempotent      same input twice -> the second run changes no bytes
    non-destructive nothing already in Daisy's config is overwritten. On a
                    collision both versions are kept and the conflict is
                    reported, because silently picking a winner is how an
                    import loses work someone wanted.

IS NOT a database, a migration system, or a lock. Two importers writing this
directory at the same instant can interleave; the fix is a lock file and it is
deliberately absent, because this runs when a person presses a button, not on a
scheduler.

Zero third-party dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The state directory is per-machine, so it is gitignored rather than committed.
DEFAULT_HOME = os.environ.get("DAISY_IMPORT_HOME") or os.path.join(ROOT, "importer", "state")

LEDGER, RUNS, REGISTRY, CONFIG, SYNC = (
    "ledger.json", "runs.json", "registry.json", "config.md", "sync.json")

# Daisy owns the text between these markers and nothing else in config.md. A
# hand-written line above the first marker survives every import forever.
BLOCK_OPEN = "<!-- daisy:import %s -->"
BLOCK_CLOSE = "<!-- /daisy:import %s -->"

CONFIG_HEADER = """# Daisy project config

Everything below this line arrived through Import. Each block is delimited and
carries the source it came from and a digest of the text; Daisy only ever
appends. Anything you write outside a block is never touched.
"""


def digest(*parts) -> str:
    """Short content hash. 12 hex chars — collision risk is irrelevant at the
    scale of one laptop's config, and a short id is readable in a report."""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


class State:
    """The import state directory. Reads are lazy, writes are explicit."""

    def __init__(self, home: str = None):
        self.home = home or DEFAULT_HOME

    # -- paths ------------------------------------------------------------
    def path(self, name: str) -> str:
        return os.path.join(self.home, name)

    def exists(self) -> bool:
        return os.path.isdir(self.home)

    def _ensure(self) -> None:
        os.makedirs(self.home, exist_ok=True)

    # -- generic json -----------------------------------------------------
    def read_json(self, name: str, default):
        try:
            with open(self.path(name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def write_json(self, name: str, obj) -> bool:
        """Write only if the bytes would change. Returns True if it wrote.

        The comparison is what makes `import` twice produce an untouched mtime,
        which is a stronger claim than "the contents are equal" and is the one
        the idempotency test asserts."""
        blob = json.dumps(obj, indent=1, sort_keys=True) + "\n"
        p = self.path(name)
        try:
            if open(p, encoding="utf-8").read() == blob:
                return False
        except OSError:
            pass
        self._ensure()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(blob)
        return True

    # -- ledger -----------------------------------------------------------
    def ledger(self) -> dict:
        return self.read_json(LEDGER, {"version": 1, "items": {}})

    def ledger_get(self, item_id: str) -> dict:
        return (self.ledger().get("items") or {}).get(item_id) or {}

    def record(self, led: dict, item_id: str, source: str, kind: str,
               dg: str, label: str) -> str:
        """Stamp one item into the ledger. Returns added | updated | unchanged."""
        items = led.setdefault("items", {})
        prev = items.get(item_id)
        now = time.time()
        if prev is None:
            items[item_id] = {"source": source, "kind": kind, "digest": dg,
                              "label": label, "first": now, "last": now,
                              "revisions": 0}
            return "added"
        if prev.get("digest") == dg:
            return "unchanged"
        # The previous digest is kept, not discarded: "this rules file changed
        # under us" is exactly the thing a resync needs to be able to say.
        prev.setdefault("history", []).append(prev.get("digest"))
        prev["digest"] = dg
        prev["label"] = label
        prev["last"] = now
        prev["revisions"] = prev.get("revisions", 0) + 1
        return "updated"

    # -- run history ------------------------------------------------------
    def runs(self) -> dict:
        return self.read_json(RUNS, {"version": 1, "runs": []})

    # -- registry ---------------------------------------------------------
    def registry(self) -> dict:
        return self.read_json(REGISTRY, {"version": 1, "skills": {}, "mcp": {}})

    # -- sync -------------------------------------------------------------
    def sync(self) -> dict:
        return self.read_json(SYNC, {"version": 1, "enabled": False,
                                     "last_run": None, "last_result": None,
                                     "watermarks": {}})

    # -- config.md --------------------------------------------------------
    def config_text(self) -> str:
        try:
            return open(self.path(CONFIG), encoding="utf-8").read()
        except OSError:
            return ""

    def write_config(self, text: str) -> bool:
        if self.config_text() == text:
            return False
        self._ensure()
        with open(self.path(CONFIG), "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
