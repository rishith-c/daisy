# AGENTS.md — two agents, one working tree

**Read this before editing anything.** Claude (Claude Code) and Codex are both
working in this repository **at the same time, in the same working tree**. Not
separate clones, not separate branches — the same files on the same disk.

That rules out the usual answer. Branching does not help when both processes
`cd` into the same directory. What follows is the protocol that does.

This file was itself deleted once, minutes after being written, while other
untracked files beside it survived. Assume anything uncommitted is at risk.

## 1. Claim a path before you edit it

    python3 tools/claim.py take  <path> --as codex     # or --as claude
    python3 tools/claim.py list                        # who holds what
    python3 tools/claim.py release <path> --as codex

A claim is a file under `.agents/locks/`. It is **advisory** — nothing enforces
it, and that is deliberate: a hard lock that outlives a crashed agent is worse
than no lock. Claims expire after 45 minutes on their own.

`take` refuses if someone else holds the path and the claim is still warm. If
you genuinely need it, say so in the commit message and take it — do not edit
silently under a live claim.

## 2. Commit small and often

Every commit is a checkpoint the other agent can build on. A long-running
uncommitted change in a shared tree is a change the other agent will destroy
without either of you noticing. There is **no remote** on this repo, so `git
log` is the only channel that survives a crash.

## 3. Never delete or revert what you did not write

Do not run `git clean`, `git checkout .`, `git stash`, or `rm` across paths you
do not own. Untracked does not mean abandoned — the other agent may be seconds
from committing it. If you need a clean state, claim the specific paths and
revert only those.

## 4. Run the gates before and after

    ./verify.sh

Green before you start, green when you stop. If it is red when you arrive, the
other agent is mid-edit — **wait, do not fix it.** Repairing someone else's
half-written file is how two agents produce a file neither of them wrote.

## 5. Do not rewrite history

No `rebase`, no `--amend`, no force on commits you did not author. The other
agent may already be building on them.

## Current ownership

**Claude** built and owns:
`taste/` `hardware/` `precedent/` `commons/` `agents/` `obs/` `scrape/`
`port/` `garden/` `lab/` `labctl.py` `tools/e2e_garden.py` `tools/claim.py`
and `../garden-site/` (the Garden site — a separate repo, deployed live).

**In flight, unfinished** when a session limit killed the agents writing them.
Ask before assuming these are abandoned: `chat/` `duo/`
`tools/add_memory_view.py`.

**Codex** owns what it has committed — Daisy Chain, first-run, agent probes,
the app build.

`index.html` is shared and is the most dangerous file in the repo. It is
generated into by several idempotent tools in `tools/`. **Never hand-edit it.**
Write or update a generator, run it twice, and confirm the second run produces
a zero diff.

## The gates are not negotiable

`python3 -m taste.lint index.html` must report 0 findings and
`python3 -m taste.contrast index.html` must report 0 failing pairs. They are
the same checks this project applies to everything else; exempting ourselves
would make the entire pitch dishonest.
