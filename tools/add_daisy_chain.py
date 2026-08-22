#!/usr/bin/env python3
"""Idempotently upgrade Daisy's generated app shell for live Daisy Chain runs.

`index.html` is generated output and must not be edited by hand. This focused
transform owns the blank-run behavior, the live Chain bridge callbacks, and
the reset row in Settings. Onboarding remains owned by `onboarding.html` and
`tools/add_onboarding.py`.
"""

from __future__ import annotations

import os
import re
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "index.html")
BASE_GUARD = "daisy:chain-live"
V2_GUARD = "daisy:real-agent-v2"
V3_GUARD = "daisy:real-agent-v3"
V4_GUARD = "daisy:real-agent-v4"
GUARD = "daisy:real-agent-v5"

EMPTY_RUN_CSS = r"""/* ---------- empty run ---------- */
.run-empty {
  position: absolute; inset: 0 0 0 34px; z-index: 2; pointer-events: none;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 36px; text-align: center; transform: translateY(-2vh); /* taste-ok: one deliberate empty-state focal point */
}
.run-empty[hidden] { display: none; }
.run-empty-mark { width: 58px; height: 58px; color: var(--ink-3); opacity: .68; }
.run-empty-mark svg { width: 100%; height: 100%; display: block; }
.run-empty h2 { margin-top: 18px; font-size: 23px; line-height: 1.25; font-weight: 550; letter-spacing: -.025em; }
.run-empty p { max-width: 440px; margin-top: 8px; color: var(--ink-3); font-size: 12.5px; line-height: 1.55; }

"""

EMPTY_RUN_HTML = r'''      <div class="run-empty" id="run-empty" role="status" aria-live="polite" aria-hidden="false">
        <span class="run-empty-mark" id="run-empty-mark" aria-hidden="true"></span>
        <h2>What should we build in Daisy?</h2>
        <p>Start with the outcome. Use one local model, or let Daisy Chain coordinate the full crew.</p>
      </div>
'''

EMPTY_RUN_JS = r'''  function showEmptyRun() {
    var empty = $('#run-empty'), mark = $('#run-empty-mark');
    if (!empty) return;
    if (mark && !mark.firstChild) mark.innerHTML = DAISY_MARK();
    empty.hidden = false;
    empty.setAttribute('aria-hidden', 'false');
  }

  function hideEmptyRun() {
    var empty = $('#run-empty');
    if (!empty) return;
    empty.hidden = true;
    empty.setAttribute('aria-hidden', 'true');
  }

'''

