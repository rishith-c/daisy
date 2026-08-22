"""
The scraper as data, not as a command.

A scrape written as code is a one-off: it lives in someone's shell history, it
cannot be diffed, and when the vendor restructures their page the only record
of what used to work is in the head of whoever wrote it. Everything that makes
this collector specific to one page — the URL, the row container, each field's
selector, its type, its plausible range, how stale its data may get — is in
rules.json instead, under version control, reviewable in a pull request.

That is not tidiness for its own sake. scrape.repair rewrites selectors
automatically, and an automatic rewrite you cannot read as a diff is an
automatic rewrite you cannot trust. Config-as-data is what makes the repair
auditable, and it is why every spec carries its own `version` and a `history`
of what changed and why.

    IS      declarative config, loaded, type-checked, and round-trippable
    IS NOT  a DSL. There is no branching, no expression language, and nothing
            here executes. A rules file cannot do anything but describe a page

The field names in the shipped spec are a contract, not a suggestion: they are
exactly the keys hardware.margins.select_fastener reads. Rename one here and
the physics gate stops certifying.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace

from .extract import TYPES, parse_selector, SelectorError

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")
PROPOSAL_PATH = os.path.join(HERE, "rules.proposed.json")
BASELINE_PATH = os.path.join(HERE, "baseline.json")
LAST_GOOD_PATH = os.path.join(HERE, "last_good.json")
FIXTURES = os.path.join(HERE, "fixtures")

HISTORY_KEEP = 10       # a spec's lineage, not its entire archaeology


class RulesError(ValueError):
    """A rules file that cannot be trusted to run. Always fatal at load."""


@dataclass(frozen=True)
class Field:
    """One column: where it lives, what it is, and what counts as absurd."""
    name: str
    selector: str
    type: str = "str"
    attr: str | None = None       # read this attribute instead of the text
    pattern: str | None = None    # optional regex; group 1 wins if present
    required: bool = True
    min: float | None = None
    max: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Spec:
    """One page, one row shape. The whole scraper, as a value."""
    name: str
    url: str
    row_selector: str
    fields: tuple
    fixture: str = ""
    ttl_hours: float = 24.0
    min_rows: int = 1
    row_tolerance: float = 0.25       # fractional drop from baseline that is fine
    median_shift_max: float = 3.0     # a column silently swapped moves its median
    version: int = 1
    updated: str = ""
    notes: str = ""
    history: tuple = ()

    def field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def required(self) -> tuple:
        return tuple(f.name for f in self.fields if f.required)

    @property
    def field_names(self) -> tuple:
        return tuple(f.name for f in self.fields)

    def with_field(self, rule: Field) -> "Spec":
        """A copy with one field replaced, order preserved."""
        return replace(self, fields=tuple(rule if f.name == rule.name else f
                                          for f in self.fields))

    def fixture_path(self, override: str | None = None) -> str:
        name = override or self.fixture
        if not name:
            return ""
        return name if os.path.isabs(name) else os.path.join(FIXTURES, name)


# ---------------------------------------------------------------------------
# serialisation — round-trip exact, because repair diffs the two forms
# ---------------------------------------------------------------------------

_FIELD_OPT = ("attr", "pattern", "min", "max", "note")


def field_to_dict(f: Field) -> dict:
    d = {"name": f.name, "selector": f.selector, "type": f.type,
         "required": f.required}
    for k in _FIELD_OPT:
        v = getattr(f, k)
        if v not in (None, ""):
            d[k] = v
    return d


def field_from_dict(d: dict, where: str) -> Field:
    if not isinstance(d, dict):
        raise RulesError("%s: each field must be an object" % where)
    for k in ("name", "selector"):
        if not d.get(k):
            raise RulesError("%s: field is missing %r" % (where, k))
    kind = d.get("type", "str")
    if kind not in TYPES:
        raise RulesError("%s.%s: type %r is not one of %s"
                         % (where, d["name"], kind, ", ".join(TYPES)))
    try:
        parse_selector(d["selector"])
    except SelectorError as e:
        raise RulesError("%s.%s: %s" % (where, d["name"], e)) from None
    return Field(name=d["name"], selector=d["selector"], type=kind,
                 attr=d.get("attr"), pattern=d.get("pattern"),
                 required=bool(d.get("required", True)),
                 min=d.get("min"), max=d.get("max"), note=d.get("note", ""))


def spec_to_dict(s: Spec) -> dict:
    return {
        "name": s.name, "version": s.version, "updated": s.updated,
        "url": s.url, "fixture": s.fixture,
        "row_selector": s.row_selector,
        "ttl_hours": s.ttl_hours, "min_rows": s.min_rows,
        "row_tolerance": s.row_tolerance, "median_shift_max": s.median_shift_max,
        "notes": s.notes,
        "fields": [field_to_dict(f) for f in s.fields],
        "history": [dict(h) for h in s.history],
    }


def spec_from_dict(d: dict) -> Spec:
    name = d.get("name") or "<unnamed>"
    for k in ("url", "row_selector", "fields"):
        if not d.get(k):
            raise RulesError("%s: spec is missing %r" % (name, k))
    try:
        parse_selector(d["row_selector"])
    except SelectorError as e:
        raise RulesError("%s.row_selector: %s" % (name, e)) from None
    fields = tuple(field_from_dict(f, name) for f in d["fields"])
    seen = [f.name for f in fields]
    if len(set(seen)) != len(seen):
        raise RulesError("%s: duplicate field names %s" % (name, seen))
    if not any(f.required for f in fields):
        raise RulesError("%s: no required fields — nothing could ever fail" % name)
    return Spec(
        name=name, url=d["url"], row_selector=d["row_selector"], fields=fields,
        fixture=d.get("fixture", ""),
        ttl_hours=float(d.get("ttl_hours", 24.0)),
        min_rows=int(d.get("min_rows", 1)),
        row_tolerance=float(d.get("row_tolerance", 0.25)),
        median_shift_max=float(d.get("median_shift_max", 3.0)),
        version=int(d.get("version", 1)),
        updated=d.get("updated", ""), notes=d.get("notes", ""),
        history=tuple(d.get("history", ())),
    )


def load(path: str = RULES_PATH) -> dict:
    """Every spec in the file, keyed by name. Raises rather than half-loading."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise RulesError("no rules file at %s" % path) from None
    except json.JSONDecodeError as e:
        raise RulesError("%s is not valid JSON: %s" % (path, e)) from None
    if not isinstance(doc.get("specs"), list) or not doc["specs"]:
        raise RulesError("%s has no 'specs' list" % path)
    out = {}
    for d in doc["specs"]:
        s = spec_from_dict(d)
        if s.name in out:
            raise RulesError("duplicate spec name %r" % s.name)
        out[s.name] = s
    return out


def save(specs: dict, path: str = RULES_PATH) -> str:
    """Write the whole file atomically. A half-written rules.json is a scraper
    that fails on the next run for a reason nobody will guess."""
    doc = {
        "note": "Version-controlled scraper configuration. See scrape/rules.py.",
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "specs": [spec_to_dict(s) for s in specs.values()],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def bump(spec: Spec, reason: str, changes: list, by: str = "scrape.repair") -> Spec:
    """Next version of a spec, with the reason recorded in the file itself.

    The history entry is the audit trail: it survives in git alongside the
    selector it explains, so a reviewer six months later can see that a
    selector changed *because* the vendor restructured, not because someone
    was experimenting.
    """
    entry = {
        "version": spec.version + 1,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by": by,
        "reason": reason,
        "changes": list(changes),
    }
    hist = (tuple(spec.history) + (entry,))[-HISTORY_KEEP:]
    return replace(spec, version=spec.version + 1, history=hist,
                   updated=entry["at"])
