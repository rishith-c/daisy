# Start here — hackathon morning

Everything below is already done and verified. This is the short list of what to
do at the venue, in order.

## 1. Sanity check (30 seconds)

```bash
cd ~/Developer/daisy && ./verify.sh
```

Expect `ALL GATES GREEN`, exit 0. If anything is red, that is new — nothing was
red at 06:48.

## 2. Push when you're ready

No remote is configured yet, on purpose. When you want it up:

```bash
cd ~/Developer/daisy && gh repo create daisy --private --source=. --remote=origin --push
```

Swap `--private` for `--public` when you want judges to see it. Flipping later
is one click on the repo's settings page.

## 3. The three things that carry the submission

**The factory is the submission, the app is the test run.** Say that early. The
judges' brief says it in those words.

**Three gates are real programs, not prompts** — and each one caught bugs in the
thing it checks. That is the strongest single claim you have:

| Gate | Caught |
|---|---|
| `taste/lint.py` + `contrast.py` | 7 real WCAG failures, 3 of which I had already "fixed" by eye |
| `hardware/margins.py` | the narrated physics numbers were fake; the repair could fail its own gate |
| `precedent/` | nothing — but it *refuses to guess*, which is the point |

**Precedent is the novel bit.** The sparse half of the retrieval score is
deterministic verification state — which gates failed and in which severity band
— not text similarity. That is why a query with almost no usable words still
retrieves correctly, and why a genuinely novel failure returns nothing.

## 4. Demo order that works

1. Hold the drone. "When the math is wrong, drones catch fire."
2. `./verify.sh` going green in one command.
3. A brief into Port → the plan committed *before any agent runs* → the approval
   that blocks. "Delete Port and no agent ever starts."
4. The CREW bay — Claude and Codex, one contract.
5. Taste gate fails with named gates → then `taste.lint index.html` passing on
   Daisy itself. "A gate you exempt yourself from is not a gate."
6. Precedent recalls the fix in 16 ms, 0 tokens — then the novel failure that
   returns **nothing**.
7. The Margin Call: red at FoS 0.72 → the derivation solving t = 4.61 mm → green
   at 1.50.
8. Unplug Bright Data → "cannot certify part — no ground truth."

## 5. Say what you brought

The UI and the gates were built the night before. Declare it plainly, on camera
and in the README. Brought assets are normal; undeclared ones are not.

## Running the factory for real

```bash
python3 labctl.py agents                      # which agents can actually be driven
python3 labctl.py run --brief "..." --lane hardware --lane scrape
python3 labctl.py run --brief "..." --lane software    # needs a usable agent
```

The hardware and scrape lanes need no LLM and no credentials — algebra does not
require a language model. That run commits a plan, fails `physics.bend` at FoS
0.725, recalls the fix from the commons, solves t = 4.61 mm, writes a real STL,
and picks a fastener from scraped rows.

To show the whole failure chain in one trace:

```bash
DAISY_SCRAPE_FIXTURE=vendor_v2.html python3 tools/traced_verify.py
```

A vendor page restructures, `scrape.schema` goes red, the repair signal fires,
and `physics.fastener` then fails with "cannot certify" — a website changing its
HTML making a physical part unsignable, visible without narration.

## Known limits

- The UI's run playback is a scripted replay, not a live execution. Say so.
- Port / SigNoz / Bright Data are offline until credentials are set.
- `claude -p` and `codex exec` were both unusable on this machine at build
  time (expired session; CLI older than its configured model). `labctl agents`
  will tell you the current truth.

---

Full account of the six hours: `SIX-HOURS.md`
Plan and video shot list: the battle-plan artifact.
