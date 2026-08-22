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
  # Prompts go to stderr, not stdout. The caller captures this function with
  # $(...), which captures stdout — so a prompt written there ends up inside
  # the value. That produced a .env.local whose first line was the prompt text
  # followed by the key, and sourcing it tried to execute the word 'Bright'.
  if [ -n "$cur" ]; then
    printf '  %s [enter to keep existing] ' "$prompt" >&2
  else
    printf '  %s ' "$prompt" >&2
  fi
  read -rs val < /dev/tty; printf '\n' >&2
  [ -z "$val" ] && val="$cur"
  printf '%s' "$val"
}

existing() {
  # Only accept a well-formed KEY=value line. A previous run wrote a malformed
  # file once; re-reading it would silently carry the corruption forward.
  [ -f "$ENVF" ] || return 0
  grep -E "^$1=[^[:space:]]+$" "$ENVF" 2>/dev/null | head -1 | cut -d= -f2- || true
}

echo "Sponsor credentials — nothing is echoed, nothing is logged."
echo
BD=$(ask BRIGHTDATA_API_KEY   "Bright Data API key:      " "$(existing BRIGHTDATA_API_KEY)")
PI=$(ask PORT_CLIENT_ID       "Port client id:           " "$(existing PORT_CLIENT_ID)")
PS=$(ask PORT_CLIENT_SECRET   "Port client secret:       " "$(existing PORT_CLIENT_SECRET)")
SE=$(ask SIGNOZ_ENDPOINT      "SigNoz endpoint (or skip):" "$(existing SIGNOZ_ENDPOINT)")
SK=$(ask SIGNOZ_INGESTION_KEY "SigNoz key (or skip):     " "$(existing SIGNOZ_INGESTION_KEY)")

for pair in "BRIGHTDATA_API_KEY:$BD" "PORT_CLIENT_ID:$PI" "PORT_CLIENT_SECRET:$PS"; do
  name="${pair%%:*}"; v="${pair#*:}"
  case "$v" in
    *[[:space:]]*) echo "  refusing to write $name — value contains whitespace." >&2
                   echo "  paste the value only, with no surrounding text." >&2; exit 1 ;;
  esac
done

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
