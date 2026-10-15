#!/usr/bin/env bash
# Entry point for launchd/cron. Self-contained: no shell profile assumed.
#   scripts/run.sh short            daily Short
#   scripts/run.sh long             weekly long-form
#   scripts/run.sh long --theme gen1 --upload
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # ffmpeg from Homebrew
mkdir -p state
exec >> state/quizbot.log 2>&1
echo "=== $(date '+%F %T') run $* ==="

# single-instance lock so overlapping schedules can't double-render (flock is in Homebrew `util-linux` on macOS;
# falls back to a plain lock dir if missing)
if command -v flock >/dev/null; then
  exec 9>state/.lock
  flock -n 9 || { echo "another run in progress, exiting"; exit 0; }
else
  mkdir state/.lockdir 2>/dev/null || { echo "another run in progress, exiting"; exit 0; }
  trap 'rmdir state/.lockdir' EXIT
fi

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

FORMAT="${1:-short}"; shift || true
"$PY" -m quizbot retry-uploads || true     # drain anything that rendered but failed to upload last time
"$PY" -m quizbot run --format "$FORMAT" "$@"
