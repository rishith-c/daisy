"""Tests for the Bright Data scraper pipeline.

    python3 -m scrape.test_scrape

Every case runs against the HTML fixtures in scrape/fixtures/. Nothing here
opens a socket or reads a credential, which is the point: the test that proves
the scraper heals itself has to run on a laptop in a hotel conference room.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import time

from . import extract, health, repair, rules
from . import cli
from .cli import Source, load_html, main as cli_main, resolve
from .extract import Extraction, FieldReport, coerce, parse, select
from hardware.margins import NoGroundTruth, select_fastener

PASS, FAIL = 0, 0

V1 = "vendor_v1.html"
V1_PARTIAL = "vendor_v1_partial.html"
V2 = "vendor_v2.html"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


def spec():
    return rules.load()["vendors.fastener"]


def html(fixture):
    with open(spec().fixture_path(fixture), "r", encoding="utf-8") as fh:
        return fh.read()


def run(fixture, s=None):
    s = s or spec()
    return extract.run(s, html(fixture))


def baseline(s=None):
    s = s or spec()
    return health.capture(run(V1, s), s, "fixture:" + V1)


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------

def test_dom():
    print("\nthe DOM — real vendor markup, not well-formed markup")
    root = parse("<table class=t><tbody><tr class=r><td class=a>8.8<td class=b>$0.09"
                 "<tr class=r><td class=a>10.9<td class=b>$0.23</tbody></table>")
    rows = select(root, "table.t > tbody > tr.r")
    check("unclosed <td> and <tr> still branch instead of nesting", len(rows) == 2,
          str(len(rows)))
    check("each row keeps only its own cells",
          [select(r, "td.b")[0].text() for r in rows] == ["$0.09", "$0.23"])
    check(":nth-child indexes elements, not text nodes",
          [n.text() for n in select(root, "tr.r > td:nth-child(2)")] == ["$0.09", "$0.23"])
    check("[attr=value] matches", len(select(root, "td[class=a]")) == 2)
    check("'>' is stricter than descendant",
          len(select(root, "table.t > tr.r")) == 0 and len(select(root, "table.t tr.r")) == 2)
    check("an unmatched end tag is dropped, not fatal",
          len(select(parse("<div class=x><p>hi</span></div>"), "div.x > p")) == 1)
    check("character references decode",
          parse("<i>M4 &times; 0.7</i>").text() == "M4 × 0.7",
          parse("<i>M4 &times; 0.7</i>").text())
    check("script bodies are not text",
          parse("<div>a<script>var x=99</script></div>").text() == "a",
          parse("<div>a<script>var x=99</script></div>").text())
    check("an unsupported pseudo-class is refused at parse time",
          _raises(lambda: extract.parse_selector("td:first-child"), extract.SelectorError))
    v2 = parse(html(V2))
    check("the real v2 page holds 7 product cards", len(select(v2, "article.sku")) == 7,
          str(len(select(v2, "article.sku"))))


def test_coercion():
    print("\ncoercion — tolerant about format, strict about meaning")
    check("currency", coerce("$0.09", "num") == 0.09)
    check("thread designation", coerce("M4", "num") == 4.0)
    check("thousands separator", coerce("1,040 MPa", "num") == 1040.0)
    check("prefixed currency code", coerce("USD 0.58", "num") == 0.58)
    check("stock labels", coerce("In stock", "bool") is True
          and coerce("Out of stock", "bool") is False)
    check("an unknown label converts to nothing, not to False",
          coerce("Ships today", "bool") is None)
    check("empty text is not a value", coerce("   ", "str") is None)
    check("a pattern narrows before typing",
          coerce("Ships in 3 days, 4 left", "num", r"(\d+) left") == 4.0)
    check("a pattern that misses yields nothing",
          coerce("no digits", "num", r"(\d+) left") is None)
    check("an unknown type is a programming error, not a None",
          _raises(lambda: coerce("1", "integer"), ValueError))


def test_config():
    print("\nrules.json — config as data, checked at load")
    s = spec()
    check("the shipped spec loads", s.name == "vendors.fastener")
    check("field names are exactly select_fastener's contract",
          set(s.field_names) == {"grade", "dia_mm", "tensile_mpa", "price_usd", "in_stock"},
          str(s.field_names))
    check("every contract field is required", set(s.required) == set(s.field_names))
    check("a spec round-trips through JSON unchanged",
          rules.spec_from_dict(rules.spec_to_dict(s)) == s)
    bad = rules.spec_to_dict(s)
    bad["fields"][0]["type"] = "integer"
    check("an unknown field type is refused",
          _raises(lambda: rules.spec_from_dict(bad), rules.RulesError))
    bad2 = rules.spec_to_dict(s)
    bad2["fields"][0]["selector"] = "td:first-child"
    check("an inexpressible selector is refused at load, not at run",
          _raises(lambda: rules.spec_from_dict(bad2), rules.RulesError))
    bad3 = rules.spec_to_dict(s)
    bad3["fields"].append(dict(bad3["fields"][0]))
    check("duplicate field names are refused",
          _raises(lambda: rules.spec_from_dict(bad3), rules.RulesError))
    bad4 = rules.spec_to_dict(s)
    for f in bad4["fields"]:
        f["required"] = False
    check("a spec where nothing is required is refused",
          _raises(lambda: rules.spec_from_dict(bad4), rules.RulesError))
    v2 = rules.bump(s, "vendor restructured", ["price_usd: a -> b"])
    check("a bump advances the version", v2.version == s.version + 1)
    check("a bump records why, in the file", v2.history[-1]["reason"] == "vendor restructured")


def test_extract_v1():
    print("\nextraction — the page that works")
    s = spec()
    ex = run(V1, s)
    check("every product row is found", ex.rows_matched == 7, str(ex.rows_matched))
    check("the header row is not a product", len(ex.complete(s.required)) == 7)
    check("the first row is exactly right",
          ex.rows[0] == {"grade": "8.8", "dia_mm": 3.0, "tensile_mpa": 800.0,
                         "price_usd": 0.09, "in_stock": True}, str(ex.rows[0]))
    check("an out-of-stock row reads as False",
          [r for r in ex.rows if r["price_usd"] == 0.19][0]["in_stock"] is False)
    check("no row has a gap", ex.gaps == [], str(ex.gaps))
    check("each field reports one match per row",
          all(r.matched == 7 and r.typed == 7 for r in ex.fields))
    check("no field selector matched more than one node per row",
          all(r.multiple == 0 for r in ex.fields))
    check("the report keeps the raw string for the diff",
          ex.report("price_usd").sample == "$0.09", ex.report("price_usd").sample)


def test_drift_fewer_keys():
    print("\ndrift — 200 OK, same rows, one fewer key (the one that matters)")
    s = spec()
    ex = run(V1_PARTIAL, s)
    base = baseline(s)
    check("the row count is untouched, so a row-count alarm would miss it",
          ex.rows_matched == 7, str(ex.rows_matched))
    check("four of five fields still extract",
          sum(1 for r in ex.fields if r.typed == 7) == 4)
    check("price is absent from every row",
          all("price_usd" not in r for r in ex.rows))
    check("no row survives as complete", len(ex.complete(s.required)) == 0)
    hl = health.evaluate(ex, s, base)
    check("health calls it broken", hl.broken)
    check("and names the required-field check",
          [c.name for c in hl.failures] == ["scrape.required"],
          str([c.name for c in hl.failures]))
    check("the detail says which field and how many rows",
          "price_usd in 7 row(s)" in hl.failures[0].detail, hl.failures[0].detail)


def test_drift_restructured():
    print("\ndrift — the vendor rebuilt the page")
    s = spec()
    ex = run(V2, s)
    hl = health.evaluate(ex, s, baseline(s))
    check("no row selector match at all", ex.rows_matched == 0)
    check("health calls it broken", hl.broken)
    check("the row check fires", "scrape.rows" in [c.name for c in hl.failures])
    check("v1 is still healthy under the same rules",
          not health.evaluate(run(V1, s), s, baseline(s)).broken)


def test_health_checks():
    print("\nhealth — each check earns its place")
    s = spec()
    base = baseline(s)
    good = run(V1, s)
    check("a good scrape against a good baseline is healthy",
          not health.evaluate(good, s, base).broken)
    check("with no baseline it warns rather than passing silently",
          "scrape.baseline" in [c.name for c in health.evaluate(good, s, None).warnings])

    # Right shape, wrong column: prices that are really property classes still
    # parse as numbers and still sit inside the declared range.
    swapped = s.with_field(rules.replace(s.field("price_usd"), selector="td.col-grade"))
    hl = health.evaluate(run(V1, swapped), swapped, base)
    check("a column pointed at the wrong data is caught by its median",
          [c.name for c in hl.failures] == ["scrape.median"],
          str([c.name for c in hl.failures]))

    out = s.with_field(rules.replace(s.field("dia_mm"), selector="td.col-tensile"))
    hl = health.evaluate(run(V1, out), out, base)
    check("a value outside the declared range fails",
          "scrape.range" in [c.name for c in hl.failures])

    flat = _fake(s, [dict(r, grade="8.8") for r in good.rows])
    hl = health.evaluate(flat, s, base)
    check("a column collapsed to one repeated value fails",
          "scrape.distinct" in [c.name for c in hl.failures],
          str([c.name for c in hl.failures]))

    thin = _fake(s, good.rows[:2], matched=2)
    check("a row count under the baseline tolerance fails",
          "scrape.rows" in [c.name for c in health.evaluate(thin, s, base).failures])
    fat = _fake(s, good.rows, matched=40)
    check("a row count that explodes warns",
          "scrape.rows.growth" in [c.name for c in health.evaluate(fat, s, base).warnings])

    now = time.time()
    fresh = health.evaluate(good, s, base, captured_at=now, now=now)
    stale = health.evaluate(good, s, base, captured_at=now - 3600 * 48, now=now)
    check("fresh data raises no freshness warning",
          "scrape.freshness" not in [c.name for c in fresh.warnings])
    check("data past the TTL warns", "scrape.freshness" in [c.name for c in stale.warnings])
    check("stale is not the same as broken", not stale.broken and not stale.ok)

    check("a matched selector that stops converting is a type failure, not a gap",
          "scrape.types" in [c.name for c in health.evaluate(
              run(V1, s.with_field(rules.replace(s.field("dia_mm"),
                                                 selector="td.col-stock"))),
              s, base).failures])

    b = health.capture(good, s, "fixture:" + V1)
    check("the baseline records anchors for repair to work from", len(b["anchors"]) == 7)
    check("the baseline records the median of every numeric column",
          b["fields"]["price_usd"]["median"] == 0.23, str(b["fields"]["price_usd"]))


def _fake(s, rows, matched=None):
    """An Extraction built by hand, for checks that need a shape no fixture has."""
    reps = [FieldReport(f.name, f.selector, f.attr, len(rows), len(rows), 0, "")
            for f in s.fields]
    return Extraction(s.name, rows, s.row_selector,
                      len(rows) if matched is None else matched, reps, [])


def test_repair_rename():
    print("\nrepair — one class renamed, one line changed")
    s = spec()
    p = repair.propose(html(V1_PARTIAL), s, baseline(s))
    check("the repair is accepted only because health passed", p.accepted)
    check("exactly one selector changed", len(p.changes) == 1, str(p.changes))
    check("and it is the one that broke", p.changes[0]["path"] == "price_usd")
    check("it found the renamed class rather than a position",
          p.spec.field("price_usd").selector == "td.price-cell",
          p.spec.field("price_usd").selector)
    check("the row selector was left alone",
          p.spec.row_selector == s.row_selector)
    check("the four working selectors were left alone",
          all(p.spec.field(n) == s.field(n)
              for n in ("grade", "dia_mm", "tensile_mpa", "in_stock")))
    check("the repaired scrape returns every row", len(p.rows) == 7)
    check("with the original prices",
          sorted(r["price_usd"] for r in p.rows)
          == [0.09, 0.14, 0.19, 0.23, 0.27, 0.31, 0.58])


def test_repair_restructure():
    print("\nrepair — the whole page rebuilt")
    s = spec()
    base = baseline(s)
    p = repair.propose(html(V2), s, base)
    check("a verified proposal came back", p.accepted)
    check("the row container was re-derived", p.spec.row_selector == "article.sku",
          p.spec.row_selector)
    check("it was not fooled by the 'recently viewed' panel reusing the classes",
          len(p.rows) == 7, "%d rows" % len(p.rows))
    check("every field was re-derived", len(p.changes) == 6, str(len(p.changes)))
    check("grade", p.spec.field("grade").selector == "dd.spec-class")
    check("tensile", p.spec.field("tensile_mpa").selector == "dd.spec-tensile")
    check("price", p.spec.field("price_usd").selector == "span.sku-price")
    check("thread diameter anchored on the spec row, not the product title",
          p.spec.field("dia_mm").selector == "dd.spec-thread",
          p.spec.field("dia_mm").selector)
    check("availability followed the data into an attribute",
          p.spec.field("in_stock").attr == "data-instock",
          str(p.spec.field("in_stock").attr))
    check("the 'Backordered' label was rejected because it fails on the other six rows",
          p.spec.field("in_stock").selector == "p.sku-avail",
          p.spec.field("in_stock").selector)
    check("the version was bumped", p.spec.version == s.version + 1)
    check("the reason is recorded in the spec's own history",
          "re-derived" in p.spec.history[-1]["reason"])

    was = {tuple(sorted(r.items())) for r in base["anchors"]}
    now = {tuple(sorted(r.items())) for r in p.rows}
    check("the repaired scrape recovers exactly the original data, order aside",
          was == now, "%d recovered of %d" % (len(now & was), len(was)))


def test_repair_refuses():
    print("\nrepair — the cases where it must not pretend")
    s = spec()
    p = repair.propose(html(V2), s, {"anchors": []})
    check("with no anchors it proposes nothing", p.spec is None and not p.accepted)
    check("and says why", "anchor" in " ".join(p.notes).lower(), str(p.notes))
    gone = repair.propose("<html><body><p>We are closed for maintenance.</p></body></html>",
                          s, baseline(s))
    check("a page with none of the data yields no proposal", not gone.accepted)
    check("and does not claim a repair", gone.spec is None)
    check("the diff of an unchanged spec says so",
          repair.diff(s, s)[1] == ["  (no selector changed)"])


def test_source_is_honest():
    print("\nsource — never imply a fetch that did not happen")
    s = spec()
    keep = {k: os.environ.get(k) for k in
            ("BRIGHTDATA_API_KEY", "BRD_ZONE", "BRD_CUSTOMER",
             "BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_COLLECTOR_ID")}
    try:
        for k in keep:
            os.environ.pop(k, None)
        src = resolve(s, None)
        check("with no credentials the mode is fixture", src.mode == "fixture")
        check("live is False, not merely absent", src.live is False)
        check("the reason names the missing variable",
              "BRIGHTDATA_API_KEY" in src.reason, src.reason)
        check("the fixture path is reported, not the vendor URL",
              src.location.endswith(V1))

        os.environ["BRIGHTDATA_API_KEY"] = "secret-token-do-not-print"
        os.environ["BRD_ZONE"] = "daisy_unlocker"
        api = resolve(s, None)
        check("key plus zone selects the unlocker API", api.mode == "live-api")
        check("that mode reports itself as live", api.live is True)
        os.environ["BRD_CUSTOMER"] = "hl_deadbeef"
        prox = resolve(s, None)
        check("adding a customer id selects the super-proxy", prox.mode == "live-proxy")
        os.environ["BRIGHT_DATA_API_TOKEN"] = "studio-token-do-not-print"
        os.environ["BRIGHT_DATA_COLLECTOR_ID"] = "c_daisy_fasteners"
        studio = resolve(s, None)
        check("token plus collector selects Scraper Studio", studio.mode == "live-studio")
        check("the published collector is named in provenance",
              studio.location == "collector:c_daisy_fasteners", studio.location)
        check("--fixture overrides credentials rather than being ignored",
              resolve(s, V2).mode == "fixture")
        blob = json.dumps([src.as_dict(), api.as_dict(), prox.as_dict(), studio.as_dict(),
                           resolve(s, V2).as_dict()])
        check("no credential value reaches the output",
              "secret-token-do-not-print" not in blob and "studio-token-do-not-print" not in blob
              and "hl_deadbeef" not in blob)
        check("a fixture source labels itself by file, not by absolute path",
              resolve(s, V2).label == "fixture:" + V2, resolve(s, V2).label)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_studio_collection_contract():
    print("\nScraper Studio — trigger, poll, typed handoff")
    s = spec()
    source = Source("live-studio", "collector:c_daisy_fasteners",
                    "token and collector are set")
    calls = []
    replies = [
        {"collection_id": "j_daisy_snapshot"},
        {"status": "building"},
        [{"grade": "8.8", "dia_mm": 4, "tensile_mpa": 800,
          "price_usd": 0.14, "in_stock": True}],
    ]

    class Response:
        status = 200
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return json.dumps(self.payload).encode()

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_method(), request.data, request.headers))
        return Response(replies.pop(0))

    old_open, old_sleep = cli.urllib.request.urlopen, cli.time.sleep
    keep = {k: os.environ.get(k) for k in
            ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_COLLECTOR_ID")}
    try:
        os.environ["BRIGHT_DATA_API_TOKEN"] = "studio-secret"
        os.environ["BRIGHT_DATA_COLLECTOR_ID"] = "c_daisy_fasteners"
        cli.urllib.request.urlopen = urlopen
        cli.time.sleep = lambda _: None
        studio_html = load_html(source, s)
    finally:
        cli.urllib.request.urlopen, cli.time.sleep = old_open, old_sleep
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    rows = extract.run(s, studio_html).complete(s.required)
    check("the collector is triggered before its dataset is read",
          calls[0][1] == "POST" and "/dca/trigger?" in calls[0][0]
          and "collector=c_daisy_fasteners" in calls[0][0])
    check("the URL input matches the governed scraper spec",
          json.loads(calls[0][2]) == [{"url": s.url}])
    check("dataset polling uses the returned collection id",
          len(calls) == 3 and all("id=j_daisy_snapshot" in c[0] for c in calls[1:]))
    check("the bearer token is sent but never written into provenance",
          calls[0][3].get("Authorization") == "Bearer studio-secret"
          and "studio-secret" not in json.dumps(source.as_dict()))
    check("the structured dataset enters the existing physics contract",
          rows == [{"grade": "8.8", "dia_mm": 4.0, "tensile_mpa": 800.0,
                    "price_usd": 0.14, "in_stock": True}], str(rows))
    check("the snapshot id is preserved as evidence",
          source.snapshot_id == "j_daisy_snapshot", source.snapshot_id)


def test_cli_loop():
    print("\nthe CLI — break, detect, repair, pass")
    with tempfile.TemporaryDirectory() as d:
        r = os.path.join(d, "rules.json")
        b = os.path.join(d, "baseline.json")
        prop = os.path.join(d, "rules.proposed.json")
        lg = os.path.join(d, "last_good.json")
        shutil.copy(rules.RULES_PATH, r)
        base = ["--rules", r, "--baseline", b, "--proposal", prop, "--last-good", lg]

        code, out = _cli(base + ["fetch", "--fixture", V1, "--save-baseline"])
        check("fetch on the good page exits 0", code == 0, str(code))
        check("it says plainly that it did not go to the network",
              out["source"]["live"] is False
              and "no network call was made" in out["source"]["reason"],
              out["source"]["reason"])
        check("it returns the contract schema",
              set(out["rows"][0]) == {"grade", "dia_mm", "tensile_mpa",
                                      "price_usd", "in_stock"})
        check("and records the baseline", out["baseline"]["saved"] is True)

        code, out = _cli(base + ["check", "--fixture", V1])
        check("check on the good page exits 0", code == 0 and out["ok"])

        code, out = _cli(base + ["check", "--fixture", V2])
        check("check on the restructured page exits 1", code == 1, str(code))
        check("and reports it broken", out["health"]["verdict"] == "broken")
        check("and points at the repair command", "repair" in out["next"])

        before = open(r).read()
        code, out = _cli(base + ["repair", "--fixture", V2])
        check("repair verifies a proposal and exits 0", code == 0 and out["repaired"])
        check("but does not apply it", out["applied"] is False)
        check("rules.json is byte-for-byte untouched", open(r).read() == before)
        check("the proposal is written where a human can read it",
              os.path.exists(prop))
        check("the proposal carries the health run that justified it",
              json.load(open(prop))["verified"]["verdict"] == "healthy")
        check("the diff shows old and new side by side",
              any("- td.col-price" in l for l in out["diff"])
              and any("+ span.sku-price" in l for l in out["diff"]))

        code, out = _cli(base + ["status"])
        check("status flags the pending proposal", out["proposal_pending"] is True)
        check("status reports credentials as present/absent only",
              set(out["credentials"].values()) == {False})

        code, out = _cli(base + ["repair", "--fixture", V2, "--accept"])
        check("--accept applies the repair", code == 0 and out["applied"]["version"] == 2)
        check("and clears the proposal", not os.path.exists(prop))

        code, out = _cli(base + ["check", "--fixture", V2])
        check("the same page that failed now passes", code == 0 and out["ok"], str(code))
        check("at the new rules version", out["rules_version"] == 2)

        code, out = _cli(base + ["repair", "--fixture", V2])
        check("a healthy scrape reports nothing to repair",
              code == 0 and out["repaired"] is False)

        code, out = _cli(base + ["check", "--fixture", "does_not_exist.html"])
        check("a missing fixture is a usage error, not an empty scrape", code == 2, str(code))

        code, out = _cli(base + ["fetch", "--fixture", V1])
        check("after the repair it is the OLD markup that fails, and it says so",
              code == 1 and out["health"]["verdict"] == "broken", str(code))


def _cli(argv):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli_main(argv)
    except SystemExit as e:
        code = e.code
    return code, json.loads(buf.getvalue())


def test_feeds_the_physics_gate():
    print("\nthe handoff — these rows certify a bracket")
    s = spec()
    v1_rows = run(V1, s).complete(s.required)
    pick = select_fastener(v1_rows, 2.4, 2, 1.5)
    check("the scrape selects the cheapest viable fastener",
          pick and pick["unit_price"] == 0.09, str(pick.get("unit_price") if pick else None))
    check("and the pick carries its own shear gate", pick["gate"].against(1.5))

    p = repair.propose(html(V2), s, baseline(s))
    v2_rows = p.rows
    pick2 = select_fastener(v2_rows, 2.4, 2, 1.5)
    check("the repaired scrape reaches the same decision",
          pick2["row"]["grade"] == pick["row"]["grade"]
          and pick2["unit_price"] == pick["unit_price"], str(pick2["unit_price"]))
    check("out-of-stock rows are still excluded after repair",
          all(r["in_stock"] for r in v2_rows if r["price_usd"] == pick2["unit_price"]))
    check("a scrape that returns nothing refuses to certify",
          _raises(lambda: select_fastener([], 2.4, 2, 1.5), NoGroundTruth))


def main():
    print("bright data scraper studio — test suite")
    test_dom()
    test_coercion()
    test_config()
    test_extract_v1()
    test_drift_fewer_keys()
    test_drift_restructured()
    test_health_checks()
    test_repair_rename()
    test_repair_restructure()
    test_repair_refuses()
    test_source_is_honest()
    test_studio_collection_contract()
    test_cli_loop()
    test_feeds_the_physics_gate()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
