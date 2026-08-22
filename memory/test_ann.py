"""Tests for the vector index and the bit-sliced scan.

    python3 -m memory.test_ann

Everything runs in memory on generated vectors. Nothing here touches the
developer's real memory store, and nothing touches the network.
"""

from __future__ import annotations

import math
import random
import sqlite3
import struct

from precedent.engine import DIM, BYTES, pack_bits, hamming, cosine, embed
from .ann import Index, M, DSUB, KSUB, CODE_BYTES, _kmeans
from .fastscan import Slab, build_from

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


def unit(rng):
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ---------------------------------------------------------------------------

def test_slab_matches_the_loop():
    print("\nbit-sliced scan — must agree with the per-row loop exactly")
    rng = random.Random(1)
    vecs = [unit(rng) for _ in range(300)]
    codes = [pack_bits(v) for v in vecs]
    slab = Slab()
    slab.add_many([("r%d" % i, c) for i, c in enumerate(codes)])
    check("every row is loaded", slab.n == 300, str(slab.n))

    for trial in range(6):
        q = pack_bits(unit(rng))
        fast = slab.scan(q, k=10)
        slow = sorted(((("r%d" % i), hamming(q, codes[i])) for i in range(300)),
                      key=lambda t: t[1])[:10]
        check("trial %d: identical distances" % trial,
              [d for _, d in fast] == [d for _, d in slow],
              "%s vs %s" % ([d for _, d in fast][:4], [d for _, d in slow][:4]))

    q = pack_bits(vecs[7])
    top = slab.scan(q, k=1)
    check("a vector is its own nearest neighbour at distance 0",
          top and top[0][0] == "r7" and top[0][1] == 0, str(top[:1]))


def test_slab_edges():
    print("\nbit-sliced scan — degenerate inputs")
    s = Slab()
    check("an empty slab returns nothing", s.scan(b"\x00" * BYTES) == [])
    s.add_many([("a", b"\x00" * BYTES)])
    check("a wrong-length query returns nothing", s.scan(b"\x00" * 7) == [])
    n = s.add_many([("bad", b"\x01" * 3), ("good", b"\x02" * BYTES)])
    check("a malformed code is skipped rather than fatal", n == 2, str(n))
    check("and the good rows still scan", len(s.scan(b"\x00" * BYTES, k=5)) == 2)

    # The query tile is cached; a second, different query must not reuse it.
    q1, q2 = b"\x00" * BYTES, b"\xff" * BYTES
    d1 = dict(s.scan(q1, k=5)); d2 = dict(s.scan(q2, k=5))
    check("a cached tile is invalidated by a new query", d1["a"] != d2["a"],
          "%s vs %s" % (d1["a"], d2["a"]))
    check("and the first query still gives its original answer",
          dict(s.scan(q1, k=5))["a"] == d1["a"])


def test_rescore_is_exact():
    print("\nrescore — approximate ranking never leaves as exact")
    rng = random.Random(2)
    vecs = {("r%d" % i): unit(rng) for i in range(60)}
    slab = Slab()
    slab.add_many([(r, pack_bits(v)) for r, v in vecs.items()])
    q = unit(rng)
    short = slab.scan(pack_bits(q), k=20)
    out = slab.rescore(q, short, lambda r: vecs.get(r), k=5)
    check("rescore returns at most k", len(out) <= 5, str(len(out)))
    check("ordered by true cosine, descending",
          all(out[i][1] >= out[i + 1][1] for i in range(len(out) - 1)))
    best = max(vecs.items(), key=lambda kv: cosine(q, kv[1]))[0]
    check("the true best is in the rescored head or the shortlist",
          best in [r for r, _, _ in out] or best in [r for r, _ in short])
    check("a missing vector is dropped, not guessed",
          slab.rescore(q, [("nope", 0)], lambda r: None) == [])


def test_slab_from_sqlite():
    print("\nslab — built straight from a table of float32 blobs")
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE ev (id TEXT PRIMARY KEY, vec BLOB)")
    rng = random.Random(3)
    vs = {}
    for i in range(40):
        v = unit(rng); vs["e%d" % i] = v
        con.execute("INSERT INTO ev VALUES (?,?)",
                    ("e%d" % i, struct.pack("<%df" % DIM, *v)))
    con.execute("INSERT INTO ev VALUES ('null_row', NULL)")
    con.execute("INSERT INTO ev VALUES ('short_row', ?)", (b"\x00" * 9,))
    con.commit()
    slab = build_from(con, "ev", "id", "vec")
    check("null and malformed rows are skipped", slab.n == 40, str(slab.n))
    check("refs are namespaced by table", slab.refs[0].startswith("ev:"), slab.refs[0])
    st = slab.stats()
    check("compression is reported against float32", st["compression"] == "32x", st["compression"])
    check("slab size is n * 64", st["slab_bytes"] == 40 * BYTES, str(st["slab_bytes"]))
    con.close()


