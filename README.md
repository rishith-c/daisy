# Daisy 🌼

Daisy is the command center for **THE LAB: Workbench** — the agentic software factory built for the WeMakeDevs Zero Downtime Hackathon (Aug 22, 2026).

One brief in → verified software **and** verified hardware out. Claude Code and Codex build in parallel git worktrees; every artifact passes gates a human can audit: a three-tier taste ladder for frontends, closed-form physics margins for parts. Port governs, Bright Data feeds, SigNoz escalates.

## This repo

`index.html` is the complete Daisy front-of-house — a self-contained, zero-dependency web app in the Codex-desktop-app design language (serif narration, near-black neutral UI, color only for meaning, daisy yellow-green accent):

- **Live run playback** with real human approval gates (clarify → Port approve → escalation)
- **Runs** list with replayable snapshots, including a failed/escalated run
- **Review queue** — nothing merges itself; diffs land here with scorecards
- **Artifacts** — dashboard preview, closed-form stress-heatmap bracket, margin report, flight recorder
- **Automations** — scheduled runs that land in the review queue
- **Skills** — mounted per agent worktree
- **⌘K command palette**
- **Appearance** — daisy-theme-v1 (chat font / code font / accent), persisted, exportable

Run it: open `index.html`, or serve the folder with any static server. In production Daisy is served by `labctl` (the orchestrator) and driven by its SSE event stream; standalone, it plays the ghost-bay simulation of run 1042.

## Status

Pre-hackathon prototype: the UI is real and complete; the factory behind it (labctl, agents, Port/Bright Data/SigNoz wiring) is built at the event.
