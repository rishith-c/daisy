"""
The Scraper Studio, from a terminal. JSON in, JSON out, honest about both.

    python3 -m scrape.cli fetch    [--fixture F] [--save-baseline]
    python3 -m scrape.cli check    [--fixture F]
    python3 -m scrape.cli repair   [--fixture F] [--accept]
    python3 -m scrape.cli status

Credentials come from the environment and are never read from a file, never
echoed, and never written into any output this tool produces:

    BRIGHT_DATA_API_TOKEN    Bright Data API token (Scraper Studio)
    BRIGHT_DATA_COLLECTOR_ID published Scraper Studio collector, c_...
    BRIGHTDATA_API_KEY   Bright Data API token
    BRD_ZONE             the zone to route through
    BRD_CUSTOMER         set as well to go through the residential super-proxy
                         instead of the Web Unlocker API endpoint

With none of them set, every command runs against the fixtures in
scrape/fixtures/ so the whole break-detect-repair loop demonstrates on a
laptop with the wifi off. That fallback is the single most dangerous thing in
this file, because a fixture run that *looks* like a live run is a lie a demo
tells the room. So every command reports a `source` block naming the mode, the
exact path or URL used, and why that mode was chosen — and a live fetch that
fails is an error, never a quiet slide back to the fixture.

The one thing this tool will not do is edit rules.json on its own. `repair`
verifies a proposal and writes it next to the rules; promoting it takes a
second command with --accept. An unreviewed automatic rewrite of the config
that decides which bolt goes in a bracket is not a feature.

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import health as _health
from . import repair as _repair
from . import rules as _rules
from .extract import run as _extract
from .rules import (BASELINE_PATH, LAST_GOOD_PATH, PROPOSAL_PATH, RULES_PATH,
                    RulesError, spec_to_dict)

API = "https://api.brightdata.com/request"
STUDIO_TRIGGER = "https://api.brightdata.com/dca/trigger"
STUDIO_DATASET = "https://api.brightdata.com/dca/dataset"
PROXY_HOST = "brd.superproxy.io:33335"
UA = "daisy-scrape/1.0 (+stdlib urllib)"
TIMEOUT = 45


@dataclass
class Source:
    """Where the HTML came from. Present in every command's output."""
    mode: str                 # live-api | live-proxy | fixture
    location: str
    reason: str
    fetched_at: float = 0.0
    snapshot_id: str = ""

    @property
    def live(self) -> bool:
        return self.mode.startswith("live")

    @property
    def label(self) -> str:
        """Short provenance for files that get committed. Absolute paths in a
        baseline make it machine-specific for no gain."""
        if self.mode == "fixture":
            return "fixture:%s" % os.path.basename(self.location)
        return "%s:%s" % (self.mode, self.location)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "live": self.live, "location": self.location,
                "reason": self.reason,
                "fetched_at": round(self.fetched_at, 3) if self.fetched_at else None,
                "snapshot_id": self.snapshot_id or None}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def resolve(spec, fixture: str | None) -> Source:
    key, zone, cust = _env("BRIGHTDATA_API_KEY"), _env("BRD_ZONE"), _env("BRD_CUSTOMER")
    studio_key = _env("BRIGHT_DATA_API_TOKEN") or key
    collector = _env("BRIGHT_DATA_COLLECTOR_ID")
    if fixture:
        return Source("fixture", spec.fixture_path(fixture),
                      "--fixture was given; no network call was made")
    if studio_key and collector:
        token_name = "BRIGHT_DATA_API_TOKEN" if _env("BRIGHT_DATA_API_TOKEN") else "BRIGHTDATA_API_KEY"
        return Source("live-studio", "collector:" + collector,
                      "%s and BRIGHT_DATA_COLLECTOR_ID are set" % token_name)
    if key and zone:
        if cust:
            return Source("live-proxy", spec.url,
                          "BRIGHTDATA_API_KEY, BRD_ZONE and BRD_CUSTOMER are set")
        return Source("live-api", spec.url, "BRIGHTDATA_API_KEY and BRD_ZONE are set")
    missing = [n for n, v in (("BRIGHTDATA_API_KEY", key), ("BRD_ZONE", zone)) if not v]
    return Source("fixture", spec.fixture_path(),
                  "%s not set, so the fixture was used and nothing was fetched"
                  % " and ".join(missing))


