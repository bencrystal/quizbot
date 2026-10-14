#!/usr/bin/env bash
# Entry point for launchd/cron. Fully self-contained: no shell profile assumed.
#   scripts/run.sh pokemon            render + (if settings.upload) upload one episode
#   scripts/run.sh pokemon --upload   force upload
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # ffmpeg from Homebrew
mkdir -p state
exec >> state/quizbot.log 2>&1
echo "=== $(date '+%F %T') run $* ==="

# single-instance lock so an overlapping schedule can't double-render
exec 9>state/.lock
flock -n 9 || { echo "another run in progress, exiting"; exit 0; }

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

PACK="${1:-pokemon}"; shift || true
"$PY" -m quizbot retry-uploads || true     # drain anything that rendered but failed to upload last time
"$PY" -m quizbot run --pack "$PACK" "$@"