CHAIN_CSS = r"""/* ---------- Daisy Chain: live botanical org map ---------- */
.chain-shell { margin-top: 18px; border: .5px solid var(--border); border-radius: var(--r-panel);
  background: color-mix(in srgb, var(--leaf-wash) 28%, var(--bg)); overflow: hidden; }
.chain-toolbar { min-height: 48px; display: flex; align-items: center; gap: 12px; padding: 10px 13px;
  border-bottom: .5px solid var(--border); background: color-mix(in srgb, var(--bg) 88%, transparent); }
.chain-toolbar .chain-state { min-width: 0; flex: 1; }
.chain-toolbar b { display: block; font-size: 13px; font-weight: 600; }
.chain-toolbar span { display: block; color: var(--ink-3); font: 10.5px/1.45 var(--mono); }
.chain-canvas { min-height: 360px; padding: 26px 22px 30px; overflow-x: auto; }
.chain-empty { max-width: 460px; margin: 88px auto; color: var(--ink-3); }
.chain-empty b { color: var(--ink); display: block; margin-bottom: 4px; }
.chain-tree { min-width: 520px; }
.chain-root { display: flex; justify-content: center; position: relative; z-index: 2; }
.chain-node { width: 210px; min-height: 82px; padding: 11px 12px; border: .5px solid var(--border-strong);
  border-radius: var(--r-card); background: var(--bg); box-shadow: var(--shadow-1); position: relative; }
.chain-node.ceo { border-color: color-mix(in srgb, var(--leaf) 44%, var(--border)); }
.chain-node .chain-role { color: var(--leaf); font: 600 9.5px/1.3 var(--mono); letter-spacing: .08em; text-transform: uppercase; }
.chain-node .chain-model { display: block; margin-top: 4px; font-size: 13px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chain-node .chain-meta, .chain-node .chain-task { display: block; margin-top: 2px; color: var(--ink-3);
  font: 10.5px/1.45 var(--mono); overflow-wrap: anywhere; }
.chain-node .chain-task { color: var(--ink-2); margin-top: 7px; font-family: var(--sans); }
.chain-daisy { position: absolute; left: -10px; top: -10px; width: 22px; height: 22px; }
.chain-daisy svg { width: 100%; height: 100%; display: block; }
.chain-stem { width: 1px; height: 38px; margin: 0 auto; background: var(--leaf); opacity: .58; }
.chain-branches { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 26px 14px;
  position: relative; padding-top: 28px; }
.chain-branches::before { content: ''; position: absolute; top: 0; left: max(105px, calc(50% / var(--chain-count)));
  right: max(105px, calc(50% / var(--chain-count))); height: 1px; background: var(--leaf); opacity: .48; }
.chain-branch { display: flex; justify-content: center; position: relative; }
.chain-branch::before { content: ''; position: absolute; width: 1px; height: 28px; top: -28px;
  background: var(--leaf); opacity: .48; }
.chain-health { position: absolute; right: 10px; top: 10px; width: 7px; height: 7px; border-radius: 50%;
  background: var(--border-strong); box-shadow: 0 0 0 3px var(--surface-2); }
.chain-health.ready, .chain-health.done { background: var(--pass); }
.chain-health.working { background: var(--daisy-deep); }
.chain-health.failed { background: var(--fail); }
.chain-foot { padding: 10px 13px; border-top: .5px solid var(--border); color: var(--ink-3);
  font: 10.5px/1.45 var(--mono); background: var(--bg); }

"""

CHAIN_NAV = r'''    <div class="navitem" data-view="chain"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 2.2v3.2M8 5.4 3.5 8.2v3.4M8 5.4l4.5 2.8v3.4"/><circle cx="8" cy="2.2" r="1.2"/><circle cx="3.5" cy="12.5" r="1.2"/><circle cx="12.5" cy="12.5" r="1.2"/></svg> Daisy Chain</div>
'''

CHAIN_VIEW = r'''  <!-- ============ DAISY CHAIN ============ -->
  <div class="view" id="view-chain"><div class="page"><div class="pcol">
    <h2>Daisy Chain</h2>
    <div class="lede">One CEO model divides the goal across every usable local model. The branches work in parallel, a peer reviews the synthesis, and Port plus deterministic gates own the verdict.</div>
    <div class="chain-shell">
      <div class="chain-toolbar"><div class="chain-state"><b id="chain-title">Scanning this Mac</b><span id="chain-subtitle">Only agents that answer a live probe appear here.</span></div><button id="chain-refresh">Refresh</button></div>
      <div class="chain-canvas" id="chain-map" aria-live="polite"></div>
      <div class="chain-foot">Port plan → CEO assignments → parallel branches → peer review → deterministic gates</div>
    </div>
  </div></div></div>

'''