def load_html(src: Source, spec) -> str:
    if src.mode == "fixture":
        if not src.location or not os.path.exists(src.location):
            raise SystemExit(_fail("no fixture at %s" % (src.location or "(unset)"), 2))
        with open(src.location, "r", encoding="utf-8") as fh:
            return fh.read()
    if src.mode == "live-studio":
        return _studio_html(src, spec)
    key, zone, cust = _env("BRIGHTDATA_API_KEY"), _env("BRD_ZONE"), _env("BRD_CUSTOMER")
    if src.mode == "live-api":
        body = json.dumps({"zone": zone, "url": spec.url, "format": "raw"}).encode()
        req = urllib.request.Request(API, data=body, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    user = "brd-customer-%s-zone-%s" % (cust, zone)
    proxy = "http://%s:%s@%s" % (urllib.parse.quote(user, safe=""),
                                 urllib.parse.quote(key, safe=""), PROXY_HOST)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    with opener.open(urllib.request.Request(spec.url, headers={"User-Agent": UA}),
                     timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _json_request(url: str, key: str, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _studio_html(src: Source, spec) -> str:
    """Run a published Scraper Studio collector and adapt its typed rows.

    The published collector's output schema is the same five-field contract
    consumed by hardware.margins.select_fastener. Rendering those rows through
    the existing HTML extractor keeps range, type, baseline, and drift gates in
    one path instead of granting Studio results a less strict shortcut.
    """
    key = _env("BRIGHT_DATA_API_TOKEN") or _env("BRIGHTDATA_API_KEY")
    collector = _env("BRIGHT_DATA_COLLECTOR_ID")
    query = urllib.parse.urlencode({"collector": collector, "queue_next": 1})
    trigger = _json_request(
        STUDIO_TRIGGER + "?" + query, key,
        json.dumps([{"url": spec.url}]).encode("utf-8"),
    )
    snapshot = trigger.get("collection_id") if isinstance(trigger, dict) else ""
    if not snapshot:
        raise OSError("Scraper Studio trigger returned no collection_id")
    src.snapshot_id = str(snapshot)

    dataset_url = STUDIO_DATASET + "?" + urllib.parse.urlencode({"id": snapshot})
    rows = None
    for attempt in range(24):
        body = _json_request(dataset_url, key)
        if isinstance(body, list):
            rows = body
            break
        if isinstance(body, dict) and body.get("status") not in (None, "building", "running"):
            raise OSError("Scraper Studio collection %s: %s" %
                          (snapshot, body.get("status")))
        if attempt < 23:
            time.sleep(5)
    if rows is None:
        raise OSError("Scraper Studio collection %s was not ready after 120 seconds" % snapshot)

    cells = (("grade", "col-grade"), ("dia_mm", "col-thread"),
             ("tensile_mpa", "col-tensile"), ("price_usd", "col-price"),
             ("in_stock", "col-stock"))
    rendered = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        tds = []
        for field, cls in cells:
            value = row.get(field, "")
            if isinstance(value, bool):
                value = "yes" if value else "no"
            tds.append('<td class="%s">%s</td>' %
                       (cls, _html.escape(str(value), quote=True)))
        rendered.append('<tr class="part-row">%s</tr>' % "".join(tds))
    return '<table class="parts-table"><tbody>%s</tbody></table>' % "".join(rendered)


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def _emit(payload: dict, code: int = 0) -> int:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return code


def _fail(msg: str, code: int = 1) -> int:
    return _emit({"ok": False, "error": msg}, code)


def _spec(a):
    specs = _rules.load(a.rules)
    if a.spec:
        if a.spec not in specs:
            raise SystemExit(_fail("no spec named %r in %s (have: %s)"
                                   % (a.spec, a.rules, ", ".join(specs)), 2))
        return specs, specs[a.spec]
    return specs, specs[next(iter(specs))]


def _pull(a):
    """Resolve the source, fetch, extract. Shared by fetch / check / repair."""
    specs, spec = _spec(a)
    src = resolve(spec, a.fixture)
    try:
        html = load_html(src, spec)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        raise SystemExit(_emit({
            "ok": False, "source": src.as_dict(),
            "error": "%s fetch failed: %s" % (src.mode, e),
            "note": "no fixture was substituted — a failed live fetch is not a scrape",
        }, 1))
    src.fetched_at = time.time()
    return specs, spec, src, html, _extract(spec, html)


def _rows_payload(ex, spec) -> dict:
    return {
        "rows": ex.complete(spec.required),
        "rows_matched": ex.rows_matched,
        "rows_complete": len(ex.complete(spec.required)),
        "fields": [{"field": r.name, "selector": r.selector, "attr": r.attr,
                    "matched": r.matched, "typed": r.typed,
                    "multiple_matches": r.multiple, "sample": r.sample}
                   for r in ex.fields],
        "gaps": ex.gaps[:10],
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_fetch(a) -> int:
    specs, spec, src, html, ex = _pull(a)
    base = _health.load(a.baseline).get(spec.name)
    hl = _health.evaluate(ex, spec, base, captured_at=src.fetched_at)
    out = {"ok": not hl.broken, "command": "fetch", "spec": spec.name,
           "rules_version": spec.version, "source": src.as_dict()}
    out.update(_rows_payload(ex, spec))
    out["health"] = hl.as_dict()

    if a.save_baseline:
        entry = _health.capture(ex, spec, src.label)
        if hl.broken:
            out["baseline"] = {"saved": False,
                               "why": "refused: a broken extraction is not a baseline"}
        else:
            _health.record(entry, spec.name, a.baseline)
            out["baseline"] = {"saved": True, "path": a.baseline,
                               "rows": entry["rows"], "anchors": len(entry["anchors"])}
    if not hl.broken:
        _write_last_good(a.last_good, spec, src, ex)
        out["cached"] = a.last_good
    return _emit(out, 0 if not hl.broken else 1)


def cmd_check(a) -> int:
    specs, spec, src, html, ex = _pull(a)
    base = _health.load(a.baseline).get(spec.name)
    hl = _health.evaluate(ex, spec, base, captured_at=src.fetched_at)
    out = {"ok": not hl.broken, "command": "check", "spec": spec.name,
           "rules_version": spec.version, "source": src.as_dict(),
           "rows_matched": ex.rows_matched,
           "rows_complete": len(ex.complete(spec.required)),
           "health": hl.as_dict()}
    if hl.broken:
        out["next"] = "python3 -m scrape.cli repair" + (
            " --fixture %s" % a.fixture if a.fixture else "")
    return _emit(out, 0 if not hl.broken else 1)


def cmd_repair(a) -> int:
    specs, spec, src, html, ex = _pull(a)
    base = _health.load(a.baseline).get(spec.name)
    hl = _health.evaluate(ex, spec, base, captured_at=src.fetched_at)
    head = {"command": "repair", "spec": spec.name, "rules_version": spec.version,
            "source": src.as_dict(), "was": hl.as_dict()}
    if not hl.broken:
        head.update(ok=True, repaired=False, reason="healthy — nothing to repair")
        return _emit(head, 0)

    p = _repair.propose(html, spec, base, now=src.fetched_at)
    head.update(p.as_dict())
    head["ok"] = p.accepted
    if not p.accepted:
        head["reason"] = ("no proposal survived the health check; rules.json is "
                          "untouched and the scrape is still broken")
        return _emit(head, 1)

    if a.accept:
        specs[spec.name] = p.spec
        _rules.save(specs, a.rules)
        if os.path.exists(a.proposal):
            os.remove(a.proposal)
        head["applied"] = {"rules": a.rules, "version": p.spec.version,
                           "proposal_cleared": a.proposal}
    else:
        with open(a.proposal, "w", encoding="utf-8") as fh:
            json.dump({"spec": spec_to_dict(p.spec), "changes": p.changes,
                       "diff": p.diff, "notes": p.notes,
                       "verified": p.health.as_dict()}, fh, indent=2)
            fh.write("\n")
        head["applied"] = False
        head["proposal"] = a.proposal
        head["next"] = ("review the diff, then re-run with --accept to write %s"
                        % a.rules)
    return _emit(head, 0)


def cmd_status(a) -> int:
    specs, spec = _spec(a)
    now = time.time()
    base = _health.load(a.baseline).get(spec.name)
    cache = _read_last_good(a.last_good).get(spec.name)
    would = resolve(spec, a.fixture)
    out = {
        "ok": True, "command": "status", "spec": spec.name,
        "rules": {"path": a.rules, "version": spec.version, "updated": spec.updated,
                  "row_selector": spec.row_selector,
                  "fields": [f.name for f in spec.fields],
                  "required": list(spec.required),
                  "ttl_hours": spec.ttl_hours,
                  "history": [h.get("reason", "") for h in spec.history][-3:]},
        "credentials": {"BRIGHT_DATA_API_TOKEN": bool(_env("BRIGHT_DATA_API_TOKEN")),
                        "BRIGHT_DATA_COLLECTOR_ID": bool(_env("BRIGHT_DATA_COLLECTOR_ID")),
                        "BRIGHTDATA_API_KEY": bool(_env("BRIGHTDATA_API_KEY")),
                        "BRD_ZONE": bool(_env("BRD_ZONE")),
                        "BRD_CUSTOMER": bool(_env("BRD_CUSTOMER"))},
        "next_fetch_would_use": would.as_dict(),
        "baseline": None,
        "cache": None,
        "proposal_pending": os.path.exists(a.proposal),
    }
    if base:
        out["baseline"] = {"captured": base.get("captured_iso"),
                           "age_hours": round((now - base.get("captured", now)) / 3600.0, 2),
                           "rows": base.get("rows"), "anchors": len(base.get("anchors", [])),
                           "rules_version": base.get("rules_version"),
                           "source": base.get("source")}
    if cache:
        age = (now - cache.get("fetched_at", now)) / 3600.0
        out["cache"] = {"fetched": cache.get("iso"), "age_hours": round(age, 2),
                        "rows": len(cache.get("rows", [])),
                        "stale": age > spec.ttl_hours,
                        "ttl_hours": spec.ttl_hours,
                        "source": cache.get("source")}
    return _emit(out, 0)


# ---------------------------------------------------------------------------
# the cached last-good rows
# ---------------------------------------------------------------------------

def _read_last_good(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_last_good(path: str, spec, src: Source, ex) -> None:
    data = _read_last_good(path)
    data[spec.name] = {
        "fetched_at": src.fetched_at,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(src.fetched_at)),
        "source": src.as_dict(),
        "rules_version": spec.version,
        "rows": ex.complete(spec.required),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------

# Shared flags: (name, dest, default, help)
SHARED = (
    ("--rules", "rules", RULES_PATH, "the version-controlled rules file"),
    ("--baseline", "baseline", BASELINE_PATH, "where known-good extractions are recorded"),
    ("--proposal", "proposal", PROPOSAL_PATH, "where an unaccepted repair is parked"),
    ("--last-good", "last_good", LAST_GOOD_PATH, "cache of the last healthy rows"),
    ("--spec", "spec", None, "which spec in the rules file"),
    ("--fixture", "fixture", None, "run against this fixture instead of the network"),
)


def build_parser() -> argparse.ArgumentParser:
    """Shared flags work on either side of the verb.

    That takes some care. argparse copies a subparser's whole namespace over
    the parent's, so a subparser declaring the same flag with a normal default
    silently discards what came before the verb — which here would mean
    `--rules /tmp/x check` quietly reading and, on --accept, WRITING the real
    rules.json. Defaulting the subparser copies to SUPPRESS leaves the
    attribute unset when the flag is absent, so the parent's value survives.
    """
    p = argparse.ArgumentParser(prog="scrape.cli",
                                description=__doc__.strip().split("\n")[0])
    for flag, dest, default, helptext in SHARED:
        p.add_argument(flag, dest=dest, default=default, help=helptext)

    sub = p.add_subparsers(dest="cmd", required=True)

    def child(name: str, helptext: str):
        c = sub.add_parser(name, help=helptext)
        for flag, dest, _default, h in SHARED:
            c.add_argument(flag, dest=dest, default=argparse.SUPPRESS, help=h)
        return c

    f = child("fetch", "scrape, report rows, cache them")
    f.add_argument("--save-baseline", action="store_true",
                   help="record this extraction as the known-good one")
    f.set_defaults(fn=cmd_fetch)

    child("check", "scrape and judge, write nothing").set_defaults(fn=cmd_check)

    r = child("repair", "re-derive selectors from the last good values")
    r.add_argument("--accept", action="store_true", help="write the proposal to rules.json")
    r.set_defaults(fn=cmd_repair)

    child("status", "what the config, baseline and cache say").set_defaults(fn=cmd_status)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except RulesError as e:
        return _fail(str(e), 2)


if __name__ == "__main__":
    raise SystemExit(main())
