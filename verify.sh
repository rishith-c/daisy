#!/usr/bin/env bash
#
# Every gate Daisy claims to enforce, run against Daisy.
#
#   ./verify.sh
#
# Exit code is the number of failing gates. Nothing here needs a network, an
# API key, or a third-party package.


# ---------------------------------------------------------------------------
# Shared-tree notice.
#
# Claude and Codex are both working in this repository at the same time, in the
# same working tree. This banner lives here because verify.sh is the one command
# both agents run every cycle — a protocol nobody reads is not a protocol.
# ---------------------------------------------------------------------------
if [ -z "${DAISY_NO_BANNER:-}" ]; then
  _claims=$(python3 tools/claim.py list 2>/dev/null | grep -v '^no live claims' | tail -n +3 || true)
  printf '\033[2m%s\033[0m\n' "two agents share this tree — read AGENTS.md before editing"
  if [ -n "$_claims" ]; then
    printf '\033[2m%s\033[0m\n' "live path claims:"
    printf '\033[2m%s\033[0m\n' "$_claims"
  fi
  printf '\033[2m%s\033[0m\n' "claim before you edit:  python3 tools/claim.py take <path> --as <you>"
  echo
fi

set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
fails=0
line() { printf '%s\n' "────────────────────────────────────────────────────────────"; }

gate() {                       # gate <name> <cmd...>
  local name="$1"; shift
  printf '\n▸ %s\n' "$name"
  if "$@"; then
    printf '  PASS\n'
  else
    printf '  FAIL (exit %d)\n' "$?"
    fails=$((fails + 1))
  fi
}

line
printf 'Daisy — verification gates\n'
printf '%s · %s\n' "$($PY -V 2>&1)" "$(uname -srm)"
line

gate "taste.t1 — design lint over the UI itself" \
     $PY -m taste.lint index.html

gate "taste.t2 — computed contrast, both themes" \
     $PY -m taste.contrast index.html

gate "taste — linter test suite" \
     env PYTHONWARNINGS=ignore $PY -m taste.test_lint

gate "physics — closed-form margin test suite" \
     env PYTHONWARNINGS=ignore $PY -m hardware.test_margins

gate "precedent — engine + compaction test suite" \
     env PYTHONWARNINGS=ignore $PY -m precedent.test_precedent

gate "agents — adoption of other tools' sessions" \
     env PYTHONWARNINGS=ignore $PY -m agents.test_discover

gate "port — governance client, blueprints, and the approval gate" \
     env PYTHONWARNINGS=ignore $PY -m port.test_port

gate "commons — verified solution reuse across agents" \
     env PYTHONWARNINGS=ignore $PY -m commons.test_commons

gate "obs — OTLP exporter, tracer and offline spool" \
     env PYTHONWARNINGS=ignore $PY -m obs.test_obs

gate "scrape — drift detection and selector auto-repair" \
     env PYTHONWARNINGS=ignore $PY -m scrape.test_scrape

gate "garden — device link, consent-gated autonomous publish" \
     env PYTHONWARNINGS=ignore $PY -m garden.test_garden

gate "memory — four tiers and the verified forgetting boundary" \
     env PYTHONWARNINGS=ignore $PY -m memory.test_memory

gate "importer — detection, idempotent import, autosync, attention" \
     env PYTHONWARNINGS=ignore $PY -m importer.test_importer

# Codex wrote these and they pass, but nothing ran them: verify.sh is the board
# everyone reads, and a board that omits a third of the suite reports green for
# a codebase it has not checked. Wired in rather than trusted.
gate "chain — model orchestration across available agents" \
     env PYTHONWARNINGS=ignore $PY -m lab.test_chain

gate "labctl — agent probes report the real reason" \
     env PYTHONWARNINGS=ignore $PY -m lab.test_labctl_agents

gate "sponsors — Port, Bright Data and SigNoz in one run" \
     env PYTHONWARNINGS=ignore $PY -m lab.test_sponsors

gate "bridge — native shell to web, and back" \
     env PYTHONWARNINGS=ignore $PY -m app.test_bridge

gate "onboarding — generator is idempotent and escapes stay literal" \
     env PYTHONWARNINGS=ignore $PY -m tools.test_onboarding

gate "chain view — the UI the orchestrator paints" \
     env PYTHONWARNINGS=ignore $PY -m tools.test_daisy_chain_ui

gate "app build — reproducible bundle" \
     env PYTHONWARNINGS=ignore $PY -m tools.test_build_app

printf '\n▸ precedent — archive present\n'
if [ -f precedent/precedent.db ]; then
  $PY -m precedent.cli stats | sed 's/^/  /'
  printf '  PASS\n'
else
  printf '  SKIP (run: %s -m precedent.seed precedent/precedent.db)\n' "$PY"
fi

printf '\n▸ physics — no scrape, no certification\n'
if $PY -c "
import sys; sys.path.insert(0,'.')
from hardware.margins import evaluate, NoGroundTruth
try:
    evaluate(2.4, 90, 18, 3.2, 'PETG', 1.5, [], 2)
    sys.exit(1)
except NoGroundTruth:
    sys.exit(0)
" 2>/dev/null; then
  printf '  PASS  an empty scrape refuses to certify\n'
else
  printf '  FAIL  certified a part with no ground truth\n'; fails=$((fails + 1))
fi

printf '\n▸ precedent — it is allowed to say no\n'
out=$($PY -m precedent.cli recall "kubernetes pod evicted due to memory pressure" --gate infra.oom=0.0 2>&1)
if printf '%s' "$out" | grep -q "has not seen this before"; then
  printf '  PASS  a novel failure returns no precedent\n'
else
  printf '  FAIL  fabricated precedent for an unseen failure\n'
  printf '%s\n' "$out" | sed 's/^/    /'
  fails=$((fails + 1))
fi

printf '\n▸ ui — single file, no external requests\n'
ext=$(grep -oE 'src="https?://[^"]+|href="https?://[^"]+' index.html | grep -v 'fonts.googleapis.com\|fonts.gstatic.com' | wc -l | tr -d ' ')
if [ "$ext" = "0" ]; then
  printf '  PASS  no third-party requests beyond Google Fonts\n'
else
  printf '  FAIL  %s external reference(s)\n' "$ext"; fails=$((fails + 1))
fi

line
if [ "$fails" -eq 0 ]; then
  printf 'ALL GATES GREEN\n'
else
  printf '%d GATE(S) RED\n' "$fails"
fi
line
exit "$fails"
