# Daisy 🌼

Daisy is the command center for **THE LAB: Workbench** — an agentic software factory built for the WeMakeDevs Zero Downtime Hackathon (Aug 22, 2026).

One brief in → verified software **and** verified hardware out. Claude Code and Codex build in parallel git worktrees; every artifact passes gates a human can audit line by line: a three-tier taste ladder for frontends, closed-form physics margins for parts. Port governs, Bright Data feeds, SigNoz escalates.

---

## What's in here

```
index.html          the whole app — one file, zero dependencies, zero network
precedent/          the Precedent Engine (see below) — pure stdlib Python
taste/              the taste gate as programs — tier 1 lint, tier 2 contrast
hardware/           the physics gate + parametric geometry, as programs
app/                native macOS wrapper (Swift + WKWebView)
icon/               generators for the hand-drawn daisy icon and brand mark
tools/              stats builder, view generators, refinement timer (see tools/README.md)
verify.sh           every gate Daisy claims to enforce, run against Daisy
```

Run all of it:

```bash
./verify.sh          # exit code = number of failing gates
```

## The app

A command center in the Codex-desktop design language, **light-first**: serif agent narration, near-black-on-white chrome, colour reserved for meaning.

- **Live run view** — the full factory run with real human approval gates (clarify → Port approve → escalation). Streaming word-level narration, diff cards, gate tables, an algebraic-repair derivation, and a span flame graph.
- **CREW bay** — Claude and Codex side by side, each in its own worktree, both pinned to one `api-contract.json`. Neither can touch git; the orchestrator owns every commit, so the diffs stay symmetric. They are combined by a contract, not by a conversation.
- **Proportional minimap** down the left edge, colour-coded by gate outcome, click to jump.
- **Runs** — aggregate windows plus ten replayable snapshots, including a red escalated run so a judge can diagnose a failure from history.
- **Scraper heal** — the whole auto-repair loop as its own watchable thread: the vendor restructures its table, the collector *does not error* (it quietly returns fewer keys), the schema key-diff catches it, `scraper heal` runs, the verifier checks the preview **before** a human sees it, and the healed selectors are committed to `CLAUDE.md` rather than a dashboard.
- **Review queue** — nothing merges itself. Diffs, scorecards, smoke-boot status, how long each item has waited, and a breakdown of what a green scorecard actually covers.
- **Precedent** — the innovative core, below.
- **Skills** — the mounted skill files, the factory's own `CLAUDE.md` (scrapers, agent routing, gate list, and the laws the orchestrator enforces), and the taste gate applied to Daisy itself
- **Artifacts / Automations**, ⌘K command palette, and `daisy-theme-v1` appearance settings (chat font, code font, accent, motion) persisted to localStorage.

### Craft notes

Values are taken from the real Codex webview palette rather than approximated (with two deliberate departures, both forced by the tier-2 contrast gate: the added-green and the dark deleted-red are darkened/lightened from the shipped `gitDecoration` values, which are tuned for file labels on plain ground and fail at 10.5px bold on a tinted chip): `--color-token-*` chains, the `0 0 0 .5px` elevation-stroke recipe, `rounded-lg` = 10px cards against a 20px composer, `superellipse(1.5)` corners, git-decoration diff colours, 28px buttons, 36px file rows, and `cursor: default` — because Mac apps don't use pointing-hand cursors.

Motion follows the same discipline: `ease-out` for enter and exit and never `ease-in`; springs generated from Apple's `.spring(duration:bounce:)` mapping; View Transitions for directional view changes; `@starting-style` transitions rather than keyframes so interrupted animations retarget instead of restarting; a wall-clock stagger budget so streaming never builds an invisible queue. The command palette deliberately has **no** open animation — it's used dozens of times a day, and Raycast ships it that way for the same reason.

---

## The taste gate

Two of the three tiers are real programs, not prompts.

### Tier 1 — `taste/lint.py` Pure stdlib, milliseconds, zero tokens. Twenty named tells, each
reported with `file:line` and a reason:

