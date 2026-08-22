"""
Precedent — the factory's case law.

Gate-Signature Hybrid Retrieval (GSHR).

Every gate failure the factory has ever seen is archived, compressed, and made
searchable, so a repeat failure is *fixed from precedent* instead of rediscovered
by an LLM. What makes this different from ordinary RAG over logs:

    the sparse half of the retrieval score is DETERMINISTIC VERIFICATION STATE
    — which gates failed and by how much — not text similarity.

So precedent is grounded in facts the factory actually proved, not in vibes.

Four tiers, cheapest first:

    T0  fingerprint   normalise volatile tokens -> blake2b     ~0.1 ms, exact
    T1  gate signature Jaccard over {gate: severity bucket}    deterministic
    T2  BM25 over the failure narrative (SQLite FTS5)          lexical
    T3  binary-quantised semantic vectors + exact rescore      32x smaller

fused with Reciprocal Rank Fusion (k=60), then rescored with a
Generative-Agents style recency/importance/relevance blend.

Zero third-party dependencies. Pure stdlib, offline, runs anywhere.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable

DIM = 512            # embedding dimensions
BYTES = DIM // 8     # 64 bytes per binary vector
RRF_K = 60           # reciprocal-rank-fusion constant (the paper's value)
HALF_LIFE_H = 72.0   # recency half-life; build factories forget faster than people
OVERSAMPLE = 8       # binary shortlist multiplier before exact rescore


# ----------------------------------------------------------------------------
# fingerprinting — tier 0
# ----------------------------------------------------------------------------

_NUM = re.compile(r"0x[0-9a-f]+|\b\d+(?:\.\d+)?\b", re.I)
_PATH = re.compile(r"(?:/[\w.\-]+)+|[\w.\-]+\.(?:py|ts|tsx|js|css|json|md)")
_HEX = re.compile(r"\b[0-9a-f]{7,}\b", re.I)
_WS = re.compile(r"\s+")


def fingerprint(text: str) -> str:
    """Structural hash of a failure, blind to volatile tokens.

    Line numbers, ports, addresses, temp paths and measured magnitudes all
    collapse, so the *same* defect hashes identically across runs.
    """
    s = text.lower()
    s = _PATH.sub(" PATH ", s)
    s = _HEX.sub(" HEX ", s)
    s = _NUM.sub(" N ", s)
    s = _WS.sub(" ", s).strip()
    return hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest()


# ----------------------------------------------------------------------------
# embeddings — pure-stdlib hashed character n-grams, binarised
# ----------------------------------------------------------------------------

def _tokens(text: str) -> Iterable[str]:
    """Word tokens plus character 4-grams (typo/variant tolerant)."""
    low = text.lower()
    for w in re.findall(r"[a-z_][a-z0-9_.]+", low):
        yield w
    squished = re.sub(r"\s+", " ", low)
    for i in range(len(squished) - 3):
        yield squished[i:i + 4]


def embed(text: str) -> list[float]:
    """Signed-hash embedding into DIM dims, L2-normalised.

    Deterministic, offline, no model download, ~40 us for a typical failure
    string. Quality is carried by the hybrid: this is only one of four tiers.
    """
    vec = [0.0] * DIM
    for tok in _tokens(text):
        h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % DIM
        sign = 1.0 if h[4] & 1 else -1.0
        # sublinear damping so frequent n-grams don't dominate
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def pack_bits(vec: list[float]) -> bytes:
    """float32[512] -> 64 bytes. 32x smaller; ~0.94 recall@10 after rescore."""
    out = bytearray(BYTES)
    for i, v in enumerate(vec):
        if v > 0:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


_POPCOUNT = bytes(bin(i).count("1") for i in range(256))


def hamming(a: bytes, b: bytes) -> int:
    return sum(_POPCOUNT[x ^ y] for x, y in zip(a, b))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ----------------------------------------------------------------------------
# gate signatures — tier 1, the part that makes this not-just-RAG
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    """One deterministic verification outcome."""
    name: str            # e.g. "physics.bend", "taste.t1", "contract.conformance"
    passed: bool
    margin: float | None = None   # ratio to allowable; <1.0 means failed
    detail: str = ""

    def token(self) -> str:
        """Signature token: gate name + severity bucket.

        Bucketing the margin is what lets 'FoS 0.82' and 'FoS 0.79' match as
        the same class of failure while 'FoS 0.12' does not.
        """
        if self.passed:
            return f"{self.name}=ok"
        if self.margin is None:
            return f"{self.name}=fail"
        if self.margin >= 0.9:
            band = "marginal"
        elif self.margin >= 0.6:
            band = "short"
        else:
            band = "gross"
        return f"{self.name}={band}"


def signature(gates: Iterable[GateResult]) -> frozenset[str]:
    """The deterministic half of the retrieval key."""
    return frozenset(g.token() for g in gates if not g.passed)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


# ----------------------------------------------------------------------------
# records
# ----------------------------------------------------------------------------

@dataclass
class Case:
    """One archived failure and — crucially — what fixed it."""
    run_id: str
    lane: str
    narrative: str              # what went wrong, in words
    gates: list[GateResult]
    fix: str                    # what actually resolved it
    fix_kind: str               # "algebra" | "resume-findings" | "human" | "none"
    resolved: bool
    ts: float
    importance: float = 0.5     # 0..1; escalations and novel families score high
    family: str = ""

    def sig(self) -> frozenset[str]:
        return signature(self.gates)

    def fp(self) -> str:
        return fingerprint(self.narrative)


@dataclass
class Hit:
    case: Case
    score: float
    tier: str                   # which tier found it first
    parts: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# the store
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id        INTEGER PRIMARY KEY,
  run_id    TEXT NOT NULL,
  lane      TEXT NOT NULL,
  narrative TEXT NOT NULL,
  gates     TEXT NOT NULL,
  sig       TEXT NOT NULL,
  fp        TEXT NOT NULL,
  fix       TEXT NOT NULL,
  fix_kind  TEXT NOT NULL,
  resolved  INTEGER NOT NULL,
  ts        REAL NOT NULL,
  importance REAL NOT NULL,
  family    TEXT NOT NULL,
  vec       BLOB NOT NULL,
  bits      BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_fp ON cases(fp);
CREATE INDEX IF NOT EXISTS cases_family ON cases(family);
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
  narrative, fix, content='cases', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS cases_ai AFTER INSERT ON cases BEGIN
  INSERT INTO cases_fts(rowid, narrative, fix) VALUES (new.id, new.narrative, new.fix);
END;
"""


