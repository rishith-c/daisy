# Garden Community and Daisy First-Run Design

Date: 2026-08-22
Status: approved for autonomous execution

## Product boundary

Daisy and Garden are one system with deliberately different trust boundaries.

- **Daisy** is the private, local-first macOS workshop. It discovers local coding agents, runs work, applies executable gates, records provenance, and remains useful without a network or an account.
- **Garden** is the public commons. It admits verified solution records, makes them discoverable, and gives people a real identity-backed place to like, discuss, and reuse them.

The visual system stays light-first, restrained, botanical, and native rather than becoming a generic social dashboard. Garden uses the meadow and specimen language; Daisy uses the same mark and semantic colors inside macOS-native composition.

## Sponsor integration

The hackathon's three sponsor pillars already map to executable Daisy subsystems. The implementation will strengthen their demonstrability rather than add logos or pretend that offline fixtures are live services.

- **Port**: the `port/` package is the governance boundary for plans, approvals, and release state. The app exposes the plan-before-build and human-approval loop.
- **Bright Data Scraper Studio**: the `scrape/` package detects schema drift, exercises repair against changed fixtures, and records scraper rules in the repository. The UI shows the key-diff, repair preview, verification, and approval chain.
- **SigNoz**: the `obs/` package emits traces, metrics, and logs with an offline spool. The UI makes latency, failures, retries, and repair spans diagnosable. Live-vs-spooled state is always labelled.

No sponsor integration is called live without credentials. Dry-run and offline paths remain first-class, explicit states.

## Garden information architecture

Garden remains a zero-build static site backed by Firebase and Vercel functions.

- `/` is a focused product landing page with the open meadow hero, explanation, trust model, and calls to browse or publish.
- `/index` is a separate marketplace page served by `community.html` through a Vercel rewrite.
- `/s/:slug` is a solution-detail route handled by the community page.
- `/u/:uid` is a compact public contributor profile route handled by the community page.
- `/connect` is the signed-in Daisy pairing surface.
- `/privacy` and `/terms` are concise public policy pages required for a production OAuth surface.

The landing page does not embed the marketplace grid. Navigation between landing and marketplace uses real paths rather than a section anchor.

## Garden marketplace experience

The marketplace header contains search, kind and gate filters, and a sort control for newest, most liked, most discussed, and most reused. Filtering and sorting run over the normalized live result set so static-mirror and Firestore rows render through the same component path.

Each solution card shows:

- generated botanical specimen artwork derived deterministically from the solution identity;
- title and kind;
- contributor name and publishing date;
- model/vendor provenance;
- passed gate chips and margins when present;
- tokens spent, reuse count, like count, and comment count.

The detail view adds the complete recipe, gate-verification table, signature, files/artifacts, source provenance, related solutions, like action, and a one-level threaded discussion. Empty, loading, signed-out, permission-denied, offline, mirror-only, and missing-solution states are explicit and never represented as successful live data.

Public browsing requires no account. Publishing, liking, commenting, editing one's comment, and managing one's profile require Google sign-in.

## Firebase data model

Existing `solutions`, `users`, `devices`, and `pairings` remain. New public social data is stored as real Firestore documents:

- `profiles/{uid}`: `uid`, `name`, optional `photo`, optional short `bio`, `created_at`, `updated_at`. Public read; owner-only write; immutable `uid`.
- `likes/{solutionId_uid}`: `solution_id`, `uid`, `created_at`. Public read; owner-only create/delete; no update. The deterministic id makes a second like impossible.
- `comments/{commentId}`: `solution_id`, `uid`, `author_name`, optional `author_photo`, `body`, optional `parent_id`, `created_at`, `updated_at`. Public read; owner create/update/delete; immutable ownership, solution, parent, and creation time. Replies may reference a root comment on the same solution, giving one level of threading.

Counts are derived from actual like and comment documents fetched for the visible result set. No untrusted client-maintained aggregate is presented as authoritative. This is appropriate for hackathon-scale data and keeps counters honest without a background function.