CHAIN_VIEW_JS = r'''  var CHAIN_ORG = null, CHAIN_CONTEXT = '';

  function requestChainMap() {
    var map = $('#chain-map');
    if (map) map.innerHTML = '<div class="chain-empty"><b>Scanning this Mac…</b>Waiting for installed agents to answer a live probe.</div>';
    if (!native || !window.webkit || !window.webkit.messageHandlers ||
        !window.webkit.messageHandlers.daisy) {
      CHAIN_STATE = { ready: false, nodes: [], why: 'Open Daisy.app to probe local agent CLIs.' };
      renderChainMap();
      return;
    }
    CHAIN_CONTEXT = 'view';
    window.webkit.messageHandlers.daisy.postMessage({ cmd: 'chain.status' });
  }

  function renderChainMap() {
    var map = $('#chain-map'), title = $('#chain-title'), subtitle = $('#chain-subtitle');
    if (!map) return;
    var state = CHAIN_STATE || { ready: false, nodes: [], why: 'Topology has not been probed yet.' };
    var nodes = state.nodes || [];
    if (!state.ready || nodes.length < 2) {
      title.textContent = 'Chain unavailable';
      subtitle.textContent = state.why || 'At least two usable models are required.';
      map.innerHTML = '<div class="chain-empty"><b>No live organization yet</b>' +
        esc(state.why || 'At least two usable models are required.') + '</div>';
      return;
    }
    title.textContent = nodes.length + ' models · 1 CEO';
    subtitle.textContent = 'Live topology · one accountable root · ' + (nodes.length - 1) + ' direct branches';
    var assignments = {};
    ((CHAIN_ORG && CHAIN_ORG.assignments) || []).forEach(function (row) { assignments[row.agent] = row.task; });
    var workers = (CHAIN_ORG && CHAIN_ORG.workers) || {};
    function nodeCard(node, root) {
      var id = node.id || node.agent;
      var work = workers[id], health = work ? (work.ok ? 'done' : 'failed') : 'ready';
      var model = (node.provider ? node.provider + '/' : '') + (node.model || 'tool default');
      var task = assignments[id] ? '<span class="chain-task">' + esc(assignments[id]) + '</span>' : '';
      return '<div class="chain-node ' + (root ? 'ceo' : '') + '">' +
        (root ? '<span class="chain-daisy" aria-hidden="true">' + DAISY_MARK() + '</span>' : '') +
        '<span class="chain-health ' + health + '" aria-label="' + health + '"></span>' +
        '<span class="chain-role">' + esc(root ? 'CEO' : node.role) + '</span>' +
        '<span class="chain-model">' + esc(model) + '</span>' +
        '<span class="chain-meta">' + esc(node.agent + (node.effort ? ' · ' + node.effort : '') +
          (node.probe_ms ? ' · probe ' + node.probe_ms + ' ms' : '')) + '</span>' + task + '</div>';
    }
    var ceo = nodes[0], peers = nodes.slice(1);
    map.innerHTML = '<div class="chain-tree" style="--chain-count:' + peers.length + '">' +
      '<div class="chain-root">' + nodeCard(ceo, true) + '</div><div class="chain-stem" aria-hidden="true"></div>' +
      '<div class="chain-branches" role="list">' + peers.map(function (node) {
        return '<div class="chain-branch" role="listitem">' + nodeCard(node, false) + '</div>';
      }).join('') + '</div></div>';
  }

'''

RESET_ROW = """    <!-- daisy:chain-reset -->
    <h3>Fresh start</h3>
    <div class="setrow"><div class="lb"><b>Erase local setup</b><span>unlink this Mac from Garden, clear Daisy preferences, and show onboarding again</span></div>
      <button id="reset-daisy">Reset Daisy&hellip;</button></div>

"""

BLANK_RUN = r"""  /* daisy:chain-live */
  var NEW_RUN_EMPTY = true;

  function newBlankRun(focusComposer) {
    clearTimeout(timer);
    clearWorking();
    col.innerHTML = ''; mm.innerHTML = ''; marks = []; idx = 0;
    feed.classList.remove('live');
    following = true; $('#jump').classList.remove('on');
    $('#btn-bell').style.color = '';
    $('#tb-title').textContent = 'New run';
    $$('.thread').forEach(function (t) { t.classList.remove('active'); });
    NEW_RUN_EMPTY = true;
    if (focusComposer !== false) setTimeout(function () { $('#amend').focus(); }, 0);
  }

"""

