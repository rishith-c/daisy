"""
Turning a vendor page into typed rows — and saying honestly how that went.

The extractor deliberately returns two things: the rows, and a per-field account
of how each one was obtained. The second half is not diagnostics for humans, it
is the input to drift detection. A scraper that cannot say "the price selector
matched 7 nodes and none of them was a number" has no way to notice the morning
it stops working, because the rows still arrive and the status is still 200.

    IS      a small DOM, a CSS-subset selector matcher, tolerant coercion of
            "$0.09" / "M4" / "In stock" into float / float / bool, and a report
    IS NOT  a browser — no JavaScript, no CSS cascade, no layout, no XPath, no
            sibling or pseudo-class combinators beyond :nth-child

The selector subset is small on purpose. Every construct here is one that
scrape.repair can *derive* mechanically by looking at a node; anything richer
would be writable by a human and unreachable by the repair loop, which is the
wrong way round for a scraper meant to fix itself.

Missing values are never invented and never quietly dropped. A row whose price
selector found nothing comes back without a "price_usd" key, and scrape.health
decides what that means. Extraction reports; it does not judge.

Zero third-party dependencies. html.parser is stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


class SelectorError(ValueError):
    """A selector that this subset cannot express, caught at load time."""


# HTML that never has children, so it must not be pushed onto the open stack.
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

# Tags whose start implicitly closes an open sibling. Real vendor pages omit
# </td> and </li> constantly; without this the tree nests instead of branching
# and every row becomes an ancestor of the next one.
IMPLICIT = {
    "li": {"li"}, "p": {"p"}, "option": {"option"},
    "td": {"td", "th"}, "th": {"td", "th"}, "tr": {"tr", "td", "th"},
    "dt": {"dt", "dd"}, "dd": {"dt", "dd"},
    "tbody": {"thead", "tbody", "tr", "td", "th"},
    "thead": {"thead", "tbody", "tr", "td", "th"},
}

SKIP_TEXT = frozenset(("script", "style"))


# ---------------------------------------------------------------------------
# the DOM
# ---------------------------------------------------------------------------

class Node:
    """One element. `kids` interleaves child Nodes and raw text in source order."""

    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.kids: list = []
        self.parent = parent

    # -- shape ------------------------------------------------------------

    @property
    def children(self) -> list["Node"]:
        return [k for k in self.kids if isinstance(k, Node)]

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def walk(self):
        """Descendants in document order."""
        for k in self.kids:
            if isinstance(k, Node):
                yield k
                yield from k.walk()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def child_index(self) -> int:
        """1-based position among the parent's element children, CSS-style."""
        if self.parent is None:
            return 1
        for i, c in enumerate(self.parent.children, 1):
            if c is self:
                return i
        return 1

    # -- content ----------------------------------------------------------

    def text(self) -> str:
        parts: list[str] = []
        self._text_into(parts)
        return norm_ws(" ".join(parts))

    def _text_into(self, out: list) -> None:
        if self.tag in SKIP_TEXT:
            return
        for k in self.kids:
            if isinstance(k, Node):
                k._text_into(out)
            else:
                out.append(k)

    def __repr__(self) -> str:
        c = "." + ".".join(self.classes) if self.classes else ""
        return "<%s%s>" % (self.tag, c)


def contains(outer: Node, inner: Node) -> bool:
    """Is `inner` at or below `outer`?"""
    n = inner
    while n is not None:
        if n is outer:
            return True
        n = n.parent
    return False


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            if k not in a:                     # first wins, as browsers do
                a[k] = v if v is not None else ""
        closes = IMPLICIT.get(tag)
        while closes and len(self.stack) > 1 and self.stack[-1].tag in closes:
            self.stack.pop()
        n = Node(tag, a, self.stack[-1])
        self.stack[-1].kids.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # An end tag with no matching start is malformed markup, not our
        # problem to correct; dropping it is what browsers do too.

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].kids.append(data)


