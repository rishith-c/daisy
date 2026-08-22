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

gate "taste — linter test suite" \
     env PYTHONWARNINGS=ignore $PY -m taste.test_lint

gate "precedent — engine + compaction test suite" \
     env PYTHONWARNINGS=ignore $PY -m precedent.test_precedent

printf '\n▸ precedent — archive present\n'
if [ -f precedent/precedent.db ]; then
  $PY -m precedent.cli stats | sed 's/^/  /'
  printf '  PASS\n'
else
  printf '  SKIP (run: %s -m precedent.seed precedent/precedent.db)\n' "$PY"
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
