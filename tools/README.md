# tools

One-shot build steps and generators. They are committed rather than discarded
because each one documents a decision, and rerunning any of them reproduces
that part of the UI exactly.

| script | what it does |
|---|---|
| `build_stats.py` | runs the precedent engine, measures it, and injects the **real** figures into `index.html`. Run after changing the engine or reseeding. |
| `add_sql_step.py` | inserts the memory-as-code run step, using a live SQL result |
| `add_scrape_step.py` | inserts the Bright Data scrape step |
| `add_heal_thread.py` | adds the scraper-heal thread |
| `add_crew_bay.py` | adds the CREW bay (both agents, one contract) |
| `add_self_gate.py` | adds the "gate applied to itself" block to Skills |
| `enrich_artifacts.py` | rebuilds the Artifacts view with provenance + manifest |
| `mark_taste_ok.py` | marks fixtures that quote a slop tell with `taste-ok` |
| `fix_sql_step.py` | removes a previously injected SQL step so it can be regenerated |
| `timer.py` | the six-hour refinement timer; writes `timer_state.json` |

The generators are idempotent: each checks for its own marker and exits early if
already applied.
