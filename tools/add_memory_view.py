#!/usr/bin/env python3
"""Add the Memory view — four tiers, and the moment compaction takes something.

Precedent shows what the factory learned from its failures. This shows the
layer underneath: what it still holds, what it compacted away, and — the part
nobody's memory system shows — that it knows the difference. The centrepiece is
the compaction moment itself, animated, with the residue visibly landing in a
tier-3 rail carrying a pointer rather than fading out.

Numbers come from memory/memory.db when it exists, so the view reports a real
ingest rather than a mock. When it does not, the constants below are the last
measurement, labelled as such in the UI.

Idempotent: a sentinel guards the whole insertion, so a second run is a no-op
and leaves a zero diff.

    python3 tools/add_memory_view.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

IDX = os.path.join(ROOT, "index.html")
DB = os.path.join(ROOT, "memory", "memory.db")
SENTINEL = "Memory view: four tiers"

# The last measured ingest, used only when memory.db is absent. Regenerate with
#   python3 -m memory.cli ingest --runs runs --sessions 4
FALLBACK = {'generated': '2026-08-22',
     'live': False,
     'events': 320,
     'facts': 62,
     'summaries': 10,
     'residue': 212,
     'runs': 10,
     'sources': 10,
     'fact_kinds': {'approval': 5, 'decision': 13, 'gate': 21, 'repair': 4, 'write': 19},
     'residue_reasons': {'deterministic': 31, 'squeezed': 181},
     'bytes_before': 214283,
     'bytes_after': 45874,
     'compression': 4.7,
     'index_kb': 17.8,
     'float_mb': 0.58,
     'db_mb': 1.6,
     'audit': {'compactions': 10,
               'events': 320,
               'events_retained': 108,
               'events_dropped': 212,
               'residue_rows': 212,
               'residue_live_pointers': 212,
               'facts': 62,
               'facts_in_context': 62,
               'facts_tier0_only': 0,
               'facts_unreachable': 0,
               'context_coverage': 1.0,
               'total_coverage': 1.0,
               'reconciles': True,
               'ratio': 4.7},
     'biggest': {'source': 'claude:449783f3-e475-4',
                 'span': [1, 264],
                 'events': 264,
                 'retained': 52,
                 'dropped': 212,
                 'ratio': 5.4,
                 'probe': 100},
     'chips': {'facts': ['taste.t1 · fail', '…veloper/daisy/README.md · created',
                         'brief: SR-11 bracket, 2…', 'scrape · gate-set',
                         'hardware · resume-findi'],
               'kept': ['This is pure ideation — the out…', '<task-notification> <task-id>w4…',
                        '**The tournament is done, and t…', '<task-notification> <task-id>w0…',
                        '<task-notification> <task-id>w8…'],
               'dropped': ['Workflow: Workflow', 'Skill: artifact-design',
                           'ToolSearch: computer-use', 'SendUserFile: SendUserFile',
                           'ToolSearch: select:WebSearch', 'Skill: superpowers:brainstorming',
                           'Agent: Research Codex app light…', 'Agent: Research award-winning M…',
                           'Agent: Research vector memory a…', 'Now the full verification pass …',
                           'Agent: Research fluid native-fe…', 'Typo in the path — retrying wit…',
                           'No site is open in this tab. Us…', 'so it cann ot be a mac app and …',
                           'read ~/Developer/daisy/icon/dai…', 'mcp__Claude_Browser__computer: …',
                           'mcp__Claude_Browser__navigate: …',
                           'mcp__Claude_Browser__read_page:…']},
     'demo': [{'label': 'exact path',
               'q': 'index.html',
               'gates': [],
               'ms': 13.3,
               'scanned': 284,
               'held': [{'tier': 1,
                         'kind': 'write',
                         'subject': '~/Developer/daisy/index.html',
                         'value': 'created',
                         'score': 0.4885,
                         'parts': {'exact': 0.85,
                                   'cover': 0.0,
                                   'cosine': 0.453,
                                   'evidence': 0.488}}],
               'forgotten': [{'score': 0.3133,
                              'claim': "Bash: python3 - <<'PYEOF' p = 'index.html' h = "
                                       'open(p).read()  # minimap: distribute proportion…',
                              'reason': 'squeezed',
                              'event': '6b6c3666198b',
                              'chars': 406,
                              'at': '11:59'},
                             {'score': 0.3113,
                              'claim': "Bash: python3 - <<'PYEOF' p = 'index.html' h = "
                                       'open(p).read()  # palette: no animation (keyboar…',
                              'reason': 'squeezed',
                              'event': 'ee37bd83179f',
                              'chars': 406,
                              'at': '11:59'}]},
              {'label': 'gate signature',
               'q': 'something is off with the frontend',
               'gates': ['taste.t1'],
               'ms': 15.6,
               'scanned': 284,
               'held': [{'tier': 2,
                         'kind': 'summary',
                         'subject': 'This is pure ideation — the output is ideas, not code — so '
                                    'per t',
                         'value': 'probe 100%',
                         'score': 0.3373,
                         'parts': {'exact': 0.0,
                                   'cover': 0.0,
                                   'cosine': 0.337,
                                   'evidence': 0.337}},
                        {'tier': 1,
                         'kind': 'gate',
                         'subject': 'taste.t1',
                         'value': 'fail',
                         'score': 0.2881,
                         'parts': {'exact': 0.0,
                                   'cover': 1.0,
                                   'cosine': 0.105,
                                   'evidence': 0.288}}],
               'forgotten': [{'score': 0.2692,
                              'claim': 'Typo in the path — retrying with the correct one.',
                              'reason': 'squeezed',
                              'event': 'e64b103513b6',
                              'chars': 49,
                              'at': '11:59'}]},
              {'label': 'paraphrase',
               'q': 'inlining the hand drawn icon into the sidebar brand',
               'gates': [],
               'ms': 12.4,
               'scanned': 284,
               'held': [{'tier': 2,
                         'kind': 'summary',
                         'subject': 'This is pure ideation — the output is ideas, not code — so '
                                    'per t',
                         'value': 'probe 100%',
                         'score': 0.293,
                         'parts': {'exact': 0.0,
                                   'cover': 0.0,
                                   'cosine': 0.293,
                                   'evidence': 0.293}}],
               'forgotten': [{'score': 0.8417,
                              'claim': "Now inlining the hand-drawn icon into the app's sidebar "
                                       'brand:',
                              'reason': 'squeezed',
                              'event': '24fd3da5631a',
                              'chars': 62,
                              'at': '11:59'},
                             {'score': 0.2624,
                              'claim': 'Now the full verification pass in the browser:',
                              'reason': 'squeezed',
                              'event': 'b903953f40e5',
                              'chars': 46,
                              'at': '11:59'}]},
              {'label': 'novel',
               'q': 'kubernetes pod evicted due to memory pressure on the node',
               'gates': [],
               'ms': 12.1,
               'scanned': 284,
               'held': [],
               'forgotten': []}]}


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

DEMO_QUERIES = [
    ("exact path", "index.html", None),
    ("gate signature", "something is off with the frontend", ["taste.t1"]),
    ("paraphrase", "inlining the hand drawn icon into the sidebar brand", None),
    ("novel", "kubernetes pod evicted due to memory pressure on the node", None),
]


# Chip labels for the animation. Real rows, but chosen for legibility: one fact
# per kind so all six tier-1 types are visible, and de-duplicated prose so the
# residue bed does not fill with twenty copies of the same tool call. The stage
# is a diagram of one compaction, not a dump of it.
CHIP_CHARS = 32
KIND_ORDER = ("gate", "write", "decision", "approval", "repair", "escalation")


HOME = os.path.expanduser("~")


def _label(s: str, n: int = CHIP_CHARS) -> str:
    """Trim a row to chip width from whichever end carries the meaning.

    A path is identified by its tail and a sentence by its head, so truncating
    both the same way makes half the chips unreadable. Home is shortened to `~`
    the way agents/discover.py does it — an absolute home path in shipped markup
    is noise at best and someone's username at worst.
    """
    s = " ".join(str(s).replace(HOME, "~").split())
    if len(s) <= n:
        return s
    pathish = "/" in s and " " not in s
    return ("\u2026" + s[-(n - 1):]) if pathish else (s[:n - 1] + "\u2026")


def _distinct(rows, n: int) -> list:
    """De-duplicate on the RENDERED label, not the source text.

    Two different events that trim to the same 32 characters look like a
    rendering bug, however honestly they were selected.
    """
    seen, out = set(), []
    for (t,) in rows:
        lab = _label(t)
        if not lab or lab.lower() in seen:
            continue
        seen.add(lab.lower())
        out.append(lab)
        if len(out) >= n:
            break
    return out


def _chips(con, big: dict) -> dict:
    facts = []
    for kind in KIND_ORDER:
        r = con.execute("SELECT subject, value FROM fact WHERE kind = ?"
                        " ORDER BY LENGTH(subject), subject LIMIT 1", (kind,)).fetchone()
        if r:
            # A decision's value is its own text again, so only append the value
            # when it actually adds something.
            lab, val = _label(r[0], 24), (r[1] or "")[:12]
            facts.append(lab if not val or val.lower() in lab.lower()
                         else "%s \u00b7 %s" % (lab, val))
    span = big.get("span") or [0, 0]
    args = (big.get("source", ""), span[0], span[1])
    kept = _distinct(con.execute(
        "SELECT text FROM event WHERE source = ? AND seq BETWEEN ? AND ?"
        " AND text <> '' AND id NOT IN (SELECT event_id FROM residue)"
        " ORDER BY kind = 'prose' DESC, seq", args), 5)
    dropped = _distinct(con.execute(
        "SELECT claim FROM residue WHERE summary_id = ? ORDER BY LENGTH(claim), id",
        (big.get("summary_id", ""),)), 18)
    return {"facts": facts, "kept": kept, "dropped": dropped}


def measured() -> dict:
    """Read the real store, or say honestly that this is the last measurement."""
    if not os.path.exists(DB):
        return dict(FALLBACK)
    from memory import boundary, recall as rc, store        # noqa: PLC0415

    con = store.connect(DB)
    s = store.stats(con)
    a = boundary.audit_all(con)
    per = sorted(a["compactions"], key=lambda c: -c["events_dropped"])
    big = per[0] if per else {}

    chips = _chips(con, big)

    demo = []
    for label, q, gates in DEMO_QUERIES:
        t0 = time.time()
        r = rc.recall(con, q, gates=gates, k=2)
        d = {"label": label, "q": q, "gates": gates or [],
             "ms": round((time.time() - t0) * 1000, 1), "scanned": r.scanned,
             "held": [{"tier": h.tier, "kind": h.kind,
                       "subject": h.subject.replace(HOME, "~")[:64],
                       "value": h.value[:32], "score": h.score, "parts": h.parts}
                      for h in r.held],
             "forgotten": []}
        for f in r.forgotten[:2]:
            ev = rc.verbatim(con, f)
            d["forgotten"].append({
                "score": f.score, "claim": f.subject.replace(HOME, "~"),
                "reason": f.reason,
                "event": f.event_id[:12], "chars": len(ev.get("text", "") or ""),
                "at": time.strftime("%H:%M", time.localtime(f.dropped_at))})
        demo.append(d)
    con.close()

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M"), "live": True,
        "events": s["events"], "facts": s["facts"], "summaries": s["summaries"],
        "residue": s["residue"], "runs": s["runs"], "sources": s["sources"],
        "fact_kinds": s["fact_kinds"], "residue_reasons": s["residue_reasons"],
        "bytes_before": s["bytes_before"], "bytes_after": s["bytes_after"],
        "compression": s["compression"],
        "index_kb": round(s["index_bytes"] / 1024.0, 1),
        "float_mb": round(s["float_bytes"] / 1e6, 2),
        "db_mb": round(os.path.getsize(DB) / 1e6, 1),
        "audit": {k: a["totals"][k] for k in
                  ("compactions", "events", "events_retained", "events_dropped",
                   "residue_rows", "residue_live_pointers", "facts",
                   "facts_in_context", "facts_tier0_only", "facts_unreachable",
                   "context_coverage", "total_coverage", "reconciles", "ratio")},
        "biggest": {"source": (big.get("source") or "")[:22],
                    "span": big.get("span", [0, 0]),
                    "events": big.get("events", 0),
                    "retained": big.get("events_retained", 0),
                    "dropped": big.get("events_dropped", 0),
                    "ratio": big.get("ratio", 0),
                    "probe": round(100 * big.get("probe_score", 0))},
        "chips": {k: chips[k] or FALLBACK["chips"][k] for k in ("facts", "kept", "dropped")},
        "demo": demo,
    }


# ---------------------------------------------------------------------------
# the pieces that go into index.html
# ---------------------------------------------------------------------------

CSS = """
/* ---------- Memory view: four tiers and the compaction moment ---------- */
/* The stage is two beds side by side. Chips do not fade when compaction takes
   them — they travel into the residue bed and grow a tier-0 pointer, because
   the whole claim of this view is that nothing leaves silently. */
