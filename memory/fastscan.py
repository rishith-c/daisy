"""
Bit-sliced Hamming scan — the vector search that pure Python can actually run.

This module exists because the obvious upgrade failed, and the failure is worth
recording rather than hiding.

**What was tried.** A real IVF-PQ index (`memory/ann.py`): k-means coarse
quantiser, 8 subspaces, 256-centroid codebooks, 8 bytes per vector, asymmetric
distance tables. It works and it is faithful — measured 94.3% recall@10 against
exhaustive cosine on 596 real vectors from this machine, at 8x smaller than the
binary codes it replaces and 256x smaller than float32.

**Why it is not the default.** Encoding one vector costs
`M * K * dsub = 8 * 256 * 64` float comparisons — 131,072 interpreter-level
operations *per vector*. At 3,000 vectors that is ~400M Python-level float ops
and it does not finish in any time worth waiting for. The constraint that binds
this project is zero third-party dependencies, which means no numpy, which
means every float multiply is a bytecode dispatch. PQ is the right structure
for C. It is the wrong structure for CPython.

**What works instead.** Give the work to C by making it bignum arithmetic.
Python's arbitrary-precision integers are implemented in C, and `int.bit_count()`
is a single C call regardless of width. So:

  * every 64-byte binary code is stored contiguously in one blob
  * the query code is tiled to the same length — one `bytes` allocation
  * the whole corpus is XORed against it in **one** operation, however many
    vectors it holds
  * each row's distance is then two C calls: a slice and a `bit_count()`

The inner loop of 64 Python iterations per row becomes zero. What was
O(n * 64) bytecode dispatches becomes O(n) C calls over a single contiguous
buffer, and the XOR itself is one.

This is not a better algorithm — it is the same linear scan. It is a better
*implementation* for this runtime, which is the distinction that mattered here:
the bottleneck was never the asymptotics at this scale, it was the constant,
and the constant is where the interpreter lives.

Exact rescore on the survivors is unchanged. Approximate ranking still gets
checked against the true float vector before anything is returned.

Zero third-party dependencies.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precedent.engine import BYTES, DIM, cosine, pack_bits  # noqa: E402


class Slab:
    """A contiguous wall of binary codes, scanned as one integer."""

    __slots__ = ("refs", "blob", "n", "_tile")

    def __init__(self):
        self.refs = []
        self.blob = b""
        self.n = 0
        self._tile = None

    # -- build --------------------------------------------------------------

    def add_many(self, pairs) -> int:
        """pairs: iterable of (ref, 64-byte code). Rebuilt in one pass."""
        refs, parts = list(self.refs), [self.blob]
        for ref, code in pairs:
            if not isinstance(code, (bytes, bytearray)) or len(code) != BYTES:
                continue                      # a malformed row is skipped, not fatal
            refs.append(ref)
            parts.append(bytes(code))
        self.refs = refs
        self.blob = b"".join(parts)
        self.n = len(refs)
        self._tile = None                     # the query tile depends on n
        return self.n

    # -- search -------------------------------------------------------------

    def scan(self, qcode: bytes, k: int = 32) -> list:
        """Return [(ref, hamming)] nearest first.

        The whole corpus is XORed against the tiled query in a single bignum
        operation. Everything after that is slicing and popcounts.
        """
        if not self.n or len(qcode) != BYTES:
            return []

        if self._tile is None or len(self._tile) != len(self.blob):
            self._tile = qcode * self.n
        elif self._tile[:BYTES] != qcode:
            self._tile = qcode * self.n

        # One operation over the entire corpus, executed in C.
        x = (int.from_bytes(self.blob, "big") ^ int.from_bytes(self._tile, "big"))
        xb = x.to_bytes(len(self.blob), "big")

        out = []
        bs = BYTES
        for i in range(self.n):
            # two C calls per row; no Python-level loop over the 64 bytes
            out.append((self.refs[i], int.from_bytes(xb[i * bs:(i + 1) * bs], "big").bit_count()))
        out.sort(key=lambda t: t[1])
        return out[:k]

    def rescore(self, qvec: list, shortlist: list, vec_of, k: int = 10) -> list:
        """Exact cosine over the shortlist.

        The point of the shortlist is that this stays cheap. The point of *this*
        is that an approximate distance never leaves the module presented as a
        real one.
        """
        out = []
        for ref, ham in shortlist:
            v = vec_of(ref)
            if not v:
                continue
            out.append((ref, cosine(qvec, v), ham))
        out.sort(key=lambda t: -t[1])
        return out[:k]

    def stats(self) -> dict:
        return {
            "vectors": self.n,
            "bytes_per_vector": BYTES,
            "slab_bytes": len(self.blob),
            "float32_equivalent": self.n * DIM * 4,
            "compression": ("%.0fx" % (DIM * 4.0 / BYTES)) if self.n else "-",
        }


def build_from(con, table: str, id_col: str, vec_col: str, limit: int = None) -> Slab:
    """Load a slab straight out of a SQLite table of float32 vector blobs."""
    slab = Slab()
    sql = "SELECT %s, %s FROM %s WHERE %s IS NOT NULL" % (id_col, vec_col, table, vec_col)
    if limit:
        sql += " LIMIT %d" % int(limit)
    pairs = []
    for rid, blob in con.execute(sql):
        if isinstance(blob, (bytes, bytearray)) and len(blob) == DIM * 4:
            v = list(struct.unpack("<%df" % DIM, blob))
            pairs.append(("%s:%s" % (table, rid), pack_bits(v)))
        elif isinstance(blob, (bytes, bytearray)) and len(blob) == BYTES:
            pairs.append(("%s:%s" % (table, rid), bytes(blob)))
    slab.add_many(pairs)
    return slab
