#!/usr/bin/env python3
"""Add the CREW bay — both agents visible at once, not narrating in turn.

The brief asked to combine Claude Code and Codex inside one app. The run shows
each of them speaking sequentially, which reads as two tools used one after the
other. This puts them side by side against the same contract, which is what
"combined" actually means.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = os.path.join(ROOT, "index.html")
h = open(idx, encoding="utf-8").read()

if "crew-bay" in h:
    print("already present")
    raise SystemExit(0)

css = """
/* ---------- CREW bay: two agents, one contract ---------- */
.bay { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 720px) { .bay { grid-template-columns: 1fr; } }
.lane {
  background: var(--surface); border: .5px solid var(--border);
  border-radius: var(--r-card); box-shadow: var(--shadow-1); overflow: hidden;
}
.lane .lh { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: .5px solid var(--border); }
.lane .lh i { width: 7px; height: 7px; border-radius: 2px; flex: 0 0 7px; }
.lane.claude .lh i { background: #C2703C; }
.lane.codex .lh i { background: #3E8577; }
.lane .lh b { font-size: 12.5px; font-weight: 600; }
.lane .lh .m { margin-left: auto; font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); white-space: nowrap; }
.lane .lb { padding: 10px 12px; font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); line-height: 1.8; }
.lane .lb .k { color: var(--ink-3); }
.lane .lb .v { color: var(--ink); }
.bay-foot {
  margin-top: 10px; padding: 10px 14px; border-radius: var(--r-card);
  background: var(--surface-2); border: .5px solid var(--border);
  font-size: 12.5px; color: var(--ink-2);
}
.bay-foot b { color: var(--ink); font-weight: 600; }
"""

h = h.replace("/* ---------- cards ---------- */", css + "\n/* ---------- cards ---------- */", 1)

def lane(cls, name, model, rows):
    body = "".join(
        '<div><span class="k">%s</span> &nbsp;<span class="v">%s</span></div>' % (k, v)
        for k, v in rows)
    return ('<div class="lane %s"><div class="lh"><i></i><b>%s</b><span class="m">%s</span></div>'
            '<div class="lb">%s</div></div>' % (cls, name, model, body))

bay = (
    '<div class="bay">'
    + lane("codex", "web-api", "codex exec &middot; gpt-5.3-codex", [
        ("worktree", "runs/1042/web-api"),
        ("branch", "run/1042/web-api"),
        ("contract", "5 endpoints, pinned"),
        ("wrote", "12 files &middot; +1,204 &minus;86"),
        ("tests", "18 passed, 0 failed"),
        ("sandbox", "workspace-write"),
      ])
    + lane("claude", "web-frontend", "claude -p &middot; fable-5 high", [
        ("worktree", "runs/1042/web-frontend"),
        ("branch", "run/1042/web-frontend"),
        ("contract", "same 5 endpoints"),
        ("wrote", "9 files &middot; +867 &minus;41"),
        ("skills", "hallmark, design-standards"),
        ("sandbox", "workspace-write"),
      ])
    + '</div>'
    '<div class="bay-foot">Two vendors, two worktrees, <b>one <code>api-contract.json</code></b>. '
    'Neither agent can touch git &mdash; the orchestrator owns every commit, so the diffs stay symmetric '
    'and reviewable. They are combined by a contract, not by a conversation.</div>'
)

step = "    { d: 820, kind: 'card', html: " + json.dumps(bay) + " },"

# place it right after labctl announces the worktrees
anchor = ("    { d: 900, kind: 'prose', tag: '<div class=\"who-tag codex\"><i></i>Codex · web-api</div>',")
assert anchor in h, "codex-lane anchor not found"
h = h.replace(anchor, step + "\n" + anchor, 1)

open(idx, "w", encoding="utf-8").write(h)
print("CREW bay added")
