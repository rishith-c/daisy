#!/usr/bin/env python3
"""Inject the Import view into index.html.

WHAT THE VIEW HAS TO DO
-----------------------
The reference (Codex desktop, Settings -> Import) is three stacked sections: an
autosync toggle, a list of detected apps each with an Import button, and a
"Needs attention" block with tabs and per-item Install buttons. Daisy's version
keeps that shape and points it the other way — into Daisy — with two changes
that the reference does not make and this one has to:

  1. every detected source states what importing it would WRITE, inline, before
     the button. A count of items is not consent; "appends a block to config.md"
     is. Those lines are `Effect` records straight out of importer/detect.py,
     not copy written here.

  2. the button reveals the DRY RUN command first. In a plain browser there is
     no shell, so a button that claimed to import would be lying; and even in
     the native shell the safe default is the dry run. Both problems have the
     same answer: hand the operator the exact command, non-destructive one
     first.

Live data arrives on `window.__daisyImport(payload)` — the same shape as
`python3 -m importer.cli status --json`, and the same pattern `__daisyAgents`
already uses. Until it fires the view shows an honest empty state naming the
command to run. It never invents a detected app, a count, or an attention item.

HOW THIS WORKS
--------------
Five insertions at five anchors, each validated to appear exactly once before
anything is written, so a half-injected index.html is not a state this script
can produce. index.html is never edited by hand.

    python3 tools/add_import_view.py

Running it twice is a no-op: the second run finds its own marker, prints
"already present" and exits 0 without opening the file for writing.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "index.html")

GUARD = "daisy:import-view"

CSS_ANCHOR = "/* ---------- overlays ---------- */"
HTML_ANCHOR = "<!-- ============ APPEARANCE ============ -->"
NAV_ANCHOR_KEY = 'data-view="skills"'
# VIEW_ORDER only sets the direction the view transition slides, but several
# generators write into this one array and they do not run in a fixed order, so
# matching the literal list would break the moment another one lands first.
# Match the array and slot Import in ahead of Settings instead.
ORDER_RE = re.compile(r"(var VIEW_ORDER = \[[^\]]*?)('settings')")

NAV_ITEM = (
    '    <div class="navitem" data-view="import">'
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M8 2.2v7.4"/><path d="M5.2 6.9 8 9.7l2.8-2.8"/>'
    '<path d="M2.8 11.2v1.4a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2v-1.4"/>'
    '</svg> Import</div>')

# ---------------------------------------------------------------------------
# CSS — tokens only, hairline borders, flat wash hovers, no new colour.
#
# Almost everything reuses a component that already exists (.card, .chead,
# .grow, .seg, .tag, .toggle, .derive). What is new here is only what the
# reference layout genuinely needs and the app did not already draw: a row for
# "what this import writes", a row for an unfinished item, and a command strip.
# ---------------------------------------------------------------------------

CSS = """
/* ---------- import: another tool's setup, brought in on purpose ---------- */
.imp-src { margin-bottom: 10px; }
/* Mono + tabular so a column of counts does not jitter as digits change width. */
.imp-n {
  font-family: var(--mono); font-size: 11px; color: var(--ink-3);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
/* One declared effect of pressing Import. Wraps, unlike .grow, because the
   whole point of the line is that it can be read. */
.imp-fx {
  display: flex; gap: 12px; align-items: baseline;
  padding: 8px 13px; font-size: 12.5px;
  transition: background var(--d-hover) var(--out);
}
.imp-fx + .imp-fx, .imp-fx:first-child { border-top: .5px solid var(--border); }
.imp-fx:hover { background: var(--wash); }
.imp-fx .a {
  font-family: var(--mono); font-size: 11px; color: var(--ink-3);
  flex: 0 0 174px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.imp-fx .d { color: var(--ink-2); min-width: 0; }
/* An item a previous import could not finish. */
.imp-att { display: flex; gap: 12px; align-items: flex-start; padding: 11px 13px; }
.imp-att + .imp-att { border-top: .5px solid var(--border); }
.imp-att .t { flex: 1; min-width: 0; }
.imp-att .t b { font-size: 13px; font-weight: 600; display: block; }
.imp-att .t span { display: block; font-size: 12.5px; color: var(--ink-3); margin-top: 2px; }
.imp-att .src {
  font-family: var(--mono); font-size: 10.5px; color: var(--ink-4);
  margin-top: 3px; display: block;
}
.imp-att button { flex: 0 0 auto; }
/* The command strip. Hidden until a button asks for it, because a wall of
   shell is not the resting state of a settings screen. */
.imp-cmd { border-top: .5px solid var(--border); background: var(--surface-2); }
.imp-cmd[hidden] { display: none; }
.imp-cmd .ln { display: flex; align-items: center; gap: 10px; padding: 8px 13px; }
.imp-cmd .ln + .ln { border-top: .5px solid var(--border); }
.imp-cmd code {
  font-family: var(--mono); font-size: 11.5px; color: var(--ink-2);
  white-space: pre; overflow-x: auto; min-width: 0;
}
.imp-cmd .why { font-size: 11.5px; color: var(--ink-4); white-space: nowrap; }
.imp-cmd button { margin-left: auto; height: 26px; padding: 3px 10px; font-size: 12.5px; }
.imp-tabs { margin-bottom: 10px; }
/* .seg draws a pill even with nothing in it, and an empty pill above an
   empty state is a control that does not exist. */
.imp-tabs:empty { display: none; }
.imp-empty { padding: 11px 13px; font-size: 12.5px; color: var(--ink-3); }
"""

# ---------------------------------------------------------------------------
# markup
# ---------------------------------------------------------------------------

# Inserted directly ahead of the settings-view comment, which carries its own
# two-space indent — hence no leading indent on the first line here, and the
# two trailing spaces on the last, so the anchor lands back where it started.
HTML = """<!-- ============ IMPORT ============ -->  <!-- daisy:import-view -->
  <div class="view" id="view-import"><div class="page"><div class="pcol">
    <h2>Import</h2><div class="lede">Bring setup, sessions, rules and MCP servers from the other
      agent tools on this Mac into Daisy. Every source below states what it would write before
      there is anything to press, and nothing moves until you pick one by name.</div>

    <h3>Autosync</h3>
    <div class="setrow"><div class="lb"><b>Keep imports in sync</b><span>Re-scan the sources you have
      already imported and pull what changed. A source you never selected is never pulled in by a
      background pass.</span></div>
      <button class="toggle" id="imp-auto" role="switch" aria-checked="false"
        aria-label="Keep imports in sync"><i></i></button></div>
    <div class="setrow"><div class="lb"><b>Last run</b><span>one watermark per source, so a resync
      moves only what moved</span></div>
      <span class="gv" id="imp-lastrun">never</span></div>
    <div class="setrow"><div class="lb"><b>Equivalent command</b><span>there is no daemon &mdash; the
      app calls this once, and so can you</span></div>
      <code class="gv" id="imp-synccmd">importer.cli sync --once</code></div>

    <h3>Import from another agent tool</h3>
    <div class="lede" style="margin:-2px 0 10px">Detected setup that can be added to Daisy, and what
      each one would land.</div>
    <div id="imp-list"></div>

    <h3>Needs attention</h3>
    <div class="lede" style="margin:-2px 0 10px">Finish setting up items from a previous import
      &mdash; a skill missing a file it points at, a server configured but not authenticated, a rules
      file citing a path that is not there.</div>
    <div class="seg imp-tabs" id="imp-tabs" role="tablist"></div>
    <div id="imp-attn"></div>
  </div></div></div>

  """

# ---------------------------------------------------------------------------
# script — self-contained, appended as its own block at end of file
# ---------------------------------------------------------------------------

JS = """<script>
/* ---------------- import view ----------------  daisy:import-view
   Live data arrives on window.__daisyImport(payload), the shape printed by
   `python3 -m importer.cli status --json`. A browser cannot read ~, so until
   that call lands this view says so rather than drawing a plausible list of
   apps it has not detected. Every button reveals the command that does the
   work, dry run first, because that is the honest affordance in a page with no
   shell behind it — and the safe default even when there is one. */
(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var IMP = null, OPEN = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  var MARKS = {
    claude: '<path d="M8 1.9v12.2M1.9 8h12.2M3.7 3.7l8.6 8.6M12.3 3.7l-8.6 8.6"/>',
    codex: '<path d="M8 1.8 13.6 5v6L8 14.2 2.4 11V5L8 1.8Z" stroke-linejoin="round"/>',
    opencode: '<path d="M6 3.2 2.6 8 6 12.8M10 3.2 13.4 8 10 12.8" stroke-linecap="round" stroke-linejoin="round"/>',
    cursor: '<path d="M3.4 2.6 8.2 13.4l1.6-4 4-1.6L3.4 2.6Z" stroke-linejoin="round"/>',
    agents: '<circle cx="6" cy="8" r="3.9"/><circle cx="10" cy="8" r="3.9"/>',
    mixed: '<path d="M4 2.4h5.2L12 5.2v8.4H4V2.4Z" stroke-linejoin="round"/><path d="M9 2.4v3h3"/>'
  };
  function mark(tool) {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">' +
      (MARKS[tool] || MARKS.mixed) + '</svg>';
  }

  function cmdStrip(id, lines) {
    var body = lines.map(function (l) {
      return '<div class="ln"><code>' + esc(l[0]) + '</code>' +
        (l[1] ? '<span class="why">' + esc(l[1]) + '</span>' : '') +
        '<button data-copy="' + esc(l[0]) + '">Copy</button></div>';
    }).join('');
    return '<div class="imp-cmd" id="' + esc(id) + '"' + (OPEN[id] ? '' : ' hidden') + '>' +
      body + '</div>';
  }

  function sourceCard(s) {
    var done = s.imported || 0, n = s.count || 0;
    var tag = '';
    if (!s.importable) tag = '<span class="tag info">DETECTED ONLY</span>';
    else if (n && done >= n) tag = '<span class="tag pass">IMPORTED</span>';
    else if (done) tag = '<span class="tag info">' + done + ' OF ' + n + '</span>';

    var cid = 'imp-cmd-' + s.id;
    var btn = s.importable && n
      ? '<button class="primary" data-imp="' + esc(s.id) + '">Import</button>'
      : '<button disabled>Import</button>';
    var fx = (s.effects || []).map(function (e) {
      return '<div class="imp-fx"><span class="a">' + esc(e.action) + ' &rarr; ' +
        esc(e.target) + '</span><span class="d">' + esc(e.detail) + '</span></div>';
    }).join('');
    if (!fx && s.note) {
      fx = '<div class="imp-fx"><span class="a">not imported</span>' +
           '<span class="d">' + esc(s.note) + '</span></div>';
    }
    return '<div class="card imp-src"><div class="chead"><span class="ci">' + mark(s.tool) +
      '</span><div style="min-width:0"><b>' + esc(s.label) + '</b><div class="sub">' +
      esc(s.path) + '</div></div><div class="act"><span class="imp-n">' +
      (n ? n + (n === 1 ? ' item' : ' items') : 'nothing found') + '</span>' + tag + btn +
      '</div></div>' + fx +
      cmdStrip(cid, [
        ['python3 -m importer.cli import --source ' + s.id, 'dry run — writes nothing'],
        ['python3 -m importer.cli import --source ' + s.id + ' --apply', 'writes it']
      ]) + '</div>';
  }

  function emptyCard() {
    return '<div class="card"><div class="chead"><span class="ci">' + mark('mixed') +
      '</span><div style="min-width:0"><b>Nothing detected yet</b><div class="sub">' +
      'a page in a browser cannot read <code>~</code></div></div></div>' +
      '<div class="imp-cmd"><div class="ln"><code>python3 -m importer.cli detect</code>' +
      '<span class="why">read-only</span>' +
      '<button data-copy="python3 -m importer.cli detect">Copy</button></div></div>' +
      '<div class="res">This view fills in the moment <code>window.__daisyImport</code> is called ' +
      'with the output of <code>python3 -m importer.cli status --json</code>. Until then it lists ' +
      'nothing: no detected apps, no counts, no attention items. A list of tools it has not looked ' +
      'for would be a guess dressed as a finding.</div></div>';
  }

  function attnRow(it) {
    var cid = 'imp-fix-' + String(it.id).replace(/[^A-Za-z0-9]+/g, '-');
    return '<div class="imp-att"><span class="t"><b>' + esc(it.title) + '</b><span>' +
      esc(it.detail) + '</span><span class="src">' + esc(it.tab.toLowerCase()) +
      ' &middot; ' + esc(it.source || 'imported') + '</span></span>' +
      '<button data-fix="' + esc(cid) + '">Finish</button></div>' +
      cmdStrip(cid, [[it.fix, '']]);
  }

  function paint() {
    var list = $('#imp-list'), attn = $('#imp-attn'), tabs = $('#imp-tabs');
    if (!list) return;

    if (!IMP) {
      list.innerHTML = emptyCard();
      tabs.innerHTML = '';
      attn.innerHTML = '<div class="card"><div class="imp-empty">Nothing has been imported on this ' +
        'machine yet, so there is nothing half-finished to report.</div></div>';
      return;
    }

    list.innerHTML = (IMP.sources || []).map(sourceCard).join('');

    var a = IMP.attention || { tabs: [], by_tab: {}, total: 0 };
    tabs.innerHTML = (a.tabs || []).map(function (t, i) {
      return '<button role="tab" data-tab="' + esc(t.name) + '"' +
        (i === 0 ? ' class="on" aria-selected="true"' : ' aria-selected="false"') + '>' +
        esc(t.name) + ' ' + t.count + '</button>';
    }).join('');
    paintTab((a.tabs && a.tabs[0] && a.tabs[0].name) || '');
  }

  function paintTab(name) {
    var a = (IMP && IMP.attention) || { by_tab: {} };
    var rows = (a.by_tab && a.by_tab[name]) || [];
    $('#imp-attn').innerHTML = '<div class="card">' + (rows.length
      ? rows.map(attnRow).join('')
      : '<div class="imp-empty">Nothing outstanding under ' + esc(name || 'this tab') +
        '. Everything imported here is complete and reachable.</div>') + '</div>';
  }

  function syncUI() {
    var t = $('#imp-auto'), on = t && t.getAttribute('aria-checked') === 'true';
    var c = $('#imp-synccmd');
    if (c) c.textContent = 'importer.cli sync ' + (on ? '--on' : '--off');
  }

  /* The native shell answers with live data; a browser keeps the honest blank. */
  window.__daisyImport = function (p) {
    try {
      var d = typeof p === 'string' ? JSON.parse(p) : p;
      if (!d || !d.sources) return;
      IMP = d;
      var t = $('#imp-auto');
      if (t && d.sync) t.setAttribute('aria-checked', d.sync.enabled ? 'true' : 'false');
      var lr = $('#imp-lastrun');
      if (lr && d.sync) lr.textContent = d.sync.last_run_human || 'never';
      syncUI();
      paint();
    } catch (e) { /* keep the blank state; a bad payload is not a detection */ }
  };

  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target.closest('button') : null;
    if (!t) return;

    if (t.id === 'imp-auto') {
      var on = t.getAttribute('aria-checked') !== 'true';
      t.setAttribute('aria-checked', on ? 'true' : 'false');
      syncUI();
      return;
    }
    var copy = t.getAttribute('data-copy');
    if (copy) {
      if (navigator.clipboard) navigator.clipboard.writeText(copy);
      var was = t.textContent; t.textContent = 'Copied';
      setTimeout(function () { t.textContent = was; }, 1200);
      return;
    }
    var tab = t.getAttribute('data-tab');
    if (tab) {
      var all = t.parentNode.querySelectorAll('button');
      for (var i = 0; i < all.length; i++) {
        var sel = all[i] === t;
        all[i].classList.toggle('on', sel);
        all[i].setAttribute('aria-selected', sel ? 'true' : 'false');
      }
      paintTab(tab);
      return;
    }
    var src = t.getAttribute('data-imp');
    var fix = t.getAttribute('data-fix');
    var id = src ? 'imp-cmd-' + src : fix;
    if (!id) return;
    var box = document.getElementById(id);
    if (!box) return;
    OPEN[id] = box.hidden;
    box.hidden = !box.hidden;
  });

  var el = document.getElementById('imp-auto');
  if (el) { syncUI(); paint(); }

  if (navigator.userAgent.indexOf('DaisyNative') !== -1 && window.webkit &&
      window.webkit.messageHandlers && window.webkit.messageHandlers.daisy) {
    try { window.webkit.messageHandlers.daisy.postMessage({ cmd: 'import' }); } catch (e) {}
  }
})();
</script>
"""


def main(argv):
    h = open(IDX, encoding="utf-8").read()
    if GUARD in h:
        print("already present")
        return 0

    nav_anchor = next((ln for ln in h.split("\n") if NAV_ANCHOR_KEY in ln), None)
    if nav_anchor is None:
        raise SystemExit("nav anchor %r not found" % NAV_ANCHOR_KEY)

    # Validate everything before writing anything: a partially injected
    # index.html must not be a state this script can leave behind.
    for anchor, what in ((CSS_ANCHOR, "stylesheet"), (HTML_ANCHOR, "settings view"),
                         (nav_anchor, "sidebar")):
        n = h.count(anchor)
        if n != 1:
            raise SystemExit("anchor for the %s appears %d times, expected 1" % (what, n))
    n = len(ORDER_RE.findall(h))
    if n != 1:
        raise SystemExit("anchor for the view order appears %d times, expected 1" % n)

    h = h.replace(CSS_ANCHOR, CSS.strip("\n") + "\n\n" + CSS_ANCHOR, 1)
    h = h.replace(nav_anchor, nav_anchor + "\n" + NAV_ITEM, 1)
    h = h.replace(HTML_ANCHOR, HTML + HTML_ANCHOR, 1)
    h = ORDER_RE.sub(r"\1'import',\2", h, count=1)
    h = h.rstrip("\n") + "\n\n" + JS

    open(IDX, "w", encoding="utf-8").write(h)
    print("import view injected — %d lines of css, %d of markup, %d of script"
          % (CSS.strip("\n").count("\n") + 1, HTML.count("\n") + 1, JS.count("\n") + 1))
    print("live data:  python3 -m importer.cli status --json  ->  window.__daisyImport(payload)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
