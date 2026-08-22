# Six hours on Daisy — what changed

Started 2026-08-21 23:31, autonomous, timer in `tools/timer.py`.

You left three instructions and they drove everything: **light mode**, **more fluid
animation**, **kill the sponsor tags** — plus "research it properly, add something
genuinely new, and don't push what won't work."

---

## 1. Research first (4 agents, ~90 sources)

Four background agents ran in parallel before any code was written:

- **The Codex desktop UI.** This one paid off more than expected — the agent found a
  decompiled source dump and came back with *actual* values rather than estimates:
  `--color-token-*` chains, the `0 0 0 .5px` elevation-stroke recipe, `rounded-lg` =
  10px cards against a 20px composer, `superellipse(1.5)` corners, git-decoration diff
  colours, 28px buttons, 36px file rows, the `MAX_VISIBLE_FILES = 3` threshold, the
  800ms diff-preview delay, and the "Review changes" hover swap.
- **Award-winning Mac app craft.** 25+ sources on what separates hand-built from
  vibecoded: layered hairline shadows, the radius ladder, vibrancy with
  `saturate(180%)`, tracking that changes with size, `cursor: default` on buttons,
  and the counterintuitive one — **never animate a keyboard-initiated action**
  (Raycast ships its palette with no open animation on purpose).
- **Native-feeling web motion.** Real spring curves generated from Apple's
  `.spring(duration:bounce:)` mapping, the `linear()` gotchas, View Transitions,
  FLIP, and the streaming-text failure mode where naive staggering builds a
  16-second invisible queue.
- **Agent memory and compression.** Measured benchmarks: binary quantization at
  32× with 0.944 recall, RRF k=60, generative-agents scoring, and the finding
  that every production compactor hits 98–99% compression while scoring ~2.3/5
  on remembering which files changed.

## 2. The UI, rebuilt

**Light-first**, using the real Codex palette rather than an approximation. Dark
mode still there and still designed, not inverted.

**Sponsor tags gone** from the top bar. Port / SigNoz / Bright Data now appear as a
provenance row at the *end* of a run, where they're contextual instead of chrome.

Motion, all of it deliberate: ease-out never ease-in, springs from Apple's own
parameters, View Transitions for directional view changes, `@starting-style`
transitions instead of keyframes so an interrupted animation retargets rather than
restarting, a wall-clock stagger budget on streaming text, and no animation at all
on ⌘K.

Craft details that took the most time and are the least visible: the proportional
minimap that colour-codes gate outcomes, autoscroll that yields the moment you
scroll up to read, focus that returns where it came from when an overlay closes,
`overscroll-behavior: contain` everywhere, and count-up numbers that write their
real value instead of freezing at zero when the tab is hidden.

## 3. The Precedent Engine — the new thing

`precedent/`, ~900 lines, **zero third-party dependencies**, pure stdlib, offline.
It runs on any judge's laptop with no install.

The factory archives every gate failure it has ever seen and cites its own case law.
What makes it not-just-RAG:

> the sparse half of the retrieval score is **deterministic verification state** —
> which gates failed and in which severity band — not text similarity.

Four tiers: structural fingerprint (0.012 ms) → gate-signature Jaccard → BM25 →
binary-quantized vectors with exact rescore, fused with RRF.

Two things I'm most confident will land with engineers:

**It can say no.** Scores are anchored on absolute evidence, never normalised rank
— because rank normalisation rates the least-bad candidate 1.0 and cites it
confidently. A genuinely novel failure returns nothing.

**Probe-validated compaction.** Facts are held out of the summary, then the summary
is quizzed on them; a compaction that loses artifacts is rejected and retried.
Ratio is reported, the probe score is the gate.

Measured: 1,284 cases, 16 ms hybrid recall, 80 KB index vs 2.6 MB float (32×),
24× run compaction at 100% probe score.

## 4. Bugs the tests caught

Worth listing because they're the reason to write tests at all:

1. Structured events (approvals, decisions) were being dropped by the blank-line
   filter — the compactor was silently losing exactly the facts it exists to keep.
2. The stopword "the" matched nearly every archived narrative in FTS5, letting
   lexical noise bypass the evidence floor. A Kubernetes OOM query was confidently
   citing scrape-staleness precedent at 0.70.
3. `validate()` was searching the essence's own probe list — so every compaction
   trivially passed by finding its own answer key.
4. Count-up tickers froze at 0 forever if the tab was hidden when they started.
5. List tables overflowed their container at narrow widths.

35/35 tests pass now.

## 5. What I did NOT do

- **No GitHub push.** Creating a public repo publishes your work, and you said you
  couldn't approve anything for six hours. It's all committed locally — 10 commits
  with real history — and ready to push the moment you say go.
- **No live agent processes.** The orchestrator behind Daisy is still tomorrow's
  build; standalone it plays a ghost-bay replay. The README says so plainly rather
  than implying otherwise.

## Where things are

- `~/Developer/daisy` — the repo, 10 commits
- `Daisy.app` — the native Mac app, running, with the hand-drawn icon
- Artifact: the same link as before, republished
- `python3 -m precedent.cli bench` — the numbers above, reproducible
- `python3 -m precedent.test_precedent` — 35 tests