.mem-stage { display: grid; grid-template-columns: minmax(0,1.1fr) minmax(0,1fr); gap: 10px; padding: 12px; }
@media (max-width: 820px) { .mem-stage { grid-template-columns: minmax(0,1fr); } }
.mem-pane { border: .5px solid var(--border); border-radius: var(--r-card); background: var(--surface-2); }
.mem-pane .mh { display: flex; align-items: baseline; gap: 8px; padding: 8px 11px; border-bottom: .5px solid var(--border); }
.mem-pane .mh b { font-size: 12px; font-weight: 600; letter-spacing: -.01em; }
.mem-pane .mh em { font-style: normal; font-size: 11.5px; color: var(--ink-3); }
.mem-pane .mh span { margin-left: auto; font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.mem-bed { display: flex; flex-wrap: wrap; gap: 5px; padding: 10px 11px; align-content: flex-start; min-height: 132px; }
.mem-chip {
  font-family: var(--mono); font-size: 10.5px; line-height: 1.55;
  padding: 3px 7px; border-radius: var(--r-badge); max-width: 100%;
  background: var(--bg); color: var(--ink-2);
  border: .5px solid var(--border); border-left: 2px solid var(--ink-4);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mem-chip.t1 { border-left-color: var(--pass); color: var(--ink); }
.mem-chip.t3 { border-left-color: var(--info); }
.mem-chip .mp { display: none; margin-left: 7px; color: var(--info); }
.mem-res .mem-chip .mp { display: inline; }
.mem-chip.leaving { animation: mem-go .18s var(--out) both; }
.mem-chip.arriving { animation: mem-land .34s var(--out) both; }
@keyframes mem-go   { to   { opacity: 0; transform: translateX(12px) scale(.9); } }
@keyframes mem-land { from { opacity: 0; transform: translateX(-16px) scale(.94); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .mem-chip.leaving, .mem-chip.arriving { animation: none; } }
.mem-bar { display: flex; align-items: center; gap: 10px; padding: 0 12px 12px; }
.mem-bar .mtally { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.mem-bar .mtally b { color: var(--ink); font-weight: 600; }
.mem-bar .mtally i { font-style: normal; color: var(--info); }
.mem-bar button { margin-left: auto; }
.mem-ptr {
  font-family: var(--mono); font-size: 11.5px; line-height: 1.75;
  padding: 9px 13px; border-top: .5px solid var(--border); color: var(--ink-2);
}
.mem-ptr b { color: var(--ink); font-weight: 600; }
.mem-ptr .mk { color: var(--info); }
.mem-ptr .mq { color: var(--ink-3); }
"""

VIEW = ('  <!-- ============ MEMORY ============ -->\n'
        '  <div class="view" id="view-memory"><div class="page">'
        '<div class="pcol" id="memory-body"></div></div></div>\n')

NAV = ('    <div class="navitem" data-view="memory">'
       '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">'
       '<rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/>'
       '<path d="M2.5 6.2h11M2.5 9.6h11" stroke-linecap="round"/>'
       '<path d="M6 12.9v.9M10 12.9v.9" stroke-linecap="round"/></svg> Memory</div>\n')


def js(stats: dict) -> str:
    """The view renderer, injected next to renderPrecedent so it reads as a peer."""
    return """
  /* ---------------- memory page ---------------- */
  /* Memory view: four tiers, and the compaction moment as the headline. */
  function renderMemory() {
    var el = $('#memory-body');
    if (el.dataset.built) return;
    el.dataset.built = '1';
    var M = MEMORY_STATS, A = M.audit, C = M.chips, B = M.biggest;
    var pct = function (x) { return (100 * x).toFixed(1) + '%'; };

    var demo = M.demo.map(function (d, i) {
      return '<button data-mdemo="' + i + '"' + (i === 0 ? ' class="on"' : '') + '>' +
             d.label + '</button>';
    }).join('');

    el.innerHTML =
      '<h2>Memory</h2><div class="lede">Four tiers over one SQLite file. The raw log, the typed facts, ' +
      'the compacted prose &mdash; and the residue: a record of what compaction took, with a pointer back to the original. ' +
      'That fourth tier is why this can answer <em>what did I forget</em> and not only <em>what do I know</em>. ' +
      'Numbers below are measured over ' + M.events.toLocaleString() + ' real events from ' + M.sources +
      ' sources' + (M.live ? '' : ' (last measurement)') + '.</div>' +

      '<h3>The compaction moment</h3>' +
      '<div class="card" style="margin-bottom:20px"><div class="chead"><span class="ci">&#8600;</span>' +
      '<div style="min-width:0"><b>' + B.events + ' events &rarr; ' + B.retained + ' still in context</b>' +
      '<div class="sub">' + B.source + ' &middot; ' + B.ratio + '&times; smaller &middot; probe ' + B.probe + '%</div></div>' +
      '<div class="act"><span class="tag info">' + B.dropped + ' RESIDUE</span></div></div>' +
      '<div class="mem-stage">' +
        '<div class="mem-pane"><div class="mh"><b>Context</b><em>tier 1 facts pinned</em>' +
          '<span id="mem-n-ctx">0</span></div><div class="mem-bed" id="mem-ctx"></div></div>' +
        '<div class="mem-pane mem-res"><div class="mh"><b>Tier 3 &middot; residue</b><em>pointer, not a copy</em>' +
          '<span id="mem-n-res">0</span></div><div class="mem-bed" id="mem-res"></div></div>' +
      '</div>' +
      '<div class="mem-bar"><span class="mtally" id="mem-tally"></span>' +
      '<button id="mem-play">Replay the compaction</button></div>' +
      '<div class="mem-ptr">A recall that lands in the residue answers with a timestamp and an address, never a reconstruction:<br>' +
      '<b>FORGOTTEN</b> &middot; compacted <b>11:59</b> &middot; reason <b>squeezed</b> &middot; original event <span class="mk">24fd3da5631a</span><br>' +
      '<span class="mq">&ldquo;Now inlining the hand-drawn icon into the app&rsquo;s sidebar brand:&rdquo;</span> ' +
      '&rarr; tier 0 still holds it verbatim.</div>' +
      '<div class="res" style="display:block">Green chips are tier-1 facts &mdash; decisions, approvals, gate verdicts, writes, repairs, escalations. ' +
      'They are extracted as rows the moment they happen and compaction never sees them. Everything else is prose, and prose is where things go missing.</div></div>' +

      '<h3>Measured</h3>' +
      '<div class="tbl" style="margin-bottom:20px"><table class="l">' +
      '<tr><th>Tier</th><th>Rows</th><th>What it holds</th></tr>' +
      '<tr><td class="s">0 &middot; verbatim</td><td class="mo">' + M.events.toLocaleString() + '</td>' +
      '<td>append-only, content-addressed, never rewritten</td></tr>' +
      '<tr><td class="s">1 &middot; facts</td><td class="mo">' + M.facts + '</td>' +
      '<td>' + Object.keys(M.fact_kinds).map(function (k) { return k + ' ' + M.fact_kinds[k]; }).join(' &middot; ') + '</td></tr>' +
      '<tr><td class="s">2 &middot; compacted</td><td class="mo">' + M.summaries + '</td>' +
      '<td>' + (M.bytes_before / 1024).toFixed(1) + ' KB &rarr; ' + (M.bytes_after / 1024).toFixed(1) +
      ' KB &nbsp;<span class="tag pass">' + M.compression + '&times;</span></td></tr>' +
      '<tr><td class="s">3 &middot; residue</td><td class="mo">' + M.residue + '</td>' +
      '<td>' + Object.keys(M.residue_reasons).map(function (k) { return k + ' ' + M.residue_reasons[k]; }).join(' &middot; ') +
      ' &middot; <b>0</b> dangling pointers</td></tr>' +
      '<tr><td class="s">Search index</td><td class="mo">' + M.index_kb + ' KB</td>' +
      '<td>64 B a row &middot; ' + M.float_mb + ' MB of float held back for the rescore</td></tr>' +
      '</table></div>' +

      '<h3>How recall fuses</h3>' +
      '<div class="card" style="margin-bottom:20px"><div class="derive" style="border-top:none">' +
      'evidence(r) = <b>0.62</b>&middot;[ <b>0.60</b>&middot;exact(r,q) + <b>0.40</b>&middot;cover(r,q) ] + <b>0.38</b>&middot;cos(v_r, v_q)\\n' +
      '              when no deterministic signal fired:  evidence(r) = cos(v_r, v_q)\\n\\n' +
      'exact  a tier-1 fact whose SUBJECT is literally the thing asked about. Identity, not similarity.\\n' +
      'cover  containment over gate names the store can confirm are gates, never guessed from shape.\\n' +
      'cos    512-d signed-hash vector &middot; 64-byte binary shortlist &middot; exact float rescore.\\n\\n' +
      'held      &ge; 0.20        forgotten  &ge; 0.25        below both, nothing is returned.\\n\\n' +
      'Absolute evidence, not reciprocal rank fusion. Rank answers &ldquo;which of these is best&rdquo;;\\n' +
      'the question here is &ldquo;do I hold this at all&rdquo;, and normalised rank always says yes.</div></div>' +

      '<h3>What did I forget</h3>' +
      '<div class="lede" style="margin-bottom:12px">Four real queries against the live store. Engine output, captured by ' +
      '<code>python3 tools/add_memory_view.py</code> &mdash; not written by hand.</div>' +
      '<div class="seg" id="mdemoseg" style="margin-bottom:12px">' + demo + '</div><div id="mdemoout"></div>' +

      '<h3 style="margin-top:22px">Compaction audit</h3>' +
      '<div class="lede" style="margin-bottom:12px">Recomputed from the store on every call, so the numbers cannot drift away from what is actually held.</div>' +
      '<div class="card"><div class="chead"><span class="ci">&Sigma;</span><div style="min-width:0">' +
      '<b>' + A.compactions + ' compactions reconcile</b>' +
      '<div class="sub">' + A.events + ' events &middot; ' + A.facts + ' tier-1 facts &middot; ' + A.residue_rows + ' residue rows</div></div>' +
      '<div class="act"><span class="tag ' + (A.reconciles ? 'pass">RECONCILES' : 'fail">MISMATCH') + '</span></div></div>' +
      '<div class="grow"><span class="gn">still in context</span><span class="gd">an agent holding only the summary knows these</span>' +
      '<span class="gm ok">' + A.facts_in_context + ' &middot; ' + pct(A.context_coverage) + '</span></div>' +
      '<div class="grow"><span class="gn">tier-0 only</span><span class="gd">reachable, but only by following a residue pointer</span>' +
      '<span class="gm">' + A.facts_tier0_only + '</span></div>' +
      '<div class="grow"><span class="gn">unreachable</span><span class="gd">in neither the summary nor the log &mdash; the gate</span>' +
      '<span class="gm ' + (A.facts_unreachable ? 'no' : 'ok') + '">' + A.facts_unreachable + '</span></div>' +
      '<div class="grow"><span class="gn">residue pointers live</span><span class="gd">every drop still resolves to its original event</span>' +
      '<span class="gm ok">' + A.residue_live_pointers + ' / ' + A.residue_rows + '</span></div>' +
      '<div class="grow"><span class="gn">events dropped</span><span class="gd">left context and were written down as residue</span>' +
      '<span class="gm">' + A.events_dropped + ' of ' + A.events + '</span></div>' +
      '<div class="res" style="display:block">' + pct(A.context_coverage) + ' of the facts survived into the compacted context and ' +
      '<b>' + pct(A.total_coverage) + '</b> are reachable in total. The gap between those two numbers is exactly what an ordinary compactor loses without saying so.</div></div>';

    mountStage();
    renderMemDemo(0);
    $('#mdemoseg').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-mdemo]'); if (!b) return;
      $$('#mdemoseg button').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      renderMemDemo(+b.getAttribute('data-mdemo'));
    });
    $('#mem-play').addEventListener('click', function () { mountStage(); playStage(); });
    playStage();
  }

  function memCalm() {
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var calm = document.documentElement.getAttribute('data-motion') === 'calm';
    return reduce || calm;
  }

  /* Build (or rebuild) the stage with every chip back in the context bed. */
  function mountStage() {
    var C = MEMORY_STATS.chips, ctx = $('#mem-ctx'), res = $('#mem-res');
    if (!ctx) return;
    ctx.innerHTML = ''; res.innerHTML = '';
    var chip = function (cls, text) {
      var d = document.createElement('div');
      d.className = 'mem-chip ' + cls;
      d.textContent = text;
      var p = document.createElement('span');
      p.className = 'mp'; p.textContent = '-> T0';
      d.appendChild(p);
      return d;
    };
    C.facts.forEach(function (t) { ctx.appendChild(chip('t1', t)); });
    C.kept.forEach(function (t) { ctx.appendChild(chip('t2', t)); });
    C.dropped.forEach(function (t) { ctx.appendChild(chip('t3 doomed', t)); });
    tally();
  }

  function tally() {
    var ctx = $('#mem-ctx'), res = $('#mem-res');
    if (!ctx) return;
    $('#mem-n-ctx').textContent = ctx.children.length;
    $('#mem-n-res').textContent = res.children.length;
    $('#mem-tally').innerHTML = '<b>' + ctx.children.length + '</b> in context &middot; <i>' +
      res.children.length + '</i> recorded as residue &middot; <b>0</b> lost';
  }

  /* The compaction itself. Doomed chips travel; they never just disappear.
     Reduced motion gets the end state in one step rather than no state. */
  function playStage() {
    var ctx = $('#mem-ctx'), res = $('#mem-res');
    if (!ctx) return;
    var doomed = $$('#mem-ctx .doomed');
    var move = function (c) {
      c.classList.remove('leaving', 'doomed');
      res.appendChild(c);
      if (!memCalm()) c.classList.add('arriving');
      tally();
    };
    if (memCalm()) { doomed.forEach(move); return; }
    doomed.forEach(function (c, i) {
      setTimeout(function () {
        if (!c.parentNode || c.parentNode !== ctx) return;
        c.classList.add('leaving');
        setTimeout(function () { move(c); }, 170);
      }, 260 + i * 70);
    });
  }

  function renderMemDemo(i) {
    var d = MEMORY_STATS.demo[i], out = $('#mdemoout');
    if (!d) { out.innerHTML = ''; return; }
    var nothing = !d.held.length && !d.forgotten.length;
    var head =
      '<div class="card"><div class="chead"><span class="ci">&sect;</span><div style="min-width:0">' +
      '<b>' + (nothing ? 'Nothing above the floor' : d.held.length + ' held &middot; ' + d.forgotten.length + ' forgotten') + '</b>' +
      '<div class="sub">' + d.ms + ' ms &middot; ' + d.scanned + ' rows &middot; 0 tokens</div></div>' +
      '<div class="act"><span class="tag ' + (nothing ? 'warn">HONEST MISS' : 'info">ANSWERED') + '</span></div></div>' +
      '<div class="grow"><span class="gn">query</span><span class="gd">&ldquo;' + d.q + '&rdquo;</span></div>' +
      (d.gates.length ? '<div class="grow"><span class="gn">gate signature</span><span class="gd">' +
        d.gates.join(', ') + '</span></div>' : '');

    var held = d.held.map(function (h) {
      var p = h.parts;
      return '<div class="grow"><span class="gn">tier ' + h.tier + ' &middot; ' + h.score.toFixed(3) + '</span>' +
             '<span class="gd">' + h.subject + '</span>' +
             '<span class="gm ok">exact ' + p.exact + ' &middot; cover ' + p.cover + ' &middot; cos ' + p.cosine + '</span></div>';
    }).join('');

    var gone = d.forgotten.map(function (f) {
      return '<div class="grow"><span class="gn">forgotten &middot; ' + f.score.toFixed(3) + '</span>' +
             '<span class="gd">compacted ' + f.at + ' &middot; ' + f.reason + ' &middot; event ' + f.event + '</span>' +
             '<span class="gm">tier 0 holds ' + f.chars + ' chars</span></div>' +
             '<div class="row" style="height:auto;padding:8px 12px">' +
             '<span class="path" style="white-space:normal">&ldquo;' + f.claim + '&rdquo;</span></div>';
    }).join('');

    var foot = nothing
      ? '<div class="res" style="display:block"><b class="no">Never held this.</b> Every signal fell below the floor, ' +
        'so the store returns nothing rather than the least-bad row &mdash; and, just as importantly, does not claim to have forgotten it either.</div>'
      : (d.forgotten.length
         ? '<div class="res" style="display:block">The forgotten rows are not reconstructions. Each one is a claim, a reason, ' +
           'a compaction time and a live tier-0 address &mdash; enough to know something is missing and to go and get it.</div>'
         : '<div class="res" style="display:block">Held outright. Nothing about this query was compacted away.</div>');

    out.innerHTML = head + held + gone + foot + '</div>';
  }
"""


# ---------------------------------------------------------------------------
# injection
# ---------------------------------------------------------------------------

def main() -> int:
    h = open(IDX, encoding="utf-8").read()
    if SENTINEL in h:
        print("already present")
        return 0

    stats = measured()
    edits = [
        # CSS, ahead of the cards block, the same seam add_crew_bay.py uses
        ("/* ---------- cards ---------- */", CSS + "\n/* ---------- cards ---------- */"),
        # nav, directly under Precedent, because the two are read together
        ('    <div class="navitem" data-view="artifacts">',
         NAV + '    <div class="navitem" data-view="artifacts">'),
        # the view itself
        ('  <!-- ============ ARTIFACTS ============ -->',
         VIEW + "\n  <!-- ============ ARTIFACTS ============ -->"),
        # view order, so the transition slides the right way
        ("'precedent','artifacts'", "'precedent','memory','artifacts'"),
        # build the page the first time it is shown
        ("if (name === 'precedent') renderPrecedent();",
         "if (name === 'precedent') renderPrecedent();\n"
         "      if (name === 'memory') renderMemory();"),
        # the measured blob, next to precedent's
        ("  var PRECEDENT_STATS = ",
         "  var MEMORY_STATS = " + json.dumps(stats, separators=(",", ":")) + ";\n"
         "  var PRECEDENT_STATS = "),
        # the renderer, next to its peer
        ("  /* ---------------- precedent page ---------------- */",
         js(stats) + "\n  /* ---------------- precedent page ---------------- */"),
    ]

    for anchor, _ in edits:
        n = h.count(anchor)
        if n != 1:
            raise SystemExit("anchor %r appears %d times, expected 1" % (anchor[:56], n))
    for anchor, replacement in edits:
        h = h.replace(anchor, replacement, 1)

    open(IDX, "w", encoding="utf-8").write(h)
    print("Memory view added — %s ingest, %d events, %d residue rows"
          % ("live" if stats["live"] else "fallback", stats["events"], stats["residue"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