def parse(html: str) -> Node:
    p = _Tree()
    p.feed(html)
    p.close()
    return p.root


# ---------------------------------------------------------------------------
# selectors — the subset, and only the subset
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    tag: str                      # "*" matches anything
    classes: tuple
    ident: str | None
    attrs: tuple                  # ((name, value-or-None), ...)
    nth: int | None
    child: bool                   # reached from the previous step by ">"


_IDENT = re.compile(r"[\w-]+")
_TAG = re.compile(r"[A-Za-z][\w-]*|\*")
_NTH = re.compile(r":nth-child\((\d+)\)")


def _compound(text: str, child: bool) -> Step:
    tag, classes, ident, attrs, nth = "*", [], None, [], None
    i = 0
    m = _TAG.match(text)
    if m:
        tag, i = m.group(0), m.end()
    while i < len(text):
        c = text[i]
        if c in ".#":
            m = _IDENT.match(text, i + 1)
            if not m:
                raise SelectorError("empty name after %r in %r" % (c, text))
            if c == ".":
                classes.append(m.group(0))
            else:
                ident = m.group(0)
            i = m.end()
        elif c == "[":
            j = text.find("]", i)
            if j < 0:
                raise SelectorError("unclosed [ in %r" % text)
            body = text[i + 1:j]
            if "=" in body:
                k, v = body.split("=", 1)
                attrs.append((k.strip(), v.strip().strip("\"'")))
            else:
                attrs.append((body.strip(), None))
            i = j + 1
        elif c == ":":
            m = _NTH.match(text, i)
            if not m:
                raise SelectorError(":nth-child(n) is the only pseudo-class supported, got %r" % text[i:])
            nth, i = int(m.group(1)), m.end()
        else:
            raise SelectorError("cannot parse %r at %r" % (text, text[i:]))
    return Step(tag, tuple(classes), ident, tuple(attrs), nth, child)


def parse_selector(sel: str) -> tuple[Step, ...]:
    steps, buf, depth, child = [], "", 0, False
    for ch in sel.strip():
        if ch in "[(":
            depth += 1
            buf += ch
        elif ch in "])":
            depth -= 1
            buf += ch
        elif depth == 0 and (ch.isspace() or ch == ">"):
            if buf:
                steps.append(_compound(buf, child))
                buf, child = "", False
            if ch == ">":
                child = True
        else:
            buf += ch
    if buf:
        steps.append(_compound(buf, child))
    if not steps:
        raise SelectorError("empty selector")
    return tuple(steps)


def _match_step(n: Node, st: Step) -> bool:
    if st.tag != "*" and n.tag != st.tag:
        return False
    if st.classes and not set(st.classes).issubset(n.classes):
        return False
    if st.ident is not None and n.attrs.get("id") != st.ident:
        return False
    for k, v in st.attrs:
        if k not in n.attrs or (v is not None and n.attrs[k] != v):
            return False
    if st.nth is not None and n.child_index() != st.nth:
        return False
    return True


def _matches(n: Node, steps: tuple, scope: Node) -> bool:
    """Right-to-left, the way selector engines actually do it."""
    if not _match_step(n, steps[-1]):
        return False
    cur = n
    for i in range(len(steps) - 2, -1, -1):
        direct = steps[i + 1].child
        p = cur.parent
        if direct:
            if p is None or not contains(scope, p) or not _match_step(p, steps[i]):
                return False
            cur = p
        else:
            while p is not None and contains(scope, p):
                if _match_step(p, steps[i]):
                    break
                p = p.parent
            else:
                return False
            if p is None:
                return False
            cur = p
    return True


def select(scope: Node, selector: str) -> list[Node]:
    """Every descendant of `scope` matching `selector`, in document order."""
    steps = parse_selector(selector)
    return [n for n in scope.walk() if _matches(n, steps, scope)]


# ---------------------------------------------------------------------------
# coercion — tolerant on purpose
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d\d\d(?:\D|$))")
_NUMBER = re.compile(r"[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?")