COMPOSER = r"""  function addBriefToRun(value) {
    var item = document.createElement('div');
    item.className = 'item';
    var brief = document.createElement('div');
    brief.className = 'brief';
    brief.textContent = value;
    item.appendChild(brief); col.appendChild(item); follow();
  }

  function addChainOutcome(data) {
    var item = document.createElement('div');
    item.className = 'item';
    var card = document.createElement('div');
    card.className = 'card';
    var crew = data && data.lanes && data.lanes.crew;
    var gates = (crew && crew.gates) || [];
    var failed = gates.filter(function (g) { return !g.passed; });
    card.innerHTML = '<div class="chead"><span class="ci">DC</span><div><b></b><div class="sub"></div></div>' +
      '<div class="act"><span class="tag"></span></div></div><div class="res" style="display:block"></div>';
    card.querySelector('.chead b').textContent = 'Daisy Chain · ' + (data.run || 'run');
    card.querySelector('.sub').textContent = gates.length + ' deterministic gates · Port ' +
      (((data.governance || {}).mode) || 'unavailable') + ' · ' + (((data.governance || {}).status) || 'unknown');
    var tag = card.querySelector('.tag');
    tag.className = 'tag ' + (failed.length ? 'fail' : 'pass');
    tag.textContent = failed.length ? failed.length + ' FAILED' : 'REVIEWED';
    card.querySelector('.res').textContent = failed.length
      ? failed.map(function (g) { return g.name + (g.detail ? ': ' + g.detail : ''); }).join(' · ')
      : 'The CEO assigned every available peer, reconciled their work, and a peer reviewed it. Release still waits at Port approval.';
    item.appendChild(card); col.appendChild(item); follow();
  }

  window.__daisyChainRun = function (payload) {
    clearWorking();
    var data;
    try { data = typeof payload === 'string' ? JSON.parse(payload) : payload; }
    catch (e) { data = { error: 'Daisy returned an unreadable run summary.' }; }
    if (!data || data.error) {
      toast('<b>Daisy Chain stopped.</b> ' + esc((data && data.error) || 'No run summary returned.'));
      return;
    }
    addChainOutcome(data);
  };

  function addAgentOutcome(data) {
    var item = document.createElement('div');
    item.className = 'item';
    if (!data || !data.ok) {
      var stopped = document.createElement('div');
      stopped.className = 'prose';
      stopped.textContent = (data && data.reason) || 'The selected agent returned no usable response.';
      item.appendChild(stopped); col.appendChild(item); follow();
      return;
    }
    var head = document.createElement('div');
    head.className = 'who-line';
    head.textContent = String(data.agent || 'agent').toUpperCase() + ' · ' + (data.model || 'tool default');
    var prose = document.createElement('div');
    prose.className = 'prose selectable';
    prose.textContent = data.stdout || '';
    item.appendChild(head); item.appendChild(prose); col.appendChild(item); follow();
  }

  window.__daisyAgentRun = function (payload) {
    clearWorking();
    var data;
    try { data = typeof payload === 'string' ? JSON.parse(payload) : payload; }
    catch (e) { data = { ok: false, reason: 'Daisy returned an unreadable agent response.' }; }
    addAgentOutcome(data);
  };

  function markCurrentRun(title) {
    var list = $('.scroller'); if (!list) return;
    list.innerHTML = '';
    var section = document.createElement('div'); section.className = 'sect'; section.textContent = 'Current';
    var row = document.createElement('div'); row.className = 'thread active';
    var pip = document.createElement('span'); pip.className = 'pip run';
    var text = document.createElement('span'); text.className = 'txt'; text.textContent = title;
    row.appendChild(pip); row.appendChild(text); list.appendChild(section); list.appendChild(row);
  }

  function sendAmend() {
    var inp = $('#amend'), v = inp.value.trim(); if (!v) return;
    inp.value = ''; $('#send').classList.remove('ready');
    if (NEW_RUN_EMPTY) {
      NEW_RUN_EMPTY = false;
      var title = v.length > 72 ? v.slice(0, 69) + '…' : v;
      $('#tb-title').textContent = title; markCurrentRun(title);
    }
    addBriefToRun(v);
    if (!native || !window.webkit || !window.webkit.messageHandlers ||
        !window.webkit.messageHandlers.daisy) {
      toast('<b>Open Daisy.app to run local agents.</b> A browser cannot start their CLIs.');
      return;
    }
    if (CHAIN_ON) {
      showWorking('CEO planning · Port commits before agents run');
      window.webkit.messageHandlers.daisy.postMessage({ cmd: 'chain.run', goal: v });
      return;
    }
    var selected = LANES.run;
    if (!selected || !selected.model) {
      toast('<b>No model is selectable.</b> Check agent setup in onboarding.');
      return;
    }
    showWorking('asking ' + (selected.model.label || selected.model.id));
    window.webkit.messageHandlers.daisy.postMessage({
      cmd: 'agent.run', goal: v, vendor: selected.model.vendor,
      model: selected.model.id, effort: selected.effort || '',
      provider: selected.model.provider || ''
    });
  }
"""