```
taste.t1  FAIL  tokens.css  —  2 findings
  gate 3   indigo primary         tokens.css:12   the Tailwind default accent; the most common generated-UI tell
  gate 11  unpaired default face  layout.tsx:8    a lone Inter stack with no paired display face
```

Named gates, not a score. "Your design scores 6.5/10" gives an agent nothing to act
on; `gate 3 at tokens.css:12` can be injected straight back into a resumed session.
The exit code is the finding count, so it drops into a gate runner unchanged.

**Daisy is held to it.** `python3 -m taste.lint index.html` returns 0 findings, and
one of the 26 linter tests is exactly that assertion. A gate you exempt yourself from
is not a gate.

Running it against itself immediately found two bugs in the linter: it was flagging
typographic glyphs (`✓`, `σ`, `≥`) as emoji when those inherit `currentColor` and are
not the tell, and it had no suppression mechanism, so a file that *documents* the
tells could never pass. Both fixed; fixtures that quote a tell now carry an explicit
`taste-ok` marker.

### Tier 2 — `taste/contrast.py`

Computes what an eye cannot judge. It reads the design tokens out of the stylesheet,
resolves `rgba()` over its surface and composites `color-mix()`, and checks the WCAG
ratio of every declared text-on-surface pair **in both themes**.

It found **seven real WCAG failures** in this file on its first run — every one of
which looked fine on screen, including three I had already "fixed" by hand after
eyeballing a smaller sample:

```
light  diff additions       --add-ink  on --surface    3.36:1  (needs 4.5)
light  disabled / hint      --ink-4    on --bg         2.13:1  (needs 3.0)
dark   FAIL chip            --fail     on --del-bg     3.98:1  (needs 4.5)
…
```

Now: `28 pairs checked, 0 failing`, tightest 3.11:1. Chip grounds are their own
tokens (`--pass-chip`, `--fail-chip`, …) specifically so the stylesheet and the
checker's pair table cannot silently drift apart.

---

## The physics gate

`hardware/margins.py` — the other half of the factory's test suite, and the third
thing here that is a program rather than a narration. Closed-form beam bending,
bolt shear, thermal rise, mass properties, and a fastener selection driven by the
scraped vendor rows. Sub-millisecond, deterministic, **no model anywhere near the
decision** — the thing deciding whether a part is safe should not be the thing
that wrote the part.

The repair is solved, not guessed:

```
sigma = 6M / (b·t²)   ⇒   t = √( 6M / (b · σ_allow / FoS) )
```

Two things it caught immediately when I pointed it at the UI:

- **The narrated numbers were not physically real.** The run claimed 212 MPa at
  FoS 0.82; the actual load case gives 69.0 MPa at FoS 0.72 in PETG. Every figure
  in the run is now generated by the engine (`tools/sync_physics.py`).
- **The repair could fail its own gate.** `solve_thickness` rounded to nearest,
  so a solved thickness could land fractionally *under* target — the repair
  failing the very gate that asked for it, and looping. It rounds up now. You
  never round a safety margin down.

Scope is stated in the module and not overclaimed: this is first-principles
statics, **not** FEA, fatigue, buckling, impact, or stress concentration. For a
bracket in single-axis static bending those omissions are defensible; for
anything cyclic or impact-loaded they are not, and it says so.

It generates real geometry too. `hardware/bracket.py` is a parametric L-bracket
that writes a binary STL and computes mass from the actual solid:

```bash
python3 -m hardware.bracket --thickness 4.61 --out bracket_v2.stl
```

The two volume calculations check each other — the analytic solid and the mesh
(by the divergence theorem) differ by exactly the volume of the bolt holes,
which the mesh does not cut. If they ever disagree by anything else, one of them
is wrong. Hand-built geometry is the honest choice for convex slab parts like
this and avoids assuming an OCCT toolchain on a judge's laptop; for fillets,
lofts or booleans you want a kernel, and the module says so.

```bash
python3 -m hardware.test_margins    # 35 tests
```

---

## The Precedent Engine

**The factory cites its own case law.**

