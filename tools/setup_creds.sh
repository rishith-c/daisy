#!/usr/bin/env bash
# Prompt for the sponsor credentials and write .env.local, 0600.
#
# Interactive on purpose. Values are read with `read -s`, so they never appear
# on screen, never enter shell history, and never pass through a transcript.
# The file is chmod 600 before anything is written into it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVF="$ROOT/.env.local"

ask() {                       # ask VAR "prompt" [current]
  local var="$1" prompt="$2" cur="${3:-}" val=""
  if [ -n "$cur" ]; then
    printf '  %s [keep existing? enter to keep] ' "$prompt"
  else
    printf '  %s ' "$prompt"
  fi
  read -rs val; echo
  [ -z "$val" ] && val="$cur"
  printf '%s' "$val"
}

existing() { [ -f "$ENVF" ] && grep -E "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }

echo "Sponsor credentials — nothing is echoed, nothing is logged."
echo
BD=$(ask BRIGHTDATA_API_KEY   "Bright Data API key:      " "$(existing BRIGHTDATA_API_KEY)")
PI=$(ask PORT_CLIENT_ID       "Port client id:           " "$(existing PORT_CLIENT_ID)")
PS=$(ask PORT_CLIENT_SECRET   "Port client secret:       " "$(existing PORT_CLIENT_SECRET)")
SE=$(ask SIGNOZ_ENDPOINT      "SigNoz endpoint (or skip):" "$(existing SIGNOZ_ENDPOINT)")
SK=$(ask SIGNOZ_INGESTION_KEY "SigNoz key (or skip):     " "$(existing SIGNOZ_INGESTION_KEY)")

umask 077
: > "$ENVF"; chmod 600 "$ENVF"
{
  echo "# written by tools/setup_creds.sh — gitignored, 0600"
  [ -n "$BD" ] && echo "BRIGHTDATA_API_KEY=$BD"
  [ -n "$PI" ] && echo "PORT_CLIENT_ID=$PI"
  [ -n "$PS" ] && echo "PORT_CLIENT_SECRET=$PS"
  [ -n "$SE" ] && echo "SIGNOZ_ENDPOINT=$SE"
  [ -n "$SK" ] && echo "SIGNOZ_INGESTION_KEY=$SK"
} >> "$ENVF"

echo
echo "wrote $ENVF ($(stat -f '%Sp' "$ENVF" 2>/dev/null || stat -c '%A' "$ENVF"))"
echo
exec "$ROOT/tools/withenv.sh" python3 "$ROOT/tools/creds.py"