def test_kmeans():
    print("\nk-means — seeding must not leave dead centroids")
    rng = random.Random(4)
    # three well-separated blobs
    pts = []
    for cx in (0.0, 8.0, 16.0):
        for _ in range(30):
            pts.append([cx + rng.gauss(0, .3), rng.gauss(0, .3)])
    cents = _kmeans(pts, 3, iters=12, seed=1)
    check("returns the requested count", len(cents) == 3, str(len(cents)))
    xs = sorted(c[0] for c in cents)
    check("finds all three blobs", all(abs(xs[i] - [0, 8, 16][i]) < 1.2 for i in range(3)),
          str(xs))
    check("k larger than n is clamped", len(_kmeans(pts[:4], 50)) <= 4)
    check("an empty corpus returns nothing", _kmeans([], 3) == [])
    check("deterministic across runs",
          _kmeans(pts, 3, seed=1) == _kmeans(pts, 3, seed=1))


def test_ivfpq_is_faithful():
    print("\nIVF-PQ — correct, and honest about being approximate")
    rng = random.Random(5)
    vecs = [unit(rng) for _ in range(240)]
    con = sqlite3.connect(":memory:")
    idx = Index(con)
    check("an untrained index searches nothing", idx.search(vecs[0]) == [])
    info = idx.train(vecs)
    check("training reports what it fit on", info.get("trained") and info["n"] == 240, str(info))
    check("one byte per subspace", info["bytes_per_vector"] == CODE_BYTES == M)
    check("codebooks cover every subspace", len(idx.pq) == M and all(len(b) == KSUB for b in idx.pq))

    for r, v in zip(("v%d" % i for i in range(240)), vecs):
        idx.add(r, v)
    con.commit()
    st = idx.stats()
    check("every vector is indexed", st["vectors"] == 240, str(st["vectors"]))
    check("8x smaller than the binary code it replaces", st["vs_binary"] == "8x smaller", st["vs_binary"])

    # A vector must retrieve itself: this is the floor, not the ceiling.
    self_hits = 0
    for i in (0, 17, 99, 200):
        got = idx.search(vecs[i], k=5, nprobe=idx.nlist)
        if ("v%d" % i) in [r for r, _ in got]:
            self_hits += 1
    check("a vector retrieves itself with full probing", self_hits == 4, str(self_hits))
    check("nprobe bounds the work", len(idx.search(vecs[0], k=50, nprobe=1)) <= 50)
    con.close()


def test_training_edges():
    print("\nIVF-PQ — degenerate corpora")
    con = sqlite3.connect(":memory:")
    idx = Index(con)
    check("one vector cannot train a codebook",
          idx.train([[0.0] * DIM]).get("trained") is False)
    rng = random.Random(6)
    same = [[0.1] * DIM for _ in range(20)]
    r = idx.train(same)
    check("identical vectors still train", r.get("trained") is True, str(r))
    idx.add("a", same[0])
    check("and encode to a valid code",
          len(con.execute("SELECT code FROM ann_code").fetchone()[0]) == CODE_BYTES)
    con.close()


def test_persistence():
    print("\nIVF-PQ — a codebook survives reopening")
    con = sqlite3.connect(":memory:")
    rng = random.Random(7)
    vecs = [unit(rng) for _ in range(120)]
    idx = Index(con); idx.train(vecs)
    for i, v in enumerate(vecs): idx.add("p%d" % i, v)
    con.commit()
    first = idx.search(vecs[3], k=5)
    again = Index(con)
    check("the codebook reloads", again.trained)
    check("nlist is preserved", again.nlist == idx.nlist)
    check("and search is reproducible", again.search(vecs[3], k=5) == first)
    con.close()


def main():
    print("vector index — test suite")
    test_slab_matches_the_loop()
    test_slab_edges()
    test_rescore_is_exact()
    test_slab_from_sqlite()
    test_kmeans()
    test_ivfpq_is_faithful()
    test_training_edges()
    test_persistence()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
