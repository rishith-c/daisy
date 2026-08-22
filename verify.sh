#!/usr/bin/env bash
#
# Every gate Daisy claims to enforce, run against Daisy.
#
#   ./verify.sh
#
# Exit code is the number of failing gates. Nothing here needs a network, an
# API key, or a third-party package.

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