# Kept deliberately tight. A label this list does not know coerces to nothing,
# which surfaces as a missing field — far better than guessing "Ships today"
# means in stock and shipping a fastener that does not exist.
TRUEISH = frozenset(("true", "yes", "y", "1", "in stock", "in-stock", "instock",
                     "available", "stocked"))
FALSEISH = frozenset(("false", "no", "n", "0", "out of stock", "out-of-stock",
                      "outofstock", "backordered", "unavailable", "sold out",
                      "discontinued"))

TYPES = ("str", "num", "bool")


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s.replace("\xa0", " ")).strip()


def coerce(raw: str | None, kind: str, pattern: str | None = None):
    """Text -> typed value, or None when it does not convert.

    None is the honest answer, not a default. Every caller treats it as a
    missing field, so a column that quietly turns into prose fails loudly
    instead of arriving as 0.0.
    """
    if kind not in TYPES:
        raise ValueError("unknown field type %r" % kind)
    if raw is None:
        return None
    s = norm_ws(raw)
    if pattern:
        m = re.search(pattern, s)
        if not m:
            return None
        s = norm_ws(m.group(1) if m.groups() else m.group(0))
    if not s:
        return None
    if kind == "str":
        return s
    if kind == "num":
        m = _NUMBER.search(_THOUSANDS.sub("", s))
        return float(m.group(0)) if m else None
    low = s.lower()
    if low in TRUEISH:
        return True
    if low in FALSEISH:
        return False
    return None


def read(node: Node, attr: str | None) -> str | None:
    """The one place a field's raw text is decided: an attribute, or the text."""
    return node.attrs.get(attr) if attr else node.text()


# ---------------------------------------------------------------------------
# running a spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldReport:
    """Per-field forensics: enough to tell a rename from an empty page."""
    name: str
    selector: str
    attr: str | None
    matched: int                  # rows where the selector found a node
    typed: int                    # rows where that node also produced a value
    multiple: int                 # rows where it matched more than one node
    sample: str = ""              # first raw string seen, for the diff

    @property
    def clean(self) -> bool:
        return self.matched == self.typed


@dataclass
class Extraction:
    spec: str
    rows: list
    row_selector: str
    rows_matched: int             # nodes the row selector found
    fields: list
    gaps: list = field(default_factory=list)   # [{"row": i, "missing": [...]}]

    def report(self, name: str) -> FieldReport | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def complete(self, required) -> list:
        """Rows carrying every required key — the only ones safe to hand on."""
        req = list(required)
        return [r for r in self.rows if all(k in r for k in req)]


def run(spec, html: str) -> Extraction:
    """Apply one rules spec to one page."""
    root = parse(html)
    row_nodes = select(root, spec.row_selector)

    matched = {f.name: 0 for f in spec.fields}
    typed = {f.name: 0 for f in spec.fields}
    multiple = {f.name: 0 for f in spec.fields}
    sample = {f.name: "" for f in spec.fields}

    rows, gaps = [], []
    for i, rn in enumerate(row_nodes):
        row, missing = {}, []
        for f in spec.fields:
            hits = select(rn, f.selector)
            if not hits:
                missing.append(f.name)
                continue
            matched[f.name] += 1
            if len(hits) > 1:
                multiple[f.name] += 1
            raw = read(hits[0], f.attr)
            if not sample[f.name] and raw:
                sample[f.name] = norm_ws(raw)[:48]
            val = coerce(raw, f.type, f.pattern)
            if val is None:
                missing.append(f.name)
                continue
            typed[f.name] += 1
            row[f.name] = val
        rows.append(row)
        if missing:
            gaps.append({"row": i, "missing": missing})

    reports = [FieldReport(f.name, f.selector, f.attr, matched[f.name],
                           typed[f.name], multiple[f.name], sample[f.name])
               for f in spec.fields]
    return Extraction(spec.name, rows, spec.row_selector, len(row_nodes), reports, gaps)