Firestore indexes support solution/date comment queries and public solution listing. Rules tests cover anonymous reads, required auth, ownership, immutable fields, timestamp integrity, body limits, cross-solution reply rejection, duplicate-like prevention through deterministic ids, and the existing verified-solution invariant.

`garden-api.js` owns all Firebase access and exposes normalized solution/community operations. Page code does not import Firebase directly.

## Daisy pairing and device trust

Google OAuth never runs inside Daisy's `WKWebView`. Google prohibits authorization in an app-controlled embedded user-agent, so Daisy opens Garden's `/connect` page in the system browser.

The pairing flow is:

1. The person signs into Garden in the system browser.
2. Garden creates a random six-character pairing document with a ten-minute expiry.
3. The person pastes the code into Daisy.
4. Daisy sends the code to `POST /api/pair`.
5. The server performs an atomic transaction: require unused and unexpired; generate a device token; store only its SHA-256 on the device record; burn the code; return the raw token exactly once.
6. Daisy's existing `garden.link` client stores the scoped device credential in its private `0600` file and never stores a Google credential.

`DELETE /api/pair` revokes the calling device. `POST /api/solutions` authenticates the bearer device token, revalidates that every gate passed, attributes the solution to the paired owner, and writes idempotently. The endpoints use Firebase Admin credentials only from Vercel environment configuration. If missing, they return a truthful `503` with setup guidance; the local Daisy workflow remains available.

## Daisy first-run experience

The existing generated onboarding remains the source of truth, but its sequence becomes a premium four-stage first run:

1. **Welcome**: full, calm composition with the Daisy mark, a concise product promise, `Start locally` as the primary action, and `Connect Garden` as an optional secondary action.
2. **Garden**: linked/unlinked state, system-browser connection button, six-character code field, progress, success, and recoverable error states. This screen can be skipped.
3. **This Mac**: run the real installed-agent probe through the native bridge and report usable, unavailable, or unknown states without invented green checks.
4. **Ready**: appearance, publishing consent, a short statement of the Port/Bright Data/SigNoz factory loop, and `Start first run`.

The app remains keyboard-completable, screen-reader-labelled, reduced-motion aware, and light/dark capable. The main application is not redesigned; first-run work reuses its tokens and interaction grammar.

The Swift bridge becomes a small explicit command router for agent discovery, Garden status/pairing, and opening external HTTPS URLs in `NSWorkspace`. It accepts both the existing `agents` name and `onboarding.agents` so old callers remain compatible. Shell commands are fixed argument arrays; user input is never interpolated into a command string.

## Failure handling

- A Firebase outage falls back to the labelled GitHub mirror for solution reads. Social writes stay disabled and say why.
- An unavailable Google provider leaves public browsing and Daisy local use intact.
- A missing Firebase Admin credential disables only device exchange and autonomous publish endpoints.
- Expired or reused pairing codes are refused without storing a credential.
- A missing coding agent is a lane-level refusal, not an application crash.
- Sponsor services without credentials use their existing explicit offline/dry/spooled modes.

## Testing and verification

Garden:

- browser layout regressions at desktop and mobile sizes;
- pure API normalization and community helper tests;
- Firebase emulator rules tests for solutions, profiles, likes, comments, devices, and pairings;
- serverless handler tests with injected stores and deterministic clocks;
- syntax checks, responsive screenshots, dark mode, reduced motion, keyboard flow, and visible focus;
- Vercel deployment status inspection after production release.

Daisy:

- focused Python pairing-client tests and Swift bridge contract checks;
- generated-onboarding source/injected-output consistency check;
- the complete `verify.sh` gate suite;
- optimized Swift build, bundle assembly, ad-hoc code signing, launch, and screenshots of welcome, Garden connection, onboarding, and main app;
- manual checks that system-browser auth is external and local start works with no account.

## Release discipline

Unrelated dirty files are preserved and excluded from commits. Garden and Daisy receive separate, scoped commits. Garden production is released to the already-linked `garden` Vercel project and Firebase rules/indexes are deployed when the authenticated CLI/project configuration permits it. Any console-only OAuth or secret setup is reported precisely and never papered over with simulated success.
