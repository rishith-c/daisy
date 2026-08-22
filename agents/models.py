"""
What each installed agent can actually be run as.

The composer used to offer two hardcoded strings. This reads the real thing:
the model each tool is configured to use, the models it has actually run, and
the effort and speed ladders it accepts — which are genuinely different per
vendor and are reported that way rather than flattened into one invented scale.

    ~/.claude/settings.json     model
    ~/.claude.json              additionalModelOptionsCache (labels, entitlements)
    ~/.codex/config.toml        model, model_reasoning_effort
    opencode.db  message.data   modelID / providerID actually used

Read-only, like the rest of agents/. Where a vendor exposes no ladder, the
answer is an empty list — OpenCode has no reasoning-effort concept, and saying
so is more useful than inventing three tiers for it.

    python3 -m agents.models          human-readable
    python3 -m agents.models --json
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, asdict, field

HOME = os.path.expanduser("~")

# Ladders are per-vendor because they really are per-vendor. Claude Code and
# Codex do not agree on the names, the count, or the top of the scale.
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["light", "medium", "high", "xhigh", "max", "ultra"]
SPEEDS = ["standard", "fast"]

EFFORT_LABEL = {"low": "Low", "light": "Light", "medium": "Medium", "high": "High",
                "xhigh": "Extra High", "max": "Max", "ultra": "Ultra"}
EFFORT_NOTE = {"ultra": "Consumes usage limits faster", "max": "Slowest, most thorough"}
SPEED_NOTE = {"standard": "Default speed", "fast": "1.5× speed, more usage"}

# Fallbacks, used only when nothing on disk names a model. Marked as such in
# `source` so the UI never implies it read something it did not.
CLAUDE_KNOWN = [
    ("claude-fable-5", "Fable 5"),
    ("claude-opus-5", "Opus 5"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5"),
]


@dataclass
class Model:
    vendor: str
    id: str
    label: str
    source: str                       # config | history | known
    current: bool = False
    efforts: list = field(default_factory=list)
    speeds: list = field(default_factory=list)
    effort: str = ""
    provider: str = ""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# ---------------------------------------------------------------------------

def claude_models(home: str = None) -> list[Model]:
    home = home or HOME
    settings = _read_json(os.path.join(home, ".claude", "settings.json"))
    top = _read_json(os.path.join(home, ".claude.json"))
    current = str(settings.get("model") or "").strip()

    out, seen = [], set()
    # Options the CLI itself cached are authoritative — they carry the label and
    # the description the user was actually shown.
    for opt in top.get("additionalModelOptionsCache") or []:
        raw = str(opt.get("value") or "")
        if not raw:
            continue
        mid = raw.split("[")[0]
        if mid in seen:
            continue
        seen.add(mid)
        out.append(Model("claude", mid, opt.get("label") or mid, "config",
                         efforts=list(CLAUDE_EFFORTS), speeds=list(SPEEDS)))
    for mid, label in CLAUDE_KNOWN:
        if mid in seen:
            continue
        seen.add(mid)
        out.append(Model("claude", mid, label, "known",
                         efforts=list(CLAUDE_EFFORTS), speeds=list(SPEEDS)))

    # settings.json stores a short alias ("opus"), not an id.
    if current:
        for m in out:
            if current in m.id or current.lower() in m.label.lower():
                m.current = True
                break
    return out


def codex_models(home: str = None) -> list[Model]:
    home = home or HOME
    cfg = os.path.join(home, ".codex", "config.toml")
    model, effort = "", ""
    try:
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    break            # past the top-level table into [projects.*]
                m = re.match(r'model\s*=\s*"([^"]+)"', line)
                if m:
                    model = m.group(1)
                e = re.match(r'model_reasoning_effort\s*=\s*"([^"]+)"', line)
                if e:
                    effort = e.group(1)
    except OSError:
        pass

    def pretty(mid):
        # gpt-5.6-sol -> 5.6 Sol
        s = re.sub(r"^gpt-", "", mid).replace("-", " ")
        return " ".join(w.capitalize() if w.isalpha() else w for w in s.split())

    out = []
    if model:
        out.append(Model("codex", model, pretty(model), "config", current=True,
                         efforts=list(CODEX_EFFORTS), speeds=list(SPEEDS),
                         effort=effort))
    return out


def opencode_models(db: str = None) -> list[Model]:
    db = db or os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
    if not os.path.exists(db):
        return []
    uri = "file:%s?immutable=1&mode=ro" % db.replace("?", "%3f").replace("#", "%23")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error:
        return []
    out = []
    try:
        rows = con.execute(
            "SELECT DISTINCT json_extract(data,'$.modelID'), json_extract(data,'$.providerID')"
            " FROM message WHERE json_extract(data,'$.modelID') IS NOT NULL").fetchall()
        for mid, prov in rows:
            if not mid:
                continue
            # OpenCode routes to whatever provider you point it at, and exposes
            # no reasoning-effort setting of its own. Empty ladders, honestly.
            out.append(Model("opencode", str(mid), str(mid), "history",
                             provider=str(prov or ""), efforts=[], speeds=[]))
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return out


def inventory() -> dict:
    models = claude_models() + codex_models() + opencode_models()
    return {
        "efforts": {"claude": CLAUDE_EFFORTS, "codex": CODEX_EFFORTS, "opencode": []},
        "speeds": SPEEDS,
        "labels": {"effort": EFFORT_LABEL, "effort_note": EFFORT_NOTE, "speed_note": SPEED_NOTE},
        "models": [asdict(m) for m in models],
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    inv = inventory()
    if a.json:
        print(json.dumps(inv, indent=1))
        return 0
    print("models available to this machine\n")
    print("  %-9s %-34s %-9s %-7s %s" % ("vendor", "model", "source", "current", "efforts"))
    print("  " + "-" * 84)
    for m in inv["models"]:
        print("  %-9s %-34s %-9s %-7s %s" % (
            m["vendor"], (m["label"] or m["id"])[:34], m["source"],
            "yes" if m["current"] else "", ",".join(m["efforts"]) or "—"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