EMPTY_WORKSPACE_JS = r"""  /* No demo chats survive into the shipped workspace. */
  function clearDemoWorkspace() {
    var list = $('.scroller');
    if (list) list.innerHTML = '<div class="sect">Your runs</div><div class="sidebar-empty">No runs yet</div>';
    var runs = $('#view-runs .pcol');
    if (runs) runs.innerHTML = '<h2>Runs</h2><div class="lede">No runs yet. Start with a brief; Daisy will show only work this app actually ran.</div>';
  }

"""

RESET_JS = r"""  /* Daisy-owned local reset. A second click prevents accidental unlinking. */
  var resetDaisy = $('#reset-daisy'), resetArmed = false, resetTimer = null;
  if (resetDaisy) resetDaisy.addEventListener('click', function () {
    if (!resetArmed) {
      resetArmed = true; resetDaisy.textContent = 'Click again to reset';
      clearTimeout(resetTimer);
      resetTimer = setTimeout(function () {
        resetArmed = false; resetDaisy.textContent = 'Reset Daisy…';
      }, 5000);
      return;
    }
    resetDaisy.disabled = true; resetDaisy.textContent = 'Resetting…';
    if (window.daisyResetToOnboarding) window.daisyResetToOnboarding();
  });

"""


def _replace_once(html: str, old: str, new: str, label: str) -> str:
    count = html.count(old)
    if count != 1:
        raise ValueError("%s anchor appears %d times, expected 1" % (label, count))
    return html.replace(old, new, 1)


def _add_empty_run(html: str) -> str:
    html = _replace_once(html, "/* ---------- composer ---------- */",
                         EMPTY_RUN_CSS + "/* ---------- composer ---------- */",
                         "empty-run styles")
    feed_anchor = ('      <div class="minimap" id="minimap" role="navigation" '
                   'aria-label="Run outline"></div>\n'
                   '      <div id="feed"')
    html = _replace_once(html, feed_anchor,
                         feed_anchor.split("\n")[0] + "\n" + EMPTY_RUN_HTML +
                         '      <div id="feed"',
                         "empty-run canvas")
    html = _replace_once(html, "  function newBlankRun(focusComposer) {",
                         EMPTY_RUN_JS + "  function newBlankRun(focusComposer) {",
                         "empty-run controller")
    html = _replace_once(html, "    NEW_RUN_EMPTY = true;\n    if (focusComposer !== false)",
                         "    NEW_RUN_EMPTY = true;\n    showEmptyRun();\n    if (focusComposer !== false)",
                         "show empty run")
    html = _replace_once(html, "  function playLive(variant) {\n    NEW_RUN_EMPTY = false;",
                         "  function playLive(variant) {\n    NEW_RUN_EMPTY = false;\n    hideEmptyRun();",
                         "hide empty run on replay")
    html = _replace_once(html, "  function snapshot(id) {\n    NEW_RUN_EMPTY = false;",
                         "  function snapshot(id) {\n    NEW_RUN_EMPTY = false;\n    hideEmptyRun();",
                         "hide empty run on snapshot")
    html = _replace_once(html, "  function addBriefToRun(value) {\n    var item",
                         "  function addBriefToRun(value) {\n    hideEmptyRun();\n    var item",
                         "hide empty run on brief")
    return html


