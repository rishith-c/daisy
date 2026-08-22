"""
IVF-PQ — an approximate nearest-neighbour index, in the standard library.

The memory store currently scans every row, unpacks a 64-byte binary code,
computes a Hamming distance and then rescores the survivors exactly. That is
correct and it is fine at a few hundred rows. It is linear, and a memory that
holds everything an agent has ever done is not going to stay at a few hundred.

This replaces the flat scan with the structure a vector database actually uses,
and it is worth being precise about why each half exists, because they solve
different problems:

**IVF (inverted file)** answers *which vectors do I not have to look at*. The
corpus is clustered by k-means into `nlist` cells; a query visits only the
`nprobe` nearest cells. That is a recall/latency dial, not an accuracy loss you
have to accept — raise nprobe and it converges on exhaustive.

**PQ (product quantisation)** answers *how small can one vector get and still
be rankable*. The 512-d vector is cut into 8 subspaces of 64 dims; each
subspace is quantised against its own 256-centroid codebook, so a vector
becomes **8 bytes**. The binary codes it replaces are 64 bytes for strictly
less information — binary quantisation keeps one bit per dimension and throws
away magnitude entirely, while PQ keeps a 64-dimensional centroid per
subspace. Eight times smaller and more faithful, which is not a trade-off,
it is just a better code.

The search is asymmetric on purpose: the query stays in full float precision
and only the stored vectors are quantised. Symmetric search would quantise the
query too and throw away information for no saving, since there is exactly one
query and it is already in memory.

    d(q, x) ~= sum over m of  D_m[ code_m(x) ]

`D_m` is precomputed once per query: 8 subspaces by 256 centroids = 2,048
distances. After that, scoring a vector is eight array lookups and seven adds,
with no arithmetic over 512 dimensions at all.

What this is NOT: a replacement for the exact rescore. PQ ranks candidates; the
top of that ranking is still rescored against the true float vector, because an
approximate answer presented as exact is the failure this project exists to
avoid.

Zero third-party dependencies. Deterministic: k-means is seeded.
"""

from __future__ import annotations

import json
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precedent.engine import DIM  # noqa: E402

M = 8                      # subspaces
DSUB = DIM // M            # 64 dims each
KSUB = 256                 # centroids per subspace -> one byte per subspace
CODE_BYTES = M             # 8 bytes per vector

SCHEMA = """
CREATE TABLE IF NOT EXISTS ann_codebook (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  nlist INTEGER NOT NULL,
  coarse BLOB NOT NULL,      -- nlist * DIM float32
  pq     BLOB NOT NULL,      -- M * KSUB * DSUB float32
  trained_on INTEGER NOT NULL,
  built_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ann_code (
  ref   TEXT PRIMARY KEY,    -- table:rowid
  cell  INTEGER NOT NULL,
  code  BLOB NOT NULL        -- CODE_BYTES
);
CREATE INDEX IF NOT EXISTS ann_cell ON ann_code(cell);
"""


# ---------------------------------------------------------------------------
# small vector helpers — kept explicit rather than clever, this is hot code
# ---------------------------------------------------------------------------

def _l2sq(a, b, off_a=0, off_b=0, n=None):
    n = n or len(a)
    s = 0.0
    for i in range(n):
        d = a[off_a + i] - b[off_b + i]
        s += d * d
    return s


