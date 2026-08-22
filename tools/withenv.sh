#!/usr/bin/env bash
# Run a command with .env.local loaded, without printing any of it.
#
# The values live in a 0600 file the developer wrote themselves. Nothing here
# echoes a value, and `set -a` means the command's own child processes inherit
# them without any of it reaching a log or a shell history.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT/.env.local" ]; then
  set -a; . "$ROOT/.env.local"; set +a
fi
exec "$@"
