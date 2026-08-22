# Demo script — 3:45

Read the **say** lines. Run the **run** lines. Times are cumulative.
Everything here is real; nothing is staged.

---

## 0:00 — Open on the claim

> **say:** "The brief for this hackathon says: the factory is the submission,
> the app is the test run. So I'll show you the factory. Daisy takes a brief and
> produces verified software *and* verified hardware — and the gates it verifies
> with are real programs, not prompts."

---

## 0:15 — One command

**run:** `./verify.sh`

> **say:** "Twenty-eight gates, fifteen hundred tests, one command. Zero
> third-party Python dependencies — you can clone this and run it with nothing
> installed."

---

## 0:35 — Real agents, probed not assumed

**run:** `python3 labctl.py agents`

> **say:** "Daisy drives the coding agents already on this machine. It doesn't
> trust a config file — it probes each one. Earlier today Codex reported as
> broken here, and the reason was an expired credential from a *different*
> tool's MCP server leaking into the check. The probe tells you which, so you
> debug the right thing."

---

## 0:55 — The factory runs, and the commons pays off

**run:** `python3 labctl.py run --brief "SR-11 bracket, 2.4 kg tip load" --lane hardware --lane scrape`

> **say:** "Watch line three. The bending gate fails at a factor of safety of
> 0.72. Before re-solving it, Daisy asks Garden — a public index — whether
> anyone has fixed this exact gate before. Someone has. Forty-eight thousand
> tokens this run did not spend."
>
> "Then it repairs by *algebra*, not by asking a model again: it inverts sigma
> equals six M over b t squared for thickness, patches one parameter,
> regenerates the geometry, and re-runs the gate. Three point two millimetres to
> four point six one. And it writes a real STL."

---

## 1:35 — Bright Data, live, from the terminal

**run:** `npx -y @brightdata/cli@latest scraper run c_mt503aoe22ii2ojmaj <url>`

> **say:** "Bright Data Scraper Studio, entirely from the terminal — I never
> opened their dashboard. I described what I wanted in plain English and it
> built the scraper. This is live JSON off a real site. It even inferred
> structure I didn't ask for — splitting price into value, currency and symbol."

---

## 2:10 — Port, and an approval that actually blocks

**run (terminal A):** `python3 -m port.cli --run judge1 approve --request`

> **say:** "Port holds the plan, and the plan is committed *before* any agent
> runs. Watch — this is stopped. Not notifying and continuing. Stopped."

**run (terminal B):** `python3 -m port.cli --run judge1 approve --grant --by rishith`

> **say:** "Four things are tested here. No plan, no agents — delete Port and
> nothing starts. The approval blocks. A timeout is not consent. And the factory
> cannot sign its own work — approving as 'claude' is refused."

---

## 2:45 — The chain that ties it together

**run:** `DAISY_SCRAPE_FIXTURE=vendor_v2.html python3 tools/traced_verify.py`

> **say:** "Now the vendor restructures their web page overnight. The scrape
> gate goes red. The repair signal fires. And three lines later the fastener
> gate fails: cannot certify — no scraped rows."
>
> "A website changed its HTML, and a physical part can no longer be signed off.
> That whole chain of blame is one trace tree."

---

## 3:10 — Say the limits out loud

> **say:** "Two things I want to be straight about. The run playback in the app's
> UI is a scripted replay — the real execution is labctl, which is what you just
> watched. And SigNoz is instrumented and tested, a hundred and seventy-nine
> tests, but I never ran an instance — I didn't have the disk. Every signal has
> been spooling to disk all day, so one command uploads the whole history the
> moment there's an endpoint."

---

## 3:25 — Close on the strongest fact

> **say:** "The three gates each caught a real bug in the thing they check. The
> contrast checker found seven WCAG failures, three of which I had already
> 'fixed' by eye. And the physics gate caught *me* — I had narrated 212
> megapascals at a safety factor of 0.82. The real load case gives 69 and 0.72.
> Confident, plausible, wrong numbers, caught by forty lines of Python."
>
> "That's the argument. Verification you can run, not verification you're asked
> to believe."

---

## Have open beforehand

- Garden: https://garden-taupe-three.vercel.app
- Repo: https://github.com/rishith-c/daisy
- Two terminals, both `cd ~/Developer/daisy`
- Bright Data collector id: `c_mt503aoe22ii2ojmaj`

## If you overrun

Cut 2:45 (the trace chain) — it's the most impressive beat but the least
self-explanatory on video. Keep 0:55 and 2:10; they are the two the rubric
asks about directly.