def _kmeans(vecs: list, k: int, iters: int = 12, seed: int = 7) -> list:
    """Lloyd's algorithm with k-means++ seeding.

    ++ seeding matters more than the iteration count here: a bad random init
    leaves empty cells, and an empty cell is a cell the search wastes a probe
    on. Deterministic seed so an index rebuild is reproducible.
    """
    if not vecs:
        return []
    n, d = len(vecs), len(vecs[0])
    k = max(1, min(k, n))
    rng = random.Random(seed)

    # k-means++ with an incremental nearest-distance cache. Recomputing the
    # distance to every chosen centroid on each round is O(k^2 n d) and was, at
    # k=256 over 8 subspaces, slower than the exhaustive search this index
    # exists to replace. Keeping the running minimum makes it O(k n d): only
    # the newest centroid can lower any point's distance, so only it needs
    # comparing.
    first = rng.randrange(n)
    cents = [list(vecs[first])]
    d2 = [_l2sq(v, cents[0]) for v in vecs]
    while len(cents) < k:
        tot = sum(d2)
        if tot <= 0:
            pick = rng.randrange(n)
        else:
            r, acc, pick = rng.random() * tot, 0.0, n - 1
            for i, w in enumerate(d2):
                acc += w
                if acc >= r:
                    pick = i
                    break
        c = list(vecs[pick])
        cents.append(c)
        for i, v in enumerate(vecs):
            dd = _l2sq(v, c)
            if dd < d2[i]:
                d2[i] = dd

    for _ in range(iters):
        sums = [[0.0] * d for _ in range(k)]
        counts = [0] * k
        for v in vecs:
            bi, bd = 0, float("inf")
            for ci, c in enumerate(cents):
                dd = _l2sq(v, c)
                if dd < bd:
                    bi, bd = ci, dd
            counts[bi] += 1
            s = sums[bi]
            for i in range(d):
                s[i] += v[i]
        moved = 0.0
        for ci in range(k):
            if not counts[ci]:
                # Re-seed an empty cell onto a random point rather than leaving
                # it stranded; a dead centroid silently costs a probe forever.
                cents[ci] = list(vecs[rng.randrange(n)])
                continue
            inv = 1.0 / counts[ci]
            new = [x * inv for x in sums[ci]]
            moved += _l2sq(new, cents[ci])
            cents[ci] = new
        if moved < 1e-9:
            break
    return cents


def _pack_f32(rows) -> bytes:
    flat = []
    for r in rows:
        flat.extend(r)
    return struct.pack("<%df" % len(flat), *flat)


def _unpack_f32(blob: bytes, width: int) -> list:
    n = len(blob) // 4
    vals = struct.unpack("<%df" % n, blob)
    return [list(vals[i:i + width]) for i in range(0, n, width)]


# ---------------------------------------------------------------------------

