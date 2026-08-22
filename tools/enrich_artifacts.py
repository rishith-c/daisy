#!/usr/bin/env python3
"""Give the Artifacts view provenance: which run made it, when, how big.

A grid of thumbnails is a gallery. An artifact registry has to answer "where
did this come from and can I trust it", so each card carries its producing run
and the gate that cleared it, and the table below is the full manifest.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
h = open(idx, encoding="utf-8").read()

if "Manifest" in h and "cleared by" in h:
    print("already enriched")
    raise SystemExit(0)

start = h.index('  <div class="view" id="view-artifacts">')
end = h.index('  <!-- ============ AUTOMATIONS ============ -->')

new = '''  <div class="view" id="view-artifacts"><div class="page"><div class="pcol">
    <h2>Artifacts</h2><div class="lede">What run 1042 shipped. Every artifact names the run that produced it and the gate that cleared it &mdash; an artifact with no provenance is just a file.</div>
    <div class="grid" style="margin-bottom:22px">
      <div class="acard"><div class="th"><div class="mini"><div class="r h"></div><div class="r g"></div><div class="r g"></div><div class="r"></div><div class="r g"></div></div></div>
        <div class="mt"><b>Fleet dashboard</b><span>run 1042 &middot; cleared by taste.t1 + t2</span></div></div>
      <div class="acard"><div class="th">
        <svg viewBox="0 0 200 130" width="168" height="110" role="img" aria-label="Bracket stress heatmap">
          <defs>
            <linearGradient id="sg" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#C0392B"/><stop offset=".45" stop-color="#E9C64A"/><stop offset="1" stop-color="#6B8F2E"/></linearGradient>
            <linearGradient id="lg" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#6B8F2E"/><stop offset=".55" stop-color="#E9C64A"/><stop offset="1" stop-color="#C0392B"/></linearGradient>
          </defs>
          <polygon points="30,100 110,100 140,82 60,82" fill="#E7E6E1" stroke="#D8D7CF"/>
          <polygon points="60,82 140,82 140,30 60,30" fill="url(#sg)" stroke="#D8D7CF"/>
          <polygon points="30,100 60,82 60,30 30,48" fill="#EDEDE8" stroke="#D8D7CF"/>
          <circle cx="45" cy="94" r="4" fill="#FFF" stroke="#94928A"/><circle cx="95" cy="94" r="4" fill="#FFF" stroke="#94928A"/>
          <rect x="168" y="30" width="8" height="70" fill="url(#lg)" rx="2"/>
          <text x="180" y="36" fill="#94928A" font-size="8" font-family="monospace">172</text>
          <text x="180" y="102" fill="#94928A" font-size="8" font-family="monospace">0</text>
          <text x="163" y="118" fill="#B5B3AA" font-size="7" font-family="monospace">MPa</text>
        </svg></div>
        <div class="mt"><b>bracket_v2.glb</b><span>run 1042 &middot; cleared by physics &middot; FoS 1.62</span></div></div>
      <div class="acard"><div class="th"><div class="doc"><b># margin-report &middot; run 1042</b><br>trace_id  a41f09c2<br>approver  rishith<br>web bending   106/172 MPa  <b>FoS 1.62</b><br>bolt shear     96/380 MPa  <b>FoS 3.94</b><br>bolt  M4 8.8 $0.14 &middot; vendor 11:07<br>mass  46.8 g &le; 60 g</div></div>
        <div class="mt"><b>margin-report.md</b><span>run 1042 &middot; cites trace + approver</span></div></div>
      <div class="acard"><div class="th"><div class="doc"><b>flight-recorder.jsonl</b><br>{"kind":"gate","name":"taste.t1","v":"fail"}<br>{"kind":"repair","by":"resume"}<br>{"kind":"gate","name":"physics","v":"fail"}<br>{"kind":"repair","by":"algebra"}<br>{"kind":"merge","scorecard":"green"}</div></div>
        <div class="mt"><b>Flight recorder</b><span>run 1042 &middot; append-only &middot; replayable</span></div></div>
    </div>

    <h3>Manifest</h3>
    <div class="tbl"><table class="l">
      <tr><th>Artifact</th><th>Kind</th><th>Size</th><th>From</th><th>Cleared by</th><th>Committed</th></tr>
      <tr><td class="s">bracket_v2.step</td><td class="mo">CAD &middot; STEP AP214</td><td class="mo">412 KB</td><td class="mo">run 1042</td><td class="mo">physics &middot; FoS 1.62</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">bracket_v2.glb</td><td class="mo">mesh + &sigma; field</td><td class="mo">96 KB</td><td class="mo">run 1042</td><td class="mo">physics</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">bracket.py</td><td class="mo">build123d source</td><td class="mo">4.1 KB</td><td class="mo">run 1042</td><td class="mo">physics</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">bom.csv</td><td class="mo">bill of materials</td><td class="mo">1.2 KB</td><td class="mo">run 1042</td><td class="mo">scrape freshness</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">margin-report.md</td><td class="mo">verification record</td><td class="mo">3.4 KB</td><td class="mo">run 1042</td><td class="mo">all gates</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">dashboard/ (21 files)</td><td class="mo">Next.js app</td><td class="mo">68 KB</td><td class="mo">run 1042</td><td class="mo">taste + contract + smoke</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">flight-recorder.jsonl</td><td class="mo">event log</td><td class="mo">412 KB</td><td class="mo">run 1042</td><td class="mo">&mdash;</td><td class="mo">6m ago</td></tr>
      <tr><td class="s">run-essence.json</td><td class="mo">compacted run</td><td class="mo">17 KB</td><td class="mo">run 1042</td><td class="mo">probe 100%</td><td class="mo">6m ago</td></tr>
    </table></div>

    <div class="card" style="margin-top:18px"><div class="chead"><span class="ci">&#8681;</span>
      <div style="min-width:0"><b>The report is not a summary of the run</b>
      <div class="sub">margin-report.md is generated from the gate results, not from the narration</div></div>
      <div class="act"><span class="tag info">TRACEABLE</span></div></div>
      <div class="derive"># margin-report &middot; run 1042
trace_id   a41f09c2          &lt;- the SigNoz trace, not a description of it
approver   rishith           &lt;- the Port action-run that granted the merge
bolt       M4 &middot; 8.8 &middot; $0.14  &lt;- the scraped row, with its timestamp
web bend   106 / 172 MPa     &lt;- computed, not asserted</div>
      <div class="res" style="display:block">Every line resolves to something that exists elsewhere: a trace you can open,
      an approval you can audit, a scraped row you can re-fetch, a formula you can re-run. That is the difference
      between a record and a claim.</div></div>
  </div></div></div>

'''
h = h[:start] + new + h[end:]
open(idx, "w", encoding="utf-8").write(h)
print("artifacts enriched with provenance + manifest")
