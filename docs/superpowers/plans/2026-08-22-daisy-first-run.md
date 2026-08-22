# Daisy First-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a native-feeling, local-first Daisy welcome, optional Garden pairing, reliable agent probe, verified app bundle, and demonstrable sponsor-backed factory loop.

**Architecture:** Keep the dependency-free Cocoa/WKWebView shell and generated onboarding source. Route a small fixed set of native commands through Swift, reuse the existing Python `garden.link` client for scoped credentials, and keep every cloud-dependent state optional and truthful.

**Tech Stack:** Swift/Cocoa/WebKit, HTML/CSS/vanilla JavaScript, Python standard library, existing Daisy gates, ad-hoc macOS code signing.

**Spec:** `docs/superpowers/specs/2026-08-22-garden-daisy-community-design.md`

## Global Constraints

- Daisy works offline and without an account.
- Google authentication opens in the system browser, never `WKWebView`.
- Only a scoped Garden device credential is stored; never a Google token.
- Onboarding is keyboard-completable, accessible, dark-mode and reduced-motion aware.
- Preserve unrelated dirty chat, duo, and memory-tool changes.

---

### Task 1: Native bridge command contract

**Files:**
- Create: `app/test_bridge.py`
- Modify: `app/main.swift`

**Interfaces:**
- Consumes messages `{cmd:'agents'|'onboarding.agents'|'garden.status'|'garden.pair'|'garden.open', ...}`.
- Produces callbacks `window.__daisyOnboarding(json)` and `window.__daisyGardenPair(json)`.

- [ ] **Step 1: Write failing source-contract tests**

Assert the Swift source recognizes both agent command names, accepts only HTTPS Garden URLs, passes pairing codes as an argument array, invokes the correct JavaScript callbacks, and never builds a shell command string.

- [ ] **Step 2: Run and observe mismatch failure**

Run: `python3 app/test_bridge.py`

Expected: FAIL because `onboarding.agents` and Garden commands are not handled.

- [ ] **Step 3: Implement an explicit Swift router**

Use `Process.executableURL = /usr/bin/env`, fixed arrays such as `['python3','-m','garden.link','pair','--code', code]`, background execution, and `NSWorkspace.shared.open(url)` for the exact HTTPS Garden origin.

- [ ] **Step 4: Run contract and Swift compiler checks**

Run: `python3 app/test_bridge.py`

Run: `swiftc -typecheck app/main.swift -framework Cocoa -framework WebKit`

Expected: PASS and exit 0.

- [ ] **Step 5: Commit**

```bash
git add app/main.swift app/test_bridge.py
git commit -m "feat: connect Daisy native bridge to Garden"
```

### Task 2: Premium welcome and Garden onboarding

**Files:**
- Modify: `onboarding.html`
- Create: `tools/test_onboarding.py`
- Modify generated: `index.html`
- Modify: `tools/add_onboarding.py`

**Interfaces:**
- Consumes native Garden and agent callbacks.
- Produces four first-run panels and `window.daisyShowOnboarding()`.

- [ ] **Step 1: Write failing onboarding behavior/source tests**

Assert unique accessible headings, `Start locally`, `Connect Garden`, code entry, external-open native command, Garden pair native command, agent-probe command, progress labels, focus trap, reduced-motion rules, and source/generated parity.

- [ ] **Step 2: Run and observe missing welcome/pair states**

Run: `python3 tools/test_onboarding.py`

Expected: FAIL on missing controls and bridge calls.

- [ ] **Step 3: Build the four-stage first run in the source file**

Use the app's existing tokens, mark, button grammar, and sheet motion. Keep `Start locally` primary, make Garden connection optional, show a six-character field with pending/success/error states, and retain the real machine probe.

- [ ] **Step 4: Make reinjection deterministic**

Extend `tools/add_onboarding.py` with `--replace` so it replaces existing delimited blocks in `index.html` from the source rather than refusing when the guard exists.

- [ ] **Step 5: Regenerate and rerun tests**

Run: `python3 tools/add_onboarding.py --replace`

Run: `python3 tools/test_onboarding.py`

Expected: source and generated output match; all behavior assertions pass.

- [ ] **Step 6: Run Daisy taste gates and commit**

Run: `python3 -m taste.lint index.html && python3 -m taste.contrast index.html`

```bash
git add onboarding.html tools/add_onboarding.py tools/test_onboarding.py index.html
git commit -m "feat: refine Daisy first run"
```

### Task 3: Sponsor-backed factory story

**Files:**
- Modify: `README.md`
- Modify only if needed for truth/clarity: `index.html`

**Interfaces:**
- Consumes existing `port/`, `scrape/`, and `obs/` executable modules.
- Produces a judge-readable runbook and concise in-app factory summary.

- [ ] **Step 1: Verify sponsor subsystems before describing them**

Run:

```bash
python3 -m port.test_port
python3 -m scrape.test_scrape
python3 -m obs.test_obs
```

Expected: all pass. Claims in docs must match their actual live/dry/spooled behavior.

- [ ] **Step 2: Add a three-pillar demo runbook**

Document exact commands and what a judge sees for Port plan/approval, Bright Data drift/repair, and SigNoz trace/spool. Label credentials required for live services and keep offline commands runnable.

- [ ] **Step 3: Run README command smoke checks and commit**

```bash
git add README.md index.html
git commit -m "docs: sharpen sponsor integration demo"
```

### Task 4: Reproducible native app bundle

**Files:**
- Create: `tools/build_app.sh`
- Modify: `app/Info.plist`
- Modify: `README.md`

**Interfaces:**
- Produces: `Daisy.app` containing executable, HTML, icon, and required Python packages.

- [ ] **Step 1: Write bundle assertions into the build script**

The script must fail unless the binary, `index.html`, `AppIcon.icns`, `agents/`, and `garden/` exist in `Contents/Resources`, then run `codesign --verify --deep --strict`.

- [ ] **Step 2: Run the script and observe current bundle gap**

Run: `bash tools/build_app.sh`

Expected before implementation: FAIL because the script is absent.

- [ ] **Step 3: Implement deterministic build and ad-hoc signing**

Compile with:

```bash
swiftc -O -o Daisy.app/Contents/MacOS/Daisy app/main.swift -framework Cocoa -framework WebKit
codesign --force --deep -s - Daisy.app
codesign --verify --deep --strict Daisy.app
```

Copy source resources explicitly; do not copy caches, databases, credentials, or local run artifacts.

- [ ] **Step 4: Build twice and verify idempotence**

Run: `bash tools/build_app.sh && bash tools/build_app.sh`

Expected: both runs succeed and the second does not accumulate stale files.

- [ ] **Step 5: Commit**

```bash
git add tools/build_app.sh app/Info.plist README.md
git commit -m "build: make Daisy app reproducible"
```

### Task 5: Full Daisy verification and visual handoff

- [ ] **Step 1: Run every Daisy gate fresh**

Run: `./verify.sh`

Expected: `ALL GATES GREEN` and exit 0.

- [ ] **Step 2: Build, sign, and launch**

Run: `bash tools/build_app.sh && open Daisy.app`

- [ ] **Step 3: Inspect native first-run states**

Capture welcome, Garden connection, machine probe, ready, and main-app screenshots. Confirm `Start locally` reaches the app with no network/account and `Connect Garden` uses the system browser.

- [ ] **Step 4: Verify repository scope**

Run: `git status --short` and `git diff --check`.

Expected: unrelated dirty files remain untouched and excluded from scoped commits.
