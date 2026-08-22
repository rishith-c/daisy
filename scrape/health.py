"""
Deciding whether a scrape that succeeded actually worked.

The failure this module exists for is not the one people instrument. When a
vendor restructures a page the collector usually does not error: it returns
200 OK, it returns rows, and the rows have fewer keys. Nothing raises. The
scrape "works" for weeks while every downstream consumer quietly loses a
column — and in this factory the downstream consumer is a physics gate that
certifies a fastener, so a missing price is not a dashboard blemish.

So health is measured against a *baseline* — the last extraction known to be
good — rather than against nothing:

    rows        did the row count collapse, or explode
    required    is every required key present in every row
    types       did a selector still match, but stop producing a value
    range       is anything outside the range the spec calls plausible
    median      did a column's centre move — the signature of a swapped column
    distinct    did a column collapse to one repeated value (a grabbed label)
    freshness   how old is the data being relied on, against the spec's TTL

    IS      a comparison against a recorded past, with the past kept on disk
    IS NOT  anomaly detection, statistics, or a model. Every threshold here is
            a number a human wrote in rules.json and can argue with

Two severities, and the difference is load-bearing. `fatal` means the data is
wrong and must not be consumed. `warn` means it is old, or unverifiable, but
not contradicted. Only fatal blocks a run; conflating the two teaches people
to ignore both.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass

from .rules import BASELINE_PATH

ANCHOR_ROWS = 12        # known-good rows kept for scrape.repair to anchor on

FATAL, WARN = "fatal", "warn"


@dataclass(frozen=True)
class Check:
    """One verdict. `observed` and `expected` are strings so the JSON reads."""
    name: str
    ok: bool
    severity: str
    observed: str
    expected: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "severity": self.severity,
                "observed": self.observed, "expected": self.expected,
                "detail": self.detail}


@dataclass
class Health:
    spec: str
    checks: list

    @property
    def failures(self) -> list:
        return [c for c in self.checks if not c.ok and c.severity == FATAL]

    @property
    def warnings(self) -> list:
        return [c for c in self.checks if not c.ok and c.severity == WARN]

    @property
    def broken(self) -> bool:
        """The only question a caller should branch on."""
        return bool(self.failures)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.warnings

    def as_dict(self) -> dict:
        return {
            "spec": self.spec,
            "broken": self.broken,
            "verdict": "broken" if self.broken else ("degraded" if self.warnings else "healthy"),
            "failed": [c.name for c in self.failures],
            "warned": [c.name for c in self.warnings],
            "checks": [c.as_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _numbers(vals) -> list:
    return [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]


def evaluate(extraction, spec, baseline: dict | None = None,
             captured_at: float | None = None, now: float | None = None) -> Health:
    """Judge one extraction. `baseline` is the record written by capture()."""
    now = now if now is not None else time.time()
    checks: list = []
    rows = extraction.rows
    complete = extraction.complete(spec.required)
    base_rows = (baseline or {}).get("rows")
    base_fields = (baseline or {}).get("fields", {})

    # -- rows ------------------------------------------------------------
    floor = spec.min_rows
    if base_rows:
        floor = max(floor, int(base_rows * (1.0 - spec.row_tolerance)))
    checks.append(Check(
        "scrape.rows", extraction.rows_matched >= floor, FATAL,
        "%d rows" % extraction.rows_matched, ">= %d" % floor,
        "row selector %r%s" % (spec.row_selector,
                               "" if base_rows is None else "; baseline %d" % base_rows)))
    if base_rows:
        ceiling = base_rows * 2 + 2
        checks.append(Check(
            "scrape.rows.growth", extraction.rows_matched <= ceiling, WARN,
            "%d rows" % extraction.rows_matched, "<= %d" % ceiling,
            "a sudden jump usually means the selector widened, not that the catalogue did"))
    else:
        checks.append(Check(
            "scrape.baseline", False, WARN, "no baseline recorded",
            "a previous good extraction",
            "nothing to compare against; run fetch --save-baseline once the rows look right"))

    # -- required keys ---------------------------------------------------
    # The headline check. Rows still arrive; they just stop carrying a column.
    short = {}
    for name in spec.required:
        have = sum(1 for r in rows if name in r)
        if have < len(rows):
            short[name] = len(rows) - have
    checks.append(Check(
        "scrape.required", not short and bool(rows), FATAL,
        "%d/%d rows complete" % (len(complete), len(rows)),
        "%d/%d" % (len(rows), len(rows)) if rows else ">= 1 row",
        ("missing: " + ", ".join("%s in %d row(s)" % (k, v) for k, v in sorted(short.items()))
         if short else "the row selector matched nothing, so there is no key to be missing"
         if not rows else "every required field present in every row")))

    # -- types -----------------------------------------------------------
    # Distinguishes "the selector is gone" from "the selector still matches a
    # node that no longer holds a number". Those need different repairs.
    lossy = [r for r in extraction.fields if r.matched and not r.clean]
    checks.append(Check(
        "scrape.types", not lossy, FATAL,
        "%d field(s) matched but did not convert" % len(lossy), "0",
        "; ".join("%s: %d/%d converted, saw %r"
                  % (r.name, r.typed, r.matched, r.sample) for r in lossy)
        or "every matched node converted to its declared type"))

    # -- declared ranges --------------------------------------------------
    out = []
    for f in spec.fields:
        if f.min is None and f.max is None:
            continue
        for r in rows:
            v = r.get(f.name)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if (f.min is not None and v < f.min) or (f.max is not None and v > f.max):
                out.append("%s=%g outside [%s, %s]" % (f.name, v, f.min, f.max))
    checks.append(Check(
        "scrape.range", not out, FATAL,
        "%d value(s) out of range" % len(out), "0", "; ".join(out[:4])
        or "every value inside the range rules.json calls plausible"))

    # -- median shift -----------------------------------------------------
    # Catches the case selectors alone cannot: the right shape, the wrong
    # column. Prices that became tensile figures still parse as numbers.
    moved = []
    for f in spec.fields:
        base = base_fields.get(f.name, {})
        if "median" not in base:
            continue
        nums = _numbers([r.get(f.name) for r in rows if f.name in r])
        if not nums:
            continue
        cur, was = statistics.median(nums), base["median"]
        if was == 0 or cur == 0:
            shift = 1.0 if cur == was else float("inf")
        else:
            shift = max(cur, was) / min(cur, was)
        if shift > spec.median_shift_max:
            moved.append("%s median %g -> %g (%.1fx)" % (f.name, was, cur, shift))
    checks.append(Check(
        "scrape.median", not moved, FATAL,
        "%d column(s) shifted" % len(moved), "<= %gx" % spec.median_shift_max,
        "; ".join(moved) or "every numeric column sits where the baseline left it"))

    # -- collapsed columns -------------------------------------------------
    flat = []
    for f in spec.fields:
        base = base_fields.get(f.name, {})
        vals = [r[f.name] for r in rows if f.name in r]
        if base.get("distinct", 0) > 1 and len(vals) > 1 and len(set(vals)) == 1:
            flat.append("%s is %r in all %d rows (baseline had %d distinct)"
                        % (f.name, vals[0], len(vals), base["distinct"]))
    checks.append(Check(
        "scrape.distinct", not flat, FATAL,
        "%d column(s) collapsed" % len(flat), "0", "; ".join(flat)
        or "no column collapsed to a single repeated value"))

    # -- freshness ---------------------------------------------------------
    if captured_at is not None:
        age_h = max(0.0, (now - captured_at) / 3600.0)
        checks.append(Check(
            "scrape.freshness", age_h <= spec.ttl_hours, WARN,
            "%.1f h old" % age_h, "<= %.1f h" % spec.ttl_hours,
            "data this old may be right; it is simply no longer evidence"))

    return Health(spec.name, checks)


# ---------------------------------------------------------------------------
# baselines — the recorded past, and the anchors repair works from
# ---------------------------------------------------------------------------

def capture(extraction, spec, source: str, now: float | None = None) -> dict:
    """Freeze one good extraction as the thing future runs are judged against.

    `anchors` is the part scrape.repair needs: real field values that were
    true the last time the scraper worked. Selectors can be re-derived from
    those; they cannot be re-derived from a row count.
    """
    now = now if now is not None else time.time()
    rows = extraction.complete(spec.required)
    fields = {}
    for f in spec.fields:
        vals = [r[f.name] for r in rows if f.name in r]
        entry = {"present": len(vals), "distinct": len(set(vals))}
        nums = _numbers(vals)
        if nums:
            entry["min"] = min(nums)
            entry["max"] = max(nums)
            entry["median"] = statistics.median(nums)
        fields[f.name] = entry
    return {
        "captured": now,
        "captured_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "rules_version": spec.version,
        "source": source,
        "rows": extraction.rows_matched,
        "complete": len(rows),
        "fields": fields,
        "anchors": rows[:ANCHOR_ROWS],
    }


def load(path: str = BASELINE_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(data: dict, path: str = BASELINE_PATH) -> str:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def record(entry: dict, name: str, path: str = BASELINE_PATH) -> str:
    data = load(path)
    data[name] = entry
    return save(data, path)
