#!/usr/bin/env python3
"""Add a viewable scraper-heal sequence.

"Show automatic scraper repair when a target website updates its HTML" is the
most-emphasised data criterion, but the heal only appeared as a pending row in
the review queue. This makes the whole loop watchable on demand without
lengthening the main run: break -> detect -> heal -> machine-verify the preview
-> human approve -> commit -> re-run green.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
h = open(idx, encoding="utf-8").read()

if "HEAL_STEPS" in h:
    print("already present")
    raise SystemExit(0)

def card(body):
    return body

steps = []

steps.append(("prose", 760,
    '<div class="who-tag sys"><i></i>watchdog · every 15 min</div>',
    'The vendor shipped a table restructure overnight. Nothing errored — the collector still returned rows, '
    'just <em>fewer keys</em> per row. That is the failure mode that matters: a scraper that breaks loudly gets '
    'noticed, and a scraper that quietly returns partial data poisons everything downstream.'))

steps.append(("fail", 820, None,
    '<div class="card"><div class="chead"><span class="ci">&#8800;</span><div style="min-width:0"><b>Schema key-diff</b>'
    '<div class="sub">run output vs the schema pinned in CLAUDE.md</div></div>'
    '<div class="act"><span class="tag fail">2 KEYS MISSING</span></div></div>'
    '<div class="derive">expected  grade, tensile_mpa, price_usd, in_stock\n'
    'received  grade, in_stock\n'
    'missing   tensile_mpa, price_usd        <-- silently omitted, not an error</div>'
    '<div class="grow"><span class="gn">rows returned</span><span class="gd">5 &mdash; the collector did not fail</span><span class="gm">&mdash;</span></div>'
    '<div class="grow"><span class="gn">downstream effect</span><span class="gd">bom_select has no tensile figure, so no fastener can be certified</span><span class="gm no">blocked</span></div>'
    '<div class="res" style="display:block">The run stops here rather than proceeding on partial data. '
    '<b class="no">scraper.broken</b> is emitted as a span event, which is what raises the alert.</div></div>'))

steps.append(("card", 760, None,
    '<div class="card"><div class="chead"><span class="ci" style="color:var(--warn)">&#8635;</span>'
    '<div style="min-width:0"><b>Auto-repair</b><div class="sub">brightdata scraper heal &middot; 41 s</div></div>'
    '<div class="act"><span class="tag warn">AWAITING APPROVAL</span></div></div>'
    '<div class="derive">$ npx -p @brightdata/cli scraper heal c_bolt_table \\\n'
    '    "tensile_mpa and price_usd missing after table restructure"\n\n'
    '  status      awaiting_approval\n'
    '  preview     5 rows, all 4 keys present\n'
    '  selector    td:nth-child(2) -> [data-col="tensile"]\n'
    '              td:nth-child(3) -> [data-col="price"]</div></div>'))

steps.append(("pass", 820, None,
    '<div class="card"><div class="chead"><span class="ci">&#10003;</span>'
    '<div style="min-width:0"><b>Machine checks the fix first</b>'
    '<div class="sub">the verifier runs on preview_result before a human sees it</div></div>'
    '<div class="act"><span class="tag pass">PREVIEW OK</span></div></div>'
    '<div class="grow"><span class="gn">schema key-diff</span><span class="gd">all 4 keys present in all 5 rows</span><span class="gm ok">&#10003;</span></div>'
    '<div class="grow"><span class="gn">type sanity</span><span class="gd">tensile 800&ndash;1040 MPa, price $0.09&ndash;$0.31 &mdash; plausible</span><span class="gm ok">&#10003;</span></div>'
    '<div class="grow"><span class="gn">row-count stability</span><span class="gd">5 rows, unchanged from the last good run</span><span class="gm ok">&#10003;</span></div>'
    '<div class="res" style="display:block">A person is asked to approve a fix that has <b>already been '
    'checked</b>, not to be the checker. Approving is a judgement call about intent; verifying is a machine job.</div></div>'))

steps.append(("gate", 900, "healapprove",
    '<div class="card alert"><div class="chead"><span class="ci">!</span><div style="min-width:0"><b>Approve the healed extractor</b>'
    '<div class="sub">the agent cannot approve this &mdash; its token has no approval action</div></div>'
    '<div class="act"><button class="primary" id="healapprove">Approve heal</button></div></div>'
    '<div class="res" hidden><b>Approved</b> by rishith &middot; <code>scraper approve c_bolt_table</code> &middot; '
    'healed selectors committed to CLAUDE.md &middot; diff in git</div></div>'))

steps.append(("card", 760, None,
    '<div class="card"><div class="chead"><span class="ci">&#9998;</span>'
    '<div style="min-width:0"><b>The repair is version-controlled</b>'
    '<div class="sub">config lives in the repo, not in a dashboard</div></div>'
    '<div class="act"><span class="tag pass">COMMITTED</span></div></div>'
    '<div class="derive">  CLAUDE.md\n'
    '- tensile_mpa:  td:nth-child(2)\n'
    '- price_usd:    td:nth-child(3)\n'
    '+ tensile_mpa:  [data-col="tensile"]\n'
    '+ price_usd:    [data-col="price"]\n'
    '+ healed:       2026-08-22 &middot; approved by rishith &middot; run heal-0007</div>'
    '<div class="res" style="display:block">Next run reads the healed selectors from the same file the agent '
    'already loads. Nobody has to remember to update a console.</div></div>'))

steps.append(("done", 700, None,
    '<div class="pill"><span class="tag pass">HEALED</span><b>heal-0007</b>'
    '<span class="mu">detected in 15 min &middot; healed in 41 s &middot; verified before review &middot; committed</span>'
    '<span class="tm">2m 55s</span><button class="ghost" id="replay">Replay</button></div>'
    '<div class="prov"><span class="p bd"><i></i>BRIGHT DATA &middot; c_bolt_table &middot; heal approved</span>'
    '<span class="p sig"><i></i>SIGNOZ &middot; scraper.broken &rarr; heal_approved</span>'
    '<span class="p port"><i></i>PORT &middot; review queue &middot; approved by rishith</span></div>'))

js = "  var HEAL_STEPS = [\n"
for kind, d, tag, body in steps:
    if kind == "prose":
        js += "    { d: %d, kind: 'prose', tag: %s, text: %s },\n" % (d, json.dumps(tag), json.dumps(body))
    elif tag and kind == "gate":
        js += "    { d: %d, kind: 'gate', wait: %s, html: %s },\n" % (d, json.dumps(tag), json.dumps(body))
    else:
        js += "    { d: %d, kind: '%s', html: %s },\n" % (d, kind, json.dumps(body))
js += "  ];\n\n"

anchor = "  var RUNS = {"
assert anchor in h
h = h.replace(anchor, js + anchor, 1)

# register the run and teach playLive about it
h = h.replace("""    '1039': { t: 'Bracket margin sweep, FoS 1.5', n: 18, failed: true },""",
              """    '1039': { t: 'Bracket margin sweep, FoS 1.5', n: 18, failed: true },
    'heal': { t: 'Scraper heal — bolt table restructure', live: true, script: 'heal' },""")

h = h.replace("""    if (variant === 'budget') {""",
              """    if (variant === 'heal') {
      script = HEAL_STEPS;
      $('#tb-title').textContent = RUNS['heal'].t;
    } else if (variant === 'budget') {""")

h = h.replace("""    if (r.live) playLive(); else snapshot(id);""",
              """    if (r.live) playLive(r.script || null); else snapshot(id);""")

# sidebar entry
h = h.replace("""    <div class="sect">Recents</div>""",
              """    <div class="thread" data-run="heal" data-peek="The vendor restructured its table overnight. The collector did not error — it quietly returned fewer keys per row, which is the failure mode that actually matters." data-foot="healed · verified before review · 2m 55s">
      <span class="pip run"></span><span class="txt">Scraper heal — bolt table</span></div>
    <div class="sect">Recents</div>""")

open(idx, "w", encoding="utf-8").write(h)
print("heal thread added (%d steps)" % len(steps))
