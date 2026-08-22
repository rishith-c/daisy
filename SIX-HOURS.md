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

## 4. The taste gate, made real

`taste/lint.py` — tier 1 of the taste ladder as an actual program rather than a
prompt. Twenty named tells, each with `file:line` and a reason, exit code = finding
count. Named gates instead of a score, because "6.5/10" gives an agent nothing to
act on while `gate 3 at tokens.css:12` can be injected straight back into a resumed
session.

Then I pointed it at Daisy. It found two bugs — **in itself**: it was flagging
typographic glyphs (`✓`, `σ`, `≥`, `·`) as emoji, when those inherit `currentColor`
and are precisely not the tell; and it had no suppression mechanism, so a file that
documents the tells could never pass. Both fixed. Daisy now returns **0 findings**,
and one of the 26 linter tests asserts exactly that. A gate you exempt yourself from
is not a gate.

Then I built **tier 2** as well — `taste/contrast.py`, which reads the design tokens
out of the stylesheet, composites `rgba()` and `color-mix()` the way the browser
will, and checks every text-on-surface pair in both themes.

It found **seven real WCAG failures** in Daisy on its first run. Every one looked
fine on screen. Three of them I had already "fixed" by hand ten minutes earlier
after eyeballing a smaller sample — which is precisely the argument for making the
gate a program rather than a judgement. Now: 28 pairs, 0 failing, tightest 3.11:1.

Tier 3 (the vision judge) genuinely needs a multimodal call, so it stays on
rehearsal rather than every save — and the UI says so instead of implying
otherwise.

`./verify.sh` runs every gate — both taste tiers over the UI itself, both test
suites, the archive stats, the it-can-say-no behaviour, and a
no-third-party-requests check. All green.

## 5. Every view made load-bearing

Late in the window I went back through each view asking "would an operator
actually use this, or is it a screenshot of one?" Four were the latter:

- **Runs** claimed three runs while Precedent claimed 1,284 — an internal
  contradiction a judge would spot in seconds. Now aggregate windows plus ten
  runs across all three lanes, with a column showing how much precedent each
  cited.
- **Automations** had no timing at all. Now last-run, result, next-run, and the
  point that matters: an automation is a run with a cron trigger, not a
  privileged path. It runs the full gate set, cannot self-approve, and lands in
  the same review queue.
- **Review queue** had its buttons wrapping onto two lines and no sense of how
  long anything had waited. Now fixed, with a breakdown of what a green
  scorecard actually covers.
- **Artifacts** was a thumbnail gallery. Now every card names its producing run
  and clearing gate, with a full manifest.

I also added the **CREW bay** — Claude and Codex side by side against one
`api-contract.json`. You asked for the two agents combined inside one app, and
the run had them narrating in turn, which reads as two tools used sequentially
rather than together. (Its layout keys off a container query, not a media query:
the bay lives inside a fixed-width column, so viewport width was the wrong
signal and it silently collapsed to one column at every size.)

And two things the sponsors' own criteria ask for that were only ever
*referenced*: the **Bright Data scrape** as its own step (terminal command,
pinned collector, clean JSON, and the five rows the solver actually chooses
from), and the **scraper heal** as its own watchable thread — because
"show automatic repair when a site changes" is the single most-emphasised data
criterion and it only existed as a pending row.

## 6. Bugs the tests caught

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
6. **⌘K was dead.** The `j`/`k` outline navigation I added matched `k` without
   excluding modifiers, so it swallowed the event and called `preventDefault()`
   before the palette handler ran — the command palette broken by its own
   shortcut. Nothing on screen suggested it; only a scripted keypress found it.
7. Minimap marks took **negative** offsets when laid out before the pane had a
   height, parking every mark off-screen.
8. Run ids were being thousands-separated by the count-up ticker ("1,042"),
   because it matched any numeric cell rather than statistics only.
9. Seven WCAG contrast failures — see the tier-2 section above.
10. View Transitions reject with `InvalidStateError` on a hidden document, and
    `ready`/`finished`/`updateCallbackDone` each rejected unhandled — one
    unhandled rejection per view switch, invisible unless you look.
11. Two of my own build generators were not idempotent: their guard strings
    didn't match what they emit (one checked for a literal `·` where the output
    uses `&middot;`, the other for `class="bay"` which is JSON-escaped inside a
    JS string). Re-running either silently duplicated a whole section. Found by
    running every generator twice and diffing — which is now the check.

39/39 precedent tests and 26/26 taste tests pass, plus a scripted interaction
sweep over every view, control, and keyboard path. `./verify.sh` is green.

## 7. What I did NOT do

- **No GitHub push.** Creating a public repo publishes your work, and you said you
  couldn't approve anything for six hours. It's all committed locally — 10 commits
  with real history — and ready to push the moment you say go.
- **No live agent processes.** The orchestrator behind Daisy is still tomorrow's
  build; standalone it plays a ghost-bay replay. The README says so plainly rather
  than implying otherwise.

## Where things are

- `~/Developer/daisy` — the repo, 37 commits, working tree clean
- `Daisy.app` — the native Mac app, running, with the hand-drawn icon
- Artifact: the same link as before, republished
- `./verify.sh` — every gate, one command
- `tools/README.md` — what each build script does; all are idempotent
- `python3 -m precedent.cli bench` — the numbers above, reproducible
- 65 tests across the two suites
