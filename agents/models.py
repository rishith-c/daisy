"""
What each installed agent can actually be run as.

The composer used to offer two hardcoded strings. This reads the real thing:
the model each tool is configured to use, the models it has actually run, and
the effort and speed ladders it accepts — which are genuinely different per
vendor and are reported that way rather than flattened into one invented scale.

    ~/.claude/settings.json     model
    ~/.claude.json              additionalModelOptionsCache (labels, entitlements)
    ~/.claude/projects/**/*.jsonl  model ids actually used
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
from glob import glob

HOME = os.path.expanduser("~")

# Ladders are per-vendor because they really are per-vendor. Claude Code and
# Codex do not agree on the names, the count, or the top of the scale.
CLAUDE_EFFORTS = []
CODEX_EFFORTS = ["light", "medium", "high", "xhigh", "max", "ultra"]
SPEEDS = ["standard", "fast"]

EFFORT_LABEL = {"low": "Low", "light": "Light", "medium": "Medium", "high": "High",
                "xhigh": "Extra High", "max": "Max", "ultra": "Ultra"}
EFFORT_NOTE = {"ultra": "Consumes usage limits faster", "max": "Slowest, most thorough"}
SPEED_NOTE = {"standard": "Default speed", "fast": "1.5× speed, more usage"}

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
                         efforts=[], speeds=[]))
    # Claude's option cache is ephemeral and the CLI may clear it after a run.
    # Session history is durable evidence that a model was genuinely used on
    # this Mac. The live model probe still decides whether it may join a Chain.
    paths = glob(os.path.join(home, ".claude", "projects", "**", "*.jsonl"),
                 recursive=True)
    paths.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    for path in paths[:256]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                sample = fh.read(262144)
        except OSError:
            continue
        for mid in re.findall(r'"model"\s*:\s*"(claude-[A-Za-z0-9._-]+)"', sample):
            if mid in seen:
                continue
            seen.add(mid)
            label = mid.removeprefix("claude-").replace("-", " ").title()
            out.append(Model("claude", mid, label, "history", efforts=[], speeds=[]))
    # settings.json stores a short alias ("opus"), not an id.
    if current:
        matched = False
        for m in out:
            if current in m.id or current.lower() in m.label.lower():
                m.current = True
                matched = True
                break
        if not matched:
            out.append(Model("claude", current, current.replace("-", " ").title(),
                             "config", current=True,
                             efforts=[], speeds=[]))
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

    out, seen = [], set()
    cache = _read_json(os.path.join(home, ".codex", "models_cache.json"))
    for row in cache.get("models") or []:
        mid = str(row.get("slug") or "").strip()
        if not mid or mid in seen or row.get("visibility") != "list":
            continue
        seen.add(mid)
        efforts = [str(item.get("effort")) for item in
                   (row.get("supported_reasoning_levels") or [])
                   if item.get("effort")]
        speeds = ["standard"]
        if "fast" in (row.get("additional_speed_tiers") or []):
            speeds.append("fast")
        current = mid == model
        out.append(Model("codex", mid, row.get("display_name") or pretty(mid),
                         "config" if current else "cache", current=current,
                         efforts=efforts or list(CODEX_EFFORTS), speeds=speeds,
                         effort=effort if current else ""))
    if model and model not in seen:
        out.insert(0, Model("codex", model, pretty(model), "config", current=True,
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
