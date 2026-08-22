# Daisy Fresh Start and Daisy Chain Design

## Product contract

Daisy is useful without an account or network connection. A first launch shows
the native-feeling onboarding, lets the user start locally, and leaves the main
run empty. The bundled sensor-fleet run is a sample in history, never content
silently inserted into a new conversation.

Garden linking is optional. Google authentication happens in the trusted
system browser. Daisy stores only the scoped Garden device credential returned
by pairing; it never receives or stores the Google credential.

## Fresh start and reset

`New run`, Command-N, and first launch all open a blank transcript titled
`New run` with the composer ready for the user's brief. Opening a sample from
history remains an explicit action and may replay its saved transcript.

Settings exposes `Erase local setup and show onboarding`. The action:

1. removes Daisy-owned browser state, including onboarding and Daisy Chain
   preferences;
2. asks the native bridge to run `python3 -m garden.link unlink` through a fixed
   argument array;
3. waits for the native callback, then reloads into onboarding;
4. reports unlink failures truthfully while still removing the local scoped
   credential, as the link layer already guarantees.

The reset does not delete user files, repositories, external Garden content,
or unrelated macOS preferences.

## Daisy Chain execution

Daisy Chain is an execution mode, not a decorative model picker. It probes the
installed Claude, Codex, and OpenCode adapters and refuses to call the feature
ready unless at least two agents answer a live probe.

The first usable agent is the CEO. Every remaining usable agent reports to the
CEO; the final peer is also the independent reviewer. A run has four model
stages:

1. The CEO returns a typed JSON task plan with exactly one bounded assignment
   for every peer.
2. All peers execute their assigned tasks concurrently through their real CLI
   adapters.
3. The CEO synthesizes the peer results against the original goal.
4. The reviewer checks the synthesis and returns a pass/fail review with
   findings.

Each response is persisted in `runs/<run>/chain.json`. A malformed CEO plan
receives one explicit repair attempt. Unavailable agents, missing assignments,
failed workers, an absent synthesis, or a failed review become named failed
gates. No model can mark its own result verified.

Port commits the run plan before any agent invocation, records the resulting
gates, and owns the human approval boundary. SigNoz receives correlated spans
for the CEO, workers, synthesis, review, and gates. Bright Data is the evidence
lane when a goal needs current web facts; without its credential the existing
named fixture/request-spool path remains visibly degraded and never claims a
live scrape. Deterministic gates and Port—not the CEO—remain the final
authority.

## Native bridge and UI

The native bridge accepts only fixed commands and array arguments:

- `chain.status` probes topology;
- `chain.run` invokes `labctl.py run --brief <goal> --daisy-chain --json`;
- `app.reset` invokes the Garden unlink command.

The web layer renders the real returned topology and run summary. Daisy Chain
uses Daisy's existing system sans typography for labels and prose; monospaced
type is reserved for agent/model/provenance data. The model menu shows
`CEO -> peers -> gates`, current availability, and honest blocked/running/error
states.

`index.html` remains generated output. The Daisy Chain and onboarding
generators are idempotent, and a second generation must produce no diff.

## Verification

Behavior is complete when focused tests prove blank-run/reset semantics,
native bridge allowlisting, CEO fan-out and review gates, and sponsor ordering;
the full repository verification passes; the app builds and ad-hoc signs; and
visual inspection shows fresh onboarding and the in-style Daisy Chain control.

