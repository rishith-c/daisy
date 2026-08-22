# Daisy Fresh Start and Daisy Chain Implementation Plan

> **For Codex:** Execute this plan test-first in the shared working tree. Claim
> every path, preserve Claude's files, and regenerate `index.html` only through
> idempotent generators.

**Goal:** Make Daisy start as a genuinely blank first-run app with optional
real Garden linking, and turn Daisy Chain into a real CEO-led local multi-agent
workflow under Port, Bright Data, and SigNoz governance.

**Architecture:** The existing `lab.run.execute` pipeline remains the single
orchestrator. `lab.chain` adds the model-controlled CEO/worker/synthesis/review
stages, while `lab.run` wraps them in the existing Port plan, OTLP trace, and
deterministic gate lifecycle. The native bridge exposes only fixed argument
arrays; generator-owned HTML renders state and invokes the bridge.

**Tech stack:** Python 3 standard library and unittest, Swift/AppKit/WebKit,
vanilla HTML/CSS/JavaScript, existing Port/Bright Data/SigNoz adapters.

---

### Task 1: Lock contracts with failing tests

**Files:**
- Modify: `lab/test_chain.py`
- Modify: `lab/test_sponsors.py`
- Modify: `app/test_bridge.py`
- Modify: `tools/test_daisy_chain_ui.py`
- Modify: `tools/test_onboarding.py`

1. Add tests for CEO coverage, concurrent peer assignments, synthesis,
   independent review, and truthful failure gates.
2. Add a sponsor composition test proving the Port plan is committed before a
   chain invokes any agent.
3. Add bridge tests for `chain.run`, `app.reset`, bounded input, callbacks, and
   no shell.
4. Add generator tests for a blank first run, blank new-run actions, reset
   callback, live chain submission, and Daisy typography.
5. Run the focused tests and confirm they fail for missing behavior.

### Task 2: Implement the governed chain runtime

**Files:**
- Modify: `lab/chain.py`
- Modify: `lab/run.py`
- Modify: `labctl.py`

1. Rename the lead role to `ceo` and keep every peer reporting to it.
2. Implement strict JSON plan parsing with one repair attempt and exactly one
   assignment per peer.
3. Execute peer tasks concurrently through `lab.executors.run`.
4. Invoke CEO synthesis and peer review, persist `chain.json`, and expose named
   deterministic gates.
5. Route `labctl chain --goal` and `run --daisy-chain` through the same runtime.
6. Run chain and sponsor tests to green.

### Task 3: Implement reset and live native commands

**Files:**
- Modify: `app/main.swift`
- Modify: `app/test_bridge.py`

1. Add validated `chain.run` handling using a fixed `Process` argument array.
2. Add `app.reset` handling that unlinks the scoped Garden device credential.
3. Emit explicit callbacks for success and failure output.
4. Run bridge tests to green and compile the Swift target.

### Task 4: Generate the blank-run and in-style Daisy Chain UI

**Files:**
- Create: `tools/add_daisy_chain.py`
- Modify: `onboarding.html`
- Modify: `tools/add_onboarding.py`
- Modify: `tools/test_daisy_chain_ui.py`
- Modify: `tools/test_onboarding.py`
- Generate: `index.html`

1. Create an importable, idempotent Daisy Chain generator.
2. Replace startup/New Run sample replay with a blank transcript function;
   preserve sample playback only for explicit history actions.
3. Submit a blank run's first brief to `chain.run` when Daisy Chain is enabled,
   and render real result/error callbacks.
4. Add an in-style reset surface and onboarding-owned reset controller.
5. Ensure sans typography for UI prose and mono only for provenance.
6. Run generator tests, regenerate twice, and prove the second run is a no-op.

### Task 5: Verify and hand off the app

**Files:**
- Build output: `Daisy.app`

1. Run focused Python, generator, and bridge tests.
2. Run `./verify.sh`.
3. Build and ad-hoc sign Daisy; verify the signature.
4. Launch the app, use reset, and inspect fresh onboarding, Garden connect,
   blank main run, and Daisy Chain UI.
5. Capture screenshots and report exact live/degraded sponsor status and any
   external credential blocker.