Every gate failure it has ever recorded is archived, compressed and made searchable, so a repeat failure is *fixed from precedent* instead of rediscovered by an LLM. What makes it different from ordinary RAG over logs:

> the sparse half of the retrieval score is **deterministic verification state** — which gates failed, and in which severity band — not text similarity.

Retrieval is therefore grounded in facts the factory actually proved.

**Gate-Signature Hybrid Retrieval**, four tiers, cheapest first:

| tier | mechanism | measured |
|---|---|---|
| T0 | structural fingerprint (volatile tokens normalised → blake2b) | **0.012 ms**, exact |
| T1 | Jaccard over the gate signature | deterministic |
| T2 | BM25 over the failure narrative (SQLite FTS5, stopword-filtered) | lexical |
| T3 | 512-d vectors stored as **64 bytes** (1 bit/dim), exact-rescored on a Hamming shortlist | **32×** smaller |

fused with Reciprocal Rank Fusion (k=60), then rescored with a recency / importance / relevance blend.

**It is allowed to say no.** Scores are anchored on *absolute* evidence, never normalised rank — because rank normalisation rates the least-bad candidate 1.0 and cites it confidently. A genuinely novel failure returns nothing.

**Compaction with a conscience.** Production compactors all hit 98–99% compression and all score ~2.3/5 on artifact tracking — they forget which files were modified. Here, structured facts are extracted as rows and never summarised, and every compaction is **probe-validated**: facts are held out, the summary is quizzed on them, and a compaction that loses artifacts is rejected and retried at a lower ratio. Ratio is reported; the probe score is the gate.

### Measured

```
1,284 archived cases · 7 failure families
0.012 ms   exact-repeat lookup
   16 ms   hybrid recall (local, 0 tokens)
   80 KB   search index   (vs 2.6 MB float — 32×)
    24×    run compaction at 100% probe score
```

### Try it

```bash
python3 -m precedent.seed precedent/precedent.db      # build the archive
python3 -m precedent.cli stats
python3 -m precedent.cli families
python3 -m precedent.cli bench
python3 -m precedent.cli recall "my bracket bends too much" --gate physics.bend=0.78
python3 -m precedent.cli recall "pod evicted, memory pressure" --gate infra.oom=0.0   # -> no precedent
python3 -m precedent.cli sql "SELECT family, COUNT(*) n FROM cases GROUP BY family"
python3 -m precedent.test_precedent                   # 39 tests
python3 -m taste.test_lint                            # 26 tests
python3 -m hardware.test_margins                      # 35 tests
```

The `sql` subcommand is *memory as code*: an agent can interrogate its own past directly, read-only, instead of being handed retrieved chunks.

Regenerate the figures the UI displays:

```bash
python3 tools/build_stats.py
```

---

## Running it

Web:

```bash
python3 -m http.server 8124 --directory .
```

Native macOS app:

```bash
bash tools/build_app.sh
open Daisy.app
```

The reproducible build compiles the Cocoa/WebKit shell, copies only the Python
packages and static resources Daisy needs, excludes caches, databases, spools,
and credentials, ad-hoc signs the result, then verifies the signature. The
native build tags its user agent `DaisyNative`; the UI detects it and hides its
drawn traffic lights so the real ones take over.

## Status

## Seven-minute judge run: one chain, three sponsors

The sponsor integrations are not three logos bolted onto a dashboard. They are
three consecutive control points in the same run:

1. **Bright Data Scraper Studio supplies load-bearing evidence.** Its published
   collector returns the fastener rows consumed by the physics solver. The
   trigger response's `collection_id`, capture time, collector ID, and source
   URL stay attached as provenance. Missing, stale, or malformed rows stop
   certification; there is no fallback price table.
2. **Port is the governance boundary.** `labctl` writes the Brief, Lane, and Run
   entities before a lane executes, writes the deterministic gate entities
   afterward, lets Port's scorecard reduce them, then opens a plan-hash-bound
   human approval. In offline mode the same requests enter a tamper-evident
   replay spool and are labelled `dry`.