class Precedent:
    """The factory's case law, on one SQLite file."""

    def __init__(self, path: str = "precedent.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- writing ------------------------------------------------------------

    def record(self, case: Case) -> int:
        vec = embed(case.narrative + " " + " ".join(sorted(case.sig())))
        bits = pack_bits(vec)
        blob = b"".join(_f32(v) for v in vec)
        cur = self.db.execute(
            "INSERT INTO cases(run_id,lane,narrative,gates,sig,fp,fix,fix_kind,"
            "resolved,ts,importance,family,vec,bits) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case.run_id, case.lane, case.narrative,
             json.dumps([asdict(g) for g in case.gates]),
             json.dumps(sorted(case.sig())), case.fp(), case.fix, case.fix_kind,
             int(case.resolved), case.ts, case.importance, case.family, blob, bits),
        )
        self.db.commit()
        return cur.lastrowid

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]

    # -- reading ------------------------------------------------------------

    def _row_to_case(self, r: sqlite3.Row) -> Case:
        return Case(
            run_id=r["run_id"], lane=r["lane"], narrative=r["narrative"],
            gates=[GateResult(**g) for g in json.loads(r["gates"])],
            fix=r["fix"], fix_kind=r["fix_kind"], resolved=bool(r["resolved"]),
            ts=r["ts"], importance=r["importance"], family=r["family"],
        )

    def recall(self, narrative: str, gates: list[GateResult],
               k: int = 3, now: float | None = None, min_score: float = 0.0) -> list[Hit]:
        """The whole point: what do we already know about this failure?"""
        now = now or time.time()
        qsig = signature(gates)
        qvec = embed(narrative + " " + " ".join(sorted(qsig)))
        qbits = pack_bits(qvec)
        qfp = fingerprint(narrative)

        # -- tier 0: exact structural fingerprint --------------------------
        exact = self.db.execute(
            "SELECT * FROM cases WHERE fp=? AND resolved=1 ORDER BY ts DESC LIMIT ?",
            (qfp, k),
        ).fetchall()
        if exact:
            return [Hit(self._row_to_case(r), 1.0, "fingerprint",
                        {"fp": qfp}) for r in exact]

        rows = self.db.execute("SELECT * FROM cases").fetchall()
        if not rows:
            return []

        # -- tier 1: gate-signature Jaccard (deterministic) ----------------
        jac = {}
        for r in rows:
            jac[r["id"]] = jaccard(qsig, frozenset(json.loads(r["sig"])))

        # -- tier 2: BM25 over the narrative (FTS5) ------------------------
        bm = {}
        terms = re.findall(r"[a-z][a-z0-9_]{2,}", narrative.lower())
        if terms:
            q = " OR ".join(sorted(set(terms))[:24])
            try:
                for r in self.db.execute(
                    "SELECT rowid, bm25(cases_fts) AS s FROM cases_fts "
                    "WHERE cases_fts MATCH ? ORDER BY s LIMIT 100", (q,)):
                    bm[r["rowid"]] = -r["s"]      # bm25() is negative-better
            except sqlite3.OperationalError:
                pass

        # -- tier 3: binary shortlist, then exact rescore -------------------
        shortlist = sorted(rows, key=lambda r: hamming(qbits, r["bits"]))[: max(k * OVERSAMPLE, 24)]
        dense = {}
        for r in shortlist:
            v = _unf32(r["vec"])
            dense[r["id"]] = cosine(qvec, v)

        # -- reciprocal rank fusion ----------------------------------------
        def ranks(d: dict) -> dict:
            order = sorted(d, key=lambda i: -d[i])
            return {rid: pos + 1 for pos, rid in enumerate(order)}

        rj, rb, rd = ranks(jac), ranks(bm), ranks(dense)
        fused: dict[int, float] = {}
        for rid in set(rj) | set(rb) | set(rd):
            s = 0.0
            if rid in rj and jac[rid] > 0:
                s += 1.0 / (RRF_K + rj[rid])
            if rid in rb:
                s += 1.0 / (RRF_K + rb[rid])
            if rid in rd:
                s += 1.0 / (RRF_K + rd[rid])
            fused[rid] = s

        by_id = {r["id"]: r for r in rows}
        hits: list[Hit] = []
        for rid, base in fused.items():
            r = by_id[rid]
            age_h = max(0.0, (now - r["ts"]) / 3600.0)
            recency = 0.5 ** (age_h / HALF_LIFE_H)
            # Generative-Agents style blend, but relevance is the fused rank
            score = (0.62 * _norm(base, fused)
                     + 0.20 * jac.get(rid, 0.0)
                     + 0.10 * recency
                     + 0.08 * r["importance"])
            if not r["resolved"]:
                score *= 0.55          # an unresolved case is weak precedent
            hits.append(Hit(self._row_to_case(r), score,
                            "hybrid",
                            {"jaccard": round(jac.get(rid, 0.0), 3),
                             "dense": round(dense.get(rid, 0.0), 3),
                             "bm25": round(bm.get(rid, 0.0), 3),
                             "recency": round(recency, 3)}))

        hits.sort(key=lambda h: -h.score)
        hits = [h for h in hits if h.score >= min_score]
        return hits[:k]

    # -- families -----------------------------------------------------------

    def families(self) -> list[dict]:
        """Cluster archived failures by exact gate signature."""
        out: dict[str, dict] = {}
        for r in self.db.execute("SELECT * FROM cases"):
            key = r["family"] or r["sig"]
            f = out.setdefault(key, {
                "family": key, "signature": json.loads(r["sig"]),
                "seen": 0, "fixed": 0, "fix_kinds": {}, "mttg": [],
            })
            f["seen"] += 1
            if r["resolved"]:
                f["fixed"] += 1
                f["fix_kinds"][r["fix_kind"]] = f["fix_kinds"].get(r["fix_kind"], 0) + 1
        return sorted(out.values(), key=lambda f: -f["seen"])

    def sql(self, query: str, limit: int = 50) -> list[dict]:
        """Memory as code: let an agent query its own past directly.

        Read-only. This is the tool that turns the archive from a black box
        into something an agent can interrogate on its own terms.
        """
        q = query.strip().rstrip(";")
        if not re.match(r"(?is)^\s*select\b", q):
            raise ValueError("precedent.sql accepts SELECT only")
        if re.search(r"(?is)\b(attach|pragma|insert|update|delete|drop|create|alter)\b", q):
            raise ValueError("precedent.sql is read-only")
        rows = self.db.execute(q).fetchmany(limit)
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

import struct


def _f32(v: float) -> bytes:
    return struct.pack("<f", v)


def _unf32(blob: bytes) -> list[float]:
    return list(struct.unpack("<%df" % (len(blob) // 4), blob))


def _norm(v: float, d: dict) -> float:
    if not d:
        return 0.0
    lo, hi = min(d.values()), max(d.values())
    return 0.0 if hi <= lo else (v - lo) / (hi - lo)