def upgrade(html: str) -> str:
    """Return the fully upgraded app shell; a second call is a byte-for-byte no-op."""
    if GUARD in html:
        return html
    if V2_GUARD in html:
        html = _replace_once(html, "    LANES.run = defaultLane('claude');",
                             "    LANES.run = defaultLane('codex');", "working default model")
        html = html.replace(V2_GUARD, V3_GUARD, 1)
    if V3_GUARD in html:
        html = _replace_once(
            html,
            ".chain-empty { max-width: 460px; margin: 88px auto; text-align: center; color: var(--ink-3); }",
            ".chain-empty { max-width: 460px; margin: 88px auto; color: var(--ink-3); }",
            "chain empty-state alignment")
        html = html.replace(V3_GUARD, V4_GUARD, 1)
    if V4_GUARD in html:
        html = _add_empty_run(html)
        return html.replace(V4_GUARD, GUARD, 1)

    if BASE_GUARD not in html:
        html = _replace_once(
            html,
            ".chain-mi .chain-copy { display: flex;",
            ".chain-mi .chain-copy { font-family: var(--sans); display: flex;",
            "Daisy Chain typography",
        )
        html = html.replace("coordinator → peers → gates",
                            "CEO → every available peer → review → gates")
        html = _replace_once(html, "    <h3>Gates</h3>", RESET_ROW + "    <h3>Gates</h3>",
                             "Settings gates")
        html = _replace_once(html, "  var script = STEPS;\n  function playLive(variant) {",
                             "  var script = STEPS;\n" + BLANK_RUN + "  function playLive(variant) {",
                             "live replay")
        html = _replace_once(html, "  function playLive(variant) {\n    clearTimeout(timer);",
                             "  function playLive(variant) {\n    NEW_RUN_EMPTY = false;\n    clearTimeout(timer);",
                             "sample replay state")
        html = _replace_once(html, "  function snapshot(id) {\n    clearTimeout(timer);",
                             "  function snapshot(id) {\n    NEW_RUN_EMPTY = false;\n    clearTimeout(timer);",
                             "snapshot state")
        replacements = (
            ("$('#tb-new').addEventListener('click', function () { show('run'); playLive(); });",
             "$('#tb-new').addEventListener('click', function () { show('run'); newBlankRun(); });", "toolbar New Run"),
            ("$('#rt-new').addEventListener('click', function () { show('run'); playLive(); });",
             "$('#rt-new').addEventListener('click', function () { show('run'); newBlankRun(); });", "run toolbar New Run"),
            ("if (action === 'new') { show('run'); playLive(); }",
             "if (action === 'new') { show('run'); newBlankRun(); }", "brand New Run"),
            ("show('run'); playLive(); return; }",
             "show('run'); newBlankRun(); return; }", "Command-N"),
            ("  applyTheme();\n  playLive();", "  applyTheme();\n  newBlankRun(false);", "initial run"),
        )
        for old, new, label in replacements:
            html = _replace_once(html, old, new, label)
        html = _replace_once(html, "  /* ---------------- toast ---------------- */",
                             RESET_JS + "  /* ---------------- toast ---------------- */", "toast section")

    composer_pattern = (r"  function addBriefToRun\(value\) \{.*?\n  \}\n"
                        r"(?=  \$\('#send'\)\.addEventListener\('click', sendAmend\);)")
    html, count = re.subn(composer_pattern, lambda _m: COMPOSER, html, count=1, flags=re.S)
    if count != 1:
        raise ValueError("generated composer block was not found exactly once")
    html = _replace_once(html, "/* ---------- overlays ---------- */",
                         CHAIN_CSS + "/* ---------- overlays ---------- */", "chain map styles")
    html = _replace_once(
        html,
        '    <div class="navitem" data-view="runs">',
        CHAIN_NAV + '    <div class="navitem" data-view="runs">',
        "chain sidebar item")
    html = _replace_once(html, "  <!-- ============ RUNS ============ -->",
                         CHAIN_VIEW + "  <!-- ============ RUNS ============ -->", "chain view")
    html = _replace_once(html, "  function menuHTML(s) {",
                         CHAIN_VIEW_JS + "  function menuHTML(s) {", "chain map controller")
    html = _replace_once(
        html,
        "    if (CHAIN_STATE && CHAIN_STATE.ready) {\n      CHAIN_ON = true; saveChain();\n      toast('<b>Daisy Chain ready.</b> ' + CHAIN_STATE.nodes.length +\n            ' probed agents share one Port plan; deterministic gates keep the verdict.');",
        "    renderChainMap();\n    if (CHAIN_STATE && CHAIN_STATE.ready) {\n      if (CHAIN_CONTEXT === 'enable') {\n        CHAIN_ON = true; saveChain();\n        toast('<b>Daisy Chain ready.</b> ' + CHAIN_STATE.nodes.length +\n              ' probed models share one Port plan; deterministic gates keep the verdict.');\n      }",
        "chain status callback")
    html = _replace_once(
        html,
        "      toast('<b>Daisy Chain stayed off.</b> ' + esc((CHAIN_STATE && CHAIN_STATE.why) || 'Two usable agents are required.'));\n    }\n    refreshChainMenu();",
        "      if (CHAIN_CONTEXT === 'enable') toast('<b>Daisy Chain stayed off.</b> ' + esc((CHAIN_STATE && CHAIN_STATE.why) || 'Two usable models are required.'));\n    }\n    CHAIN_CONTEXT = '';\n    refreshChainMenu();",
        "chain status completion")
    html = _replace_once(
        html,
        "    CHAIN_PENDING = true; refreshChainMenu();\n    window.webkit.messageHandlers.daisy.postMessage({ cmd: 'chain.status' });",
        "    CHAIN_PENDING = true; CHAIN_CONTEXT = 'enable'; refreshChainMenu();\n    window.webkit.messageHandlers.daisy.postMessage({ cmd: 'chain.status' });",
        "chain enabling intent")
    html = _replace_once(
        html,
        "  var VIEW_ORDER = ['run','runs','review','precedent','memory','artifacts','autos','skills','import','settings'];",
        "  var VIEW_ORDER = ['run','chain','runs','review','precedent','memory','artifacts','autos','skills','import','settings'];",
        "view order")
    html = _replace_once(html, "      if (name === 'memory') renderMemory();",
                         "      if (name === 'memory') renderMemory();\n      if (name === 'chain') requestChainMap();",
                         "chain view opening")
    html = _replace_once(
        html,
        "    addChainOutcome(data);\n  };",
        "    var crew = data.lanes && data.lanes.crew;\n    if (crew && crew.organization) { CHAIN_ORG = crew.organization; renderChainMap(); }\n    addChainOutcome(data);\n  };",
        "chain run visualization")
    html = _replace_once(html, "  $('#send').addEventListener('click', sendAmend);",
                         "  var chainRefresh = $('#chain-refresh');\n  if (chainRefresh) chainRefresh.addEventListener('click', requestChainMap);\n  $('#send').addEventListener('click', sendAmend);",
                         "chain refresh")
    html = _replace_once(html, "  /* Daisy-owned local reset. A second click prevents accidental unlinking. */",
                         EMPTY_WORKSPACE_JS + "  /* Daisy-owned local reset. A second click prevents accidental unlinking. */",
                         "reset controller")
    html = _replace_once(html, "  applyTheme();\n  newBlankRun(false);",
                         "  applyTheme();\n  clearDemoWorkspace();\n  newBlankRun(false);\n  /* daisy:real-agent-v4 */",
                         "empty initial workspace")
    html = _replace_once(html, "    LANES.run = defaultLane('claude');",
                         "    LANES.run = defaultLane('codex');", "working default model")
    html = _replace_once(
        html,
        "$('#btn-bell').addEventListener('click', function () { closeBrandMenu(); openRun('1042'); toast('<b>1 alert.</b> gate_failures ≥ 2 fired on run 1042 — the escalation card is in the feed.'); });",
        "$('#btn-bell').addEventListener('click', function () { closeBrandMenu(); toast('<b>No alerts.</b> New gate failures will appear here after a real run.'); });",
        "alerts")
    html = re.sub(r"^    \{ l: 'Run brief [AB].*?\n", "", html, flags=re.M)
    html = _add_empty_run(html)
    return html.replace(V4_GUARD, GUARD, 1)


def main(argv=None) -> int:
    with open(IDX, encoding="utf-8") as fh:
        before = fh.read()
    after = upgrade(before)
    if after == before:
        print("Daisy Chain UI already current")
        return 0
    with open(IDX, "w", encoding="utf-8") as fh:
        fh.write(after)
    print("Daisy Chain UI generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