3. **Open-source SigNoz explains the whole run.** Daisy emits OTLP/HTTP JSON
   traces, metrics, and logs to a self-hosted collector. The Bright Data fetch,
   failed physics gate, algebraic repair, rerun, Port commit, and human wait are
   correlated in one trace. If the collector is down, the exact OTLP payloads
   spool and replay later.

The no-credential rehearsal exercises the same decisions and says what stayed
offline:

```bash
./verify.sh
python3 labctl.py run --run-id judge-rehearsal \
  --brief "Verify a 2.4 kg SR-11 sensor bracket" \
  --lane hardware --lane scrape --json
python3 -m port.cli status --run judge-rehearsal
python3 -m obs.cli tail -n 12
```

To show Bright Data's break-detect-repair loop without changing repository
state:

```bash
python3 -m scrape.cli fetch --fixture vendor_v1.html
python3 -m scrape.cli check --fixture vendor_v1_partial.html
python3 -m scrape.cli repair --fixture vendor_v2.html \
  --proposal /tmp/daisy-rules.proposed.json
```

For live Scraper Studio, configure the published collector once. `labctl` then
uses it automatically because no `--fixture` was supplied:

```bash
export BRIGHT_DATA_API_TOKEN="..."
export BRIGHT_DATA_COLLECTOR_ID="c_..."
python3 -m scrape.cli fetch
```

For live Port, bootstrap the blueprints, scorecard, action, and service catalog
before the run. The same `labctl` command will switch from a dry spool to live
entity writes:

```bash
export PORT_CLIENT_ID="..."
export PORT_CLIENT_SECRET="..."
python3 -m port.cli bootstrap
python3 labctl.py run --run-id judge-live \
  --brief "Verify a 2.4 kg SR-11 sensor bracket" \
  --lane hardware --lane scrape --json
```

Point Daisy at a local open-source SigNoz OTLP/HTTP receiver (community/self-
hosted installs require no ingestion key), or at SigNoz Cloud with its key:

```bash
export SIGNOZ_ENDPOINT="http://localhost:4318"
python3 tools/traced_verify.py
python3 -m obs.cli replay
```

With all variables set, the live `labctl` command is the demo: Bright Data
creates the evidence snapshot, Port owns the run and approval, and SigNoz owns
the correlated operational record. The output always states `live`, `dry`, or
`spool`; those words are evidence states, not styling.

## What is real, and what is not

Stated plainly, because a factory that overstates itself is the thing this
project exists to argue against.

**Real, tested, and runnable right now — deterministic gates and `./verify.sh`**

| Piece | What it does |
|---|---|
| `hardware/` | Closed-form margins. Every number the UI shows is generated by this, after it caught the narrated figures being fabricated |
| `taste/` | Tier-1 design lint (20 tells) and tier-2 computed WCAG contrast (34 pairs, both themes) |
| `precedent/` | Gate-signature hybrid retrieval with an absolute evidence floor, so a novel failure returns nothing |
| `commons/` | Cross-agent solution reuse where the price of admission is a passing gate signature |
| `agents/` | Read-only adoption of Claude Code / Codex / OpenCode sessions already on the machine |
| `obs/` | OTLP/JSON to SigNoz, with an offline spool as a first-class path |
| `scrape/` | Selector drift detection and value-anchored auto-repair |
| `port/` | Plan committed before execution; an approval that actually blocks |
| `lab/` + `labctl.py` | The orchestrator. Brief in, verified artifacts out |

**Real but dependent on your machine.** `labctl agents` probes each coding
agent rather than trusting a config file, because availability genuinely
varies: a CLI can be installed with an expired session, or authenticated but
older than the model its own config selects. The probe reports which, and the
software lane declines to run rather than failing halfway.

**Not live without credentials.** Port and Bright Data run in dry/fixture mode;
SigNoz writes an offline spool. None ever phrases a spooled request as live.
Self-hosted SigNoz needs only its OTLP endpoint; Port and Scraper Studio each
need the two environment variables shown above.

**Simulated.** The run playback in the UI is a scripted replay of run 1042 —
it is a demo of the interface, not a live execution. `labctl.py` is the part
that actually runs.