class Index:
    """A trained IVF-PQ index over one SQLite connection."""

    def __init__(self, con):
        self.con = con
        con.executescript(SCHEMA)
        self.nlist = 0
        self.coarse = []
        self.pq = []          # M lists of KSUB centroids, each DSUB long
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> bool:
        row = self.con.execute(
            "SELECT nlist, coarse, pq FROM ann_codebook WHERE id = 1").fetchone()
        if not row:
            return False
        self.nlist = row[0]
        self.coarse = _unpack_f32(row[1], DIM)
        flat = _unpack_f32(row[2], DSUB)
        self.pq = [flat[m * KSUB:(m + 1) * KSUB] for m in range(M)]
        return True

    @property
    def trained(self) -> bool:
        return bool(self.coarse) and bool(self.pq)

    # -- training -----------------------------------------------------------

    # Training is the cost centre, not search. Lloyd's algorithm in pure Python
    # is O(iters * k * n * d) of interpreter-level float work, and at 6,000
    # vectors it does not finish in a useful time. So the sample is small and
    # fixed: a codebook describes a *distribution*, and 1,500 points describe
    # one as well as a million. The corpus can then grow without retraining —
    # `add()` only encodes, it never re-fits.
    TRAIN_SAMPLE = 1536
    COARSE_ITERS = 10
    SUB_ITERS = 6

    def train(self, vecs: list, nlist: int = None) -> dict:
        """Fit the coarse quantiser and the M subspace codebooks.

        nlist defaults to ~sqrt(n), the usual heuristic: it balances the cost
        of scanning centroids against the cost of scanning a cell's contents.
        """
        n = len(vecs)
        if n < 2:
            return {"trained": False, "why": "need at least 2 vectors, have %d" % n}
        fit = vecs
        if n > self.TRAIN_SAMPLE:
            rs = random.Random(5)
            fit = [vecs[i] for i in rs.sample(range(n), self.TRAIN_SAMPLE)]
        nlist = nlist or max(1, min(int(math.sqrt(len(fit))) + 1, len(fit) // 2 or 1))

        self.coarse = _kmeans(fit, nlist, iters=self.COARSE_ITERS, seed=11)
        self.nlist = len(self.coarse)

        # Each subspace gets its own codebook. Training them jointly would be a
        # single 512-d quantiser, which is the thing PQ exists to avoid: 256
        # centroids cannot cover 512 dimensions, but they cover 64 well.
        self.pq = []
        for m in range(M):
            lo = m * DSUB
            sub = [v[lo:lo + DSUB] for v in fit]
            distinct = len(set(tuple(s) for s in sub))
            k = min(KSUB, max(1, min(distinct, len(sub) // 3) or 1))
            self.pq.append(_kmeans(sub, k, iters=self.SUB_ITERS, seed=23 + m))
            while len(self.pq[m]) < KSUB:      # pad so a code byte is always valid
                self.pq[m].append(list(self.pq[m][-1]))

        import time
        coarse_blob = _pack_f32(self.coarse)
        pq_blob = _pack_f32([c for book in self.pq for c in book])
        self.con.execute(
            "INSERT OR REPLACE INTO ann_codebook (id,nlist,coarse,pq,trained_on,built_at)"
            " VALUES (1,?,?,?,?,?)",
            (self.nlist, coarse_blob, pq_blob, n, time.time()))
        self.con.commit()

        # Adopt the float32 round-trip immediately. k-means works in float64 and
        # the codebook persists as float32, so an index that keeps its training
        # precision in memory ranks fractionally differently from the same index
        # after a reload — a reproducibility bug that only appears once the
        # process restarts, which is the worst time to find it. Reading back
        # what was just written makes the two indistinguishable by construction.
        self.coarse = _unpack_f32(coarse_blob, DIM)
        flat = _unpack_f32(pq_blob, DSUB)
        self.pq = [flat[m * KSUB:(m + 1) * KSUB] for m in range(M)]
        return {"trained": True, "n": n, "fit_on": len(fit), "nlist": self.nlist,
                "bytes_per_vector": CODE_BYTES, "subspaces": M, "dsub": DSUB}

    # -- encoding -----------------------------------------------------------

    def _cell_of(self, v: list) -> int:
        bi, bd = 0, float("inf")
        for i, c in enumerate(self.coarse):
            d = _l2sq(v, c)
            if d < bd:
                bi, bd = i, d
        return bi

    def encode(self, v: list) -> bytes:
        out = bytearray(M)
        for m in range(M):
            lo = m * DSUB
            book = self.pq[m]
            bi, bd = 0, float("inf")
            for ci in range(len(book)):
                d = _l2sq(v, book[ci], lo, 0, DSUB)
                if d < bd:
                    bi, bd = ci, d
            out[m] = bi
        return bytes(out)

    def add(self, ref: str, v: list) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO ann_code (ref, cell, code) VALUES (?,?,?)",
            (ref, self._cell_of(v), self.encode(v)))

    # -- search -------------------------------------------------------------

    def search(self, q: list, k: int = 32, nprobe: int = None) -> list:
        """Return [(ref, approx_distance)] nearest first.

        nprobe defaults to a quarter of the cells, minimum 2 — deliberately
        generous. This index is measured in thousands of rows, where the cost
        of an extra probe is microseconds and the cost of a missed neighbour is
        an agent re-deriving something it already knew.
        """
        if not self.trained:
            return []
        nprobe = nprobe or max(2, self.nlist // 4)

        cells = sorted(range(self.nlist), key=lambda i: _l2sq(q, self.coarse[i]))[:nprobe]

        # Asymmetric distance table: query stays float, only the corpus is coded.
        # 8 x 256 distances computed once, then every candidate is 8 lookups.
        tbl = []
        for m in range(M):
            lo = m * DSUB
            book = self.pq[m]
            tbl.append([_l2sq(q, book[ci], lo, 0, DSUB) for ci in range(len(book))])

        qs = ",".join("?" * len(cells))
        rows = self.con.execute(
            "SELECT ref, code FROM ann_code WHERE cell IN (%s)" % qs, cells).fetchall()

        out = []
        for ref, code in rows:
            if len(code) != M:
                continue
            d = 0.0
            for m in range(M):
                d += tbl[m][code[m]]
            out.append((ref, d))
        out.sort(key=lambda t: t[1])
        return out[:k]

    def stats(self) -> dict:
        row = self.con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT cell) FROM ann_code").fetchone()
        meta = self.con.execute(
            "SELECT nlist, trained_on FROM ann_codebook WHERE id=1").fetchone()
        n = row[0] or 0
        return {
            "vectors": n,
            "cells_used": row[1] or 0,
            "nlist": meta[0] if meta else 0,
            "trained_on": meta[1] if meta else 0,
            "bytes_per_vector": CODE_BYTES,
            "index_bytes": n * CODE_BYTES,
            "flat_float_bytes": n * DIM * 4,
            "vs_binary": "%.0fx smaller" % (64.0 / CODE_BYTES) if n else "-",
            "vs_float32": "%.0fx smaller" % (DIM * 4.0 / CODE_BYTES) if n else "-",
        }
