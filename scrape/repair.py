"""
Re-deriving selectors by anchoring on the data, not on the markup.

When a vendor restructures a page, every selector in rules.json describes
markup that no longer exists. The one thing that did *not* change is the data:
the M4 screw still costs $0.14 and is still class 8.8. So the repair works
backwards from values the scraper is known to have read correctly — the
`anchors` in baseline.json — and asks the new page where those values live now.

    find the nodes holding each known value
    -> the largest container around one product and no other product is a row
    -> the shortest selector reaching each value from that row is the field
    -> keep any old selector that still works, so the diff stays readable
    -> re-extract, re-run health, and discard the whole proposal if it fails

Anchoring on data rather than markup is what makes this survive a rewrite
instead of a rename. A heuristic keyed on tag names or class similarity would
have nothing to hold on to once <td class="col-price"> becomes
<span class="sku-price">; a heuristic keyed on 0.14 does.

    IS      a bounded, deterministic search over selectors the extractor can
            express, validated against every row before it is proposed
    IS NOT  a model, a guess, or an edit. Nothing here writes rules.json

That last line is the point. A scraper that silently rewrites its own config
is a scraper whose output nobody can explain. This module returns a proposal
and a diff; promoting it to rules.json takes a human typing --accept.

Zero third-party dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dfield, replace

from . import health as _health
from .extract import (Node, SelectorError, coerce, contains, parse, read,
                      select)
from .rules import Field, Spec, bump

# A value never lives on the document, the page shell, or inside a script.
SKIP_NODES = frozenset(("#document", "html", "head", "body", "script", "style", "template"))

MAX_CANDIDATES = 24     # selectors tried per field per anchor node


@dataclass(frozen=True)
class Cand:
    """A place in the new page where a known-good value turned up."""
    node: Node
    attr: str | None
    raw: str
    value: object


@dataclass
class Proposal:
    spec: Spec | None
    rows: list
    health: object | None
    changes: list
    diff: list
    notes: list = dfield(default_factory=list)
    anchors: int = 0

    @property
    def accepted(self) -> bool:
        """A repair nobody verified is not a repair."""
        return self.spec is not None and self.health is not None and not self.health.broken

    def as_dict(self) -> dict:
        from .rules import spec_to_dict
        return {
            "repaired": self.accepted,
            "anchors_used": self.anchors,
            "changes": self.changes,
            "diff": self.diff,
            "notes": self.notes,
            "health": self.health.as_dict() if self.health else None,
            "rows": self.rows,
            "spec": spec_to_dict(self.spec) if self.spec else None,
        }


# ---------------------------------------------------------------------------
# finding known values in unknown markup
# ---------------------------------------------------------------------------

def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9
    return a == b


def _index(root: Node, spec: Spec) -> dict:
    """Every (node, source) in the page that converts under some field's type.

    Built once. Each node's text is computed exactly once here; doing it per
    field per anchor turns a fixture-sized page into a noticeable pause.
    """
    nodes = [n for n in root.walk() if n.tag not in SKIP_NODES]
    texts = {id(n): n.text() for n in nodes}
    idx: dict = {}
    for f in spec.fields:
        key = (f.type, f.pattern)
        if key in idx:
            continue
        found = []
        for n in nodes:
            t = texts[id(n)]
            v = coerce(t, f.type, f.pattern)
            if v is not None:
                found.append(Cand(n, None, t, v))
            for k, raw in n.attrs.items():
                if k == "class" or not raw:
                    continue
                v2 = coerce(raw, f.type, f.pattern)
                if v2 is not None:
                    found.append(Cand(n, k, raw, v2))
        idx[key] = found
    return idx


def _holders(idx: dict, f: Field, value) -> list:
    """Innermost nodes carrying `value`.

    An ancestor inherits its children's text, so <article> "matches" the price
    of the screw inside it. Keeping only the innermost text match is what stops
    the repair proposing a selector for the whole card.
    """
    hits = [c for c in idx[(f.type, f.pattern)] if _same(c.value, value)]
    texty = [c for c in hits if c.attr is None]
    out = []
    for c in hits:
        if c.attr is None and any(o is not c and o.node is not c.node
                                  and contains(c.node, o.node) for o in texty):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# which part of the page is one row
# ---------------------------------------------------------------------------

def _ownership(root: Node, spec: Spec, anchors: list, idx: dict):
    """Map each node to the anchor record it can only belong to.

    Only values held by exactly one record are usable here. "8.8" appears in
    three rows, so a container holding an 8.8 tells you nothing; $0.14 appears
    in one, so a container holding it is inside that product or wraps the lot.
    """
    holders = {}
    for ri, rec in enumerate(anchors):
        for f in spec.fields:
            if rec.get(f.name) is None:
                continue
            holders[(ri, f.name)] = _holders(idx, f, rec[f.name])

    owners: dict = {}
    for f in spec.fields:
        seen: dict = {}
        for ri, rec in enumerate(anchors):
            v = rec.get(f.name)
            if v is None:
                continue
            hit = next((k for k in seen if _same(k, v)), None)
            seen.setdefault(v if hit is None else hit, []).append(ri)
        for v, ris in seen.items():
            if len(ris) != 1:
                continue
            for c in holders.get((ris[0], f.name), ()):
                owners.setdefault(id(c.node), set()).add(ris[0])

    below: dict = {}

    def descend(n: Node) -> set:
        s = set(owners.get(id(n), ()))
        for c in n.children:
            s |= descend(c)
        below[id(n)] = s
        return s

    descend(root)
    return holders, below


def _container(seed: Node, ri: int, below: dict) -> Node | None:
    """The largest ancestor of `seed` that still belongs to record `ri` alone."""
    if below.get(id(seed), set()) - {ri}:
        return None
    best, p = seed, seed.parent
    while p is not None and p.tag not in SKIP_NODES:
        if below.get(id(p), set()) - {ri}:
            break
        best, p = p, p.parent
    return best if best.tag not in SKIP_NODES else None


def _subtree_size(n: Node) -> int:
    return 1 + sum(1 for _ in n.walk())


def _row_containers(spec: Spec, anchors: list, holders: dict, below: dict) -> dict:
    """One row element per anchor record, chosen by how much of it they hold."""
    out = {}
    for ri in range(len(anchors)):
        seeds = []
        for f in spec.fields:
            seeds.extend(c.node for c in holders.get((ri, f.name), ()))
        best, best_key = None, None
        for s in seeds:
            a = _container(s, ri, below)
            if a is None:
                continue
            cover = sum(1 for f in spec.fields
                        if any(contains(a, c.node) for c in holders.get((ri, f.name), ())))
            key = (cover, -_subtree_size(a))
            if best_key is None or key > best_key:
                best, best_key = a, key
        if best is not None:
            out[ri] = best
    return out


# ---------------------------------------------------------------------------
# writing selectors for nodes
# ---------------------------------------------------------------------------

def _compound(n: Node) -> str:
    return n.tag + "".join("." + c for c in n.classes)


def _dedupe(seq) -> list:
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _absolute_candidates(a: Node) -> list:
    """Selectors that might identify `a` and its siblings-in-role page-wide."""
    c = [a.tag]
    for cl in a.classes:
        c.append("%s.%s" % (a.tag, cl))
    if len(a.classes) > 1:
        c.append(_compound(a))
    p = a.parent
    if p is not None and p.tag not in SKIP_NODES:
        c.append("%s > %s" % (_compound(p), _compound(a)))
    for anc in a.ancestors():
        if anc.attrs.get("id"):
            c.append("%s#%s %s" % (anc.tag, anc.attrs["id"], _compound(a)))
            break
    return _dedupe(c)


def _relative_candidates(rowel: Node, n: Node) -> list:
    """Selectors reaching `n` from inside its row."""
    c = [n.tag]
    for cl in n.classes:
        c.append("%s.%s" % (n.tag, cl))
    if len(n.classes) > 1:
        c.append(_compound(n))
    c.append("%s:nth-child(%d)" % (n.tag, n.child_index()))
    p = n.parent
    if p is not None and p is not rowel and contains(rowel, p):
        c.append("%s > %s" % (_compound(p), _compound(n)))
        c.append("%s > %s:nth-child(%d)" % (_compound(p), n.tag, n.child_index()))
    chain, cur = [], n
    while cur is not None and cur is not rowel:
        chain.append(_compound(cur))
        cur = cur.parent
    if len(chain) > 1:
        c.append(" > ".join(reversed(chain)))
    return _dedupe(c)[:MAX_CANDIDATES]


# ---------------------------------------------------------------------------
# validating a candidate rule against every row
# ---------------------------------------------------------------------------

def _first(rowel: Node, selector: str):
    try:
        hits = select(rowel, selector)
    except SelectorError:
        return None
    return hits[0] if hits else None


def _validates(rule: Field, row_nodes: list, anchor_of: dict) -> bool:
    """Must produce a value in EVERY row, and reproduce every anchor it covers.

    "Every row" is deliberate. A selector that works for six of seven rows is
    exactly the drift this pipeline exists to catch; proposing one as a repair
    would be laundering the bug into the config.
    """
    if not row_nodes:
        return False
    for rn in row_nodes:
        node = _first(rn, rule.selector)
        if node is None:
            return False
        val = coerce(read(node, rule.attr), rule.type, rule.pattern)
        if val is None:
            return False
        rec = anchor_of.get(id(rn))
        if rec is not None and rec.get(rule.name) is not None and not _same(val, rec[rule.name]):
            return False
    return True


def _score(rule: Field, row_nodes: list) -> tuple:
    """Rank viable rules. Order of the keys is the argument, not the numbers.

    Matching exactly one node per row comes first: anything else means the
    selector is describing a group and the extractor is taking the first of it.
    Then the shortest raw string, because a value sitting alone in its element
    is far more likely to be that field than the same number embedded in a
    product title, where it may be a coincidence that holds for seven rows.
    """
    exact = sum(1 for rn in row_nodes if len(select(rn, rule.selector)) == 1)
    raws = []
    for rn in row_nodes:
        node = _first(rn, rule.selector)
        raws.append(len(read(node, rule.attr) or "") if node else 999)
    purity = -(sum(raws) / len(raws)) if raws else -999
    named = 1 if ("." in rule.selector or "#" in rule.selector) else 0
    steps = rule.selector.count(">") + rule.selector.count(" ")
    return (exact, purity, named, -steps, -len(rule.selector),
            0 if rule.attr is None else -1)


# ---------------------------------------------------------------------------
# the proposal
# ---------------------------------------------------------------------------

def _describe(rule: Field | None) -> str:
    if rule is None:
        return "(none)"
    return rule.selector + ("" if rule.attr is None else "  @%s" % rule.attr)


def diff(old: Spec, new: Spec) -> tuple:
    """Old vs new, as a list of changes and as lines a human reads."""
    changes, lines = [], []
    if old.row_selector != new.row_selector:
        changes.append({"path": "row_selector", "before": old.row_selector,
                        "after": new.row_selector})
    for f in new.fields:
        was = old.field(f.name)
        if was is None:
            changes.append({"path": f.name, "before": "(new field)", "after": _describe(f)})
        elif (was.selector, was.attr) != (f.selector, f.attr):
            changes.append({"path": f.name, "before": _describe(was), "after": _describe(f)})
    for c in changes:
        lines.append("  %s" % c["path"])
        lines.append("    - %s" % c["before"])
        lines.append("    + %s" % c["after"])
    if not changes:
        lines.append("  (no selector changed)")
    return changes, lines


def propose(html: str, spec: Spec, baseline: dict | None,
            now: float | None = None) -> Proposal:
    """Derive a spec that works on `html`, and verify it before returning."""
    anchors = list((baseline or {}).get("anchors") or [])
    if not anchors:
        return Proposal(None, [], None, [], [],
                        ["no anchors in the baseline — auto-repair has nothing to "
                         "anchor on. Record a good extraction first: "
                         "fetch --save-baseline"], 0)

    root = parse(html)
    idx = _index(root, spec)
    holders, below = _ownership(root, spec, anchors, idx)
    notes: list = []

    # Keep the existing row selector if it still finds the anchors. Rewriting a
    # selector that works produces a diff nobody can review.
    row_selector = spec.row_selector
    row_nodes = select(root, row_selector)
    reached = {ri for rn in row_nodes for ri in below.get(id(rn), ())}
    if not row_nodes or len(reached) < min(2, len(anchors)):
        containers = _row_containers(spec, anchors, holders, below)
        if not containers:
            return Proposal(None, [], None, [], [],
                            notes + ["could not locate any known product in this page; "
                                     "the values themselves have changed, not just the markup"],
                            len(anchors))
        derived = _row_selector_for(root, list(containers.values()))
        if derived is None:
            return Proposal(None, [], None, [], [],
                            notes + ["found the rows but could not express them as a selector"],
                            len(anchors))
        row_selector = derived
        row_nodes = select(root, row_selector)
        notes.append("row container re-derived from %d anchored product(s)" % len(containers))
    else:
        notes.append("row selector still resolves; only fields were re-derived")

    anchor_of = {}
    for rn in row_nodes:
        owned = below.get(id(rn), set())
        if len(owned) == 1:
            anchor_of[id(rn)] = anchors[next(iter(owned))]

    new = replace(spec, row_selector=row_selector)
    for f in spec.fields:
        if _validates(f, row_nodes, anchor_of):
            continue
        best = _derive_field(f, row_nodes, anchor_of, holders, idx, anchors)
        if best is None:
            notes.append("no selector reproduces %s in every row" % f.name)
            continue
        new = new.with_field(best)

    ex = _run(new, html)
    hl = _health.evaluate(ex, new, baseline, captured_at=now, now=now)
    changes, lines = diff(spec, new)
    if changes:
        new = bump(new, "vendor markup changed; selectors re-derived from baseline anchors",
                   ["%s: %s -> %s" % (c["path"], c["before"], c["after"]) for c in changes])
        changes, lines = diff(spec, new)
    return Proposal(new, ex.rows, hl, changes, lines, notes, len(anchors))


def _run(spec: Spec, html: str):
    from .extract import run
    return run(spec, html)


def _row_selector_for(root: Node, containers: list) -> str | None:
    want = len(containers)
    ids = {id(a) for a in containers}
    best, best_key = None, None
    for a in containers:
        for s in _absolute_candidates(a):
            try:
                hits = select(root, s)
            except SelectorError:
                continue
            hit_ids = {id(n) for n in hits}
            covered = len(ids & hit_ids)
            if not covered:
                continue
            key = (covered, -abs(len(hits) - want),
                   1 if ("." in s or "#" in s) else 0, -len(s))
            if best_key is None or key > best_key:
                best, best_key = s, key
    return best


def _derive_field(f: Field, row_nodes: list, anchor_of: dict, holders: dict,
                  idx: dict, anchors: list) -> Field | None:
    seen, viable = set(), []
    for rn in row_nodes:
        rec = anchor_of.get(id(rn))
        if rec is None or rec.get(f.name) is None:
            continue
        for c in _holders(idx, f, rec[f.name]):
            if c.node is rn or not contains(rn, c.node):
                continue
            for sel in _relative_candidates(rn, c.node):
                key = (sel, c.attr)
                if key in seen:
                    continue
                seen.add(key)
                rule = replace(f, selector=sel, attr=c.attr)
                if _validates(rule, row_nodes, anchor_of):
                    viable.append(rule)
    if not viable:
        return None
    return max(viable, key=lambda r: _score(r, row_nodes))
