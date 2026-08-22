# Sponsor integrations — what runs, and what does not

Stated precisely, because two of the three were verified against live accounts
and the third was not, and a submission that blurs those is worth less than one
that separates them.

| | State | Evidence |
|---|---|---|
| **Bright Data** | **live** | scraper built from plain English, 32 rows of real JSON |
| **Port** | **live** | real org, blueprints created, approval blocked then granted |
| **SigNoz** | instrumented, **not run** | 179 tests, verified against a stand-in OTLP receiver |

---

## Bright Data — live

Scraper Studio, entirely from the terminal, no dashboard:

```bash
npx -y @brightdata/cli@latest scraper create \
  "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html" \
  "extract each book: title, price in GBP, star rating, availability text" \
  --name daisy-demo-scraper
# -> collector_id: c_mt503aoe22ii2ojmaj

npx -y @brightdata/cli@latest scraper run c_mt503aoe22ii2ojmaj <url>
```

Real output, 32 rows:

```json
{"title":"A Study in Scarlet (Sherlock Holmes #1)",
 "price":{"value":16.73,"currency":"GBP","symbol":"£"},
 "rating":"Two","availability":"In stock (14 available)"}
```

It inferred structure nobody asked for — splitting price into value/currency/
symbol and pulling the product URL.

**Worth knowing:** Web Unlocker and SERP both require a payment method on the
account, even with credits available; zone creation returns
`payment_method_required`. Scraper Studio does not. That is the path.

Alongside it, `scrape/` implements the same self-healing shape locally and
without an API: drift detection on required-field fill rate rather than HTTP
status (a 200 with fewer keys per row is the failure that actually matters),
then selectors re-derived by anchoring on last-good **values** instead of
markup. Both paths stop at a human approval.

## Port — live

Blueprints created in a real org; a real run gated and approved:

```bash
python3 -m port.cli bootstrap                       # 17 live calls
python3 -m port.cli --run judge1615 plan            # plan BEFORE any agent
python3 -m port.cli --run judge1615 gate --lane hardware --name physics.bend --margin 1.51
python3 -m port.cli --run judge1615 approve --request   # blocks
```

Measured: run `judge1615`, 4 gates, 0 failed, scorecard **Silver**, approval
blocked until a human granted it.

Four properties, each tested rather than asserted:

- **no plan, no agents.** A gate with no committed plan is refused `NotPlanned`.
  Delete Port and nothing starts.
- **the approval blocks.** It does not notify and continue.
- **timeout is not consent.** A decision that never arrives returns `timeout`.
- **the factory cannot sign its own work.** `--by claude|codex|labctl` is
  refused `SelfApproval`.

## SigNoz — instrumented, not run

**We did not run a SigNoz instance.** Self-hosting needs Docker and a
multi-gigabyte image pull, and this machine had 5.4 GB free. Rather than claim
a dashboard we never opened, here is exactly what exists and what would happen
the moment an endpoint appears.

**What is built** — `obs/`, 2,085 lines, pure standard library, **179 tests**:

- OTLP/HTTP **JSON** exporter for traces, metrics and logs — no protobuf, no
  `opentelemetry-sdk`, nothing to install
- W3C trace/span ids, a per-thread context stack, status and exception
  recording, a bounded batcher on a daemon flush thread
- failure and repair as **first-class spans** (`gate.fail`, `scrape.repair`,
  `human.escalation`), each tagged `event.kind` *and* counted — the span answers
  "what happened in this run", the counter answers "how often does this happen
  at all", and neither substitutes for the other
- an **offline spool** as a first-class path, not a fallback

**How we verified it without a server.** A stand-in OTLP receiver on
`127.0.0.1:4318` recorded exactly what the exporter sends:

```
endpoints hit : ['/v1/logs', '/v1/metrics', '/v1/traces']
ingestion key : (none sent — correct for self-hosted)
spans         : 6 · metric posts: 2 · log posts: 1
```

Correct paths, correct absence of the ingestion-key header when none is
configured. The wire format is right; only the receiver is missing.

**What a real run produces.** `tools/traced_verify.py` runs the actual gates
inside spans. With the vendor page restructured:

```
factory.verify
  gate.taste.t1              pass
  gate.physics.bend          FAIL   -> repair.solve_thickness -> rerun pass
  gate.scrape.schema         FAIL   <- the website changed
  scrape.repair                     [scrape.repair]
  gate.physics.fastener      FAIL   "cannot certify — no scraped rows"
  human.escalation                  [human.escalation]
```

A website changing its HTML propagates to a physical part that can no longer be
signed off, visible in one tree.

**To point it at a live instance** — no account, no key, SigNoz is Apache-2.0:

```bash
git clone -b main https://github.com/SigNoz/signoz.git
cd signoz/deploy/docker && docker compose up -d       # UI :3301, OTLP :4318

echo 'SIGNOZ_ENDPOINT=http://localhost:4318' >> .env.local
./tools/withenv.sh python3 -m obs.cli replay
```

`replay` is the part that matters. Every run so far spooled to
`obs/spool/*.jsonl` rather than dropping signals, so the moment an endpoint
exists the whole accumulated history uploads at once — you open SigNoz to a
populated dashboard, not an empty one. That is why the offline path was built
as a real path instead of a degraded mode.

Requires roughly 4 GB of free disk for the images (ClickHouse, Zookeeper,
query-service, frontend, collector).
