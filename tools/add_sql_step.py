#!/usr/bin/env python3
"""Insert the 'memory as code' run step, using a real SQL result."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(ROOT, "precedent", "sqldemo.json")))
idx = os.path.join(ROOT, "index.html")
h = open(idx).read()

if "Memory as code" in h:
    print("already present")
    sys.exit(0)

rows = "".join(
    '<tr><td class="nm">{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
        r["family"], r["seen"], r["fixed"], r["weight"])
    for r in d["rows"])

sql_lines = [
    'labctl.precedent.sql(',
    '  "SELECT family, COUNT(*) AS seen, SUM(resolved) AS fixed,',
    '          ROUND(AVG(importance),2) AS weight',
    '     FROM cases',
    "    WHERE sig LIKE '%taste%' OR sig LIKE '%physics%'",
    '    GROUP BY family ORDER BY seen DESC")',
]
sql_text = "\n".join(sql_lines)

head = (
    '<div class="card"><div class="chead"><span class="ci">&gt;_</span>'
    '<div style="min-width:0"><b>Memory as code</b>'
    '<div class="sub">the agent queries its own history &middot; read-only &middot; {} ms</div></div>'
    '<div class="act"><span class="tag info">SQL TOOL</span></div></div>'
).format(d["ms"])

body = (
    '<div class="derive">' + sql_text + '</div>'
    '<div class="sx"><table class="m">'
    '<tr><th>family</th><th>seen</th><th>fixed</th><th>weight</th></tr>' + rows + '</table></div>'
    '<div class="res" style="display:block">Retrieved chunks are something an agent is '
    '<em>handed</em>. A read-only cursor is something it can <b>interrogate</b> &mdash; so it asks '
    'the question it actually has, not the one the retriever guessed at.</div></div>'
)

step = "    { d: 720, kind: 'card', html: " + json.dumps(head + body) + " },"

anchor = "    { d: 820, kind: 'precedent', html: '' },"
assert anchor in h, "anchor step not found"
h = h.replace(anchor, anchor + "\n" + step, 1)
open(idx, "w").write(h)
print("inserted memory-as-code step ({} chars)".format(len(step)))
