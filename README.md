# Daisy 🌼

Daisy is the command center for **THE LAB: Workbench** — an agentic software factory built for the WeMakeDevs Zero Downtime Hackathon (Aug 22, 2026).

One brief in → verified software **and** verified hardware out. Claude Code and Codex build in parallel git worktrees; every artifact passes gates a human can audit line by line: a three-tier taste ladder for frontends, closed-form physics margins for parts. Port governs, Bright Data feeds, SigNoz escalates.

---

## What's in here

```
index.html          the whole app — one file, zero dependencies, zero network
precedent/          the Precedent Engine (see below) — pure stdlib Python
taste/              the tier-1 design lint — the taste gate, as a program
app/                native macOS wrapper (Swift + WKWebView)
icon/               generators for the hand-drawn daisy icon and brand mark
tools/              stats builder, six-hour refinement timer
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
- **Review queue** — nothing merges itself. Diffs, scorecards, smoke-boot status.
- **Precedent** — the innovative core, below.
- **Skills** — the mounted skill files, the factory's own `CLAUDE.md` (scrapers, agent routing, gate list, and the laws the orchestrator enforces), and the taste gate applied to Daisy itself
- **Artifacts / Automations**, ⌘K command palette, and `daisy-theme-v1` appearance settings (chat font, code font, accent, motion) persisted to localStorage.

### Craft notes

Values are taken from the real Codex webview palette rather than approximated: `--color-token-*` chains, the `0 0 0 .5px` elevation-stroke recipe, `rounded-lg` = 10px cards against a 20px composer, `superellipse(1.5)` corners, git-decoration diff colours (`#00a240` / `#ba2623` light), 28px buttons, 36px file rows, and `cursor: default` — because Mac apps don't use pointing-hand cursors.

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
swiftc -O -o app/Daisy app/main.swift -framework Cocoa -framework WebKit
mkdir -p Daisy.app/Contents/MacOS Daisy.app/Contents/Resources
cp app/Daisy Daisy.app/Contents/MacOS/ && cp app/Info.plist Daisy.app/Contents/
cp index.html icon/AppIcon.icns Daisy.app/Contents/Resources/
codesign --force --deep -s - Daisy.app && open Daisy.app
```

The native build tags its user agent `DaisyNative`; the UI detects it and hides its drawn traffic lights so the real ones take over.

## Status

The UI is complete and the Precedent Engine is real, tested, and benchmarked. The orchestrator behind them (`labctl`, live agent processes, Port / Bright Data / SigNoz wiring) is built at the event — standalone, Daisy plays a ghost-bay replay of run 1042.
