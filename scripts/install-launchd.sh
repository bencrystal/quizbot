#!/usr/bin/env bash
# Installs (or reinstalls) both scheduled jobs for this checkout. Run from anywhere.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
for job in short long; do
  dst="$HOME/Library/LaunchAgents/com.quizbot.$job.plist"
  launchctl unload "$dst" 2>/dev/null || true
  sed "s#__REPO__#$REPO#g" "$REPO/scripts/com.quizbot.$job.plist" > "$dst"
  launchctl load "$dst"
  echo "loaded com.quizbot.$job"
done
echo "Test now:   launchctl start com.quizbot.short   (then: tail -f $REPO/state/quizbot.log)"
echo "Remove:     launchctl unload ~/Library/LaunchAgents/com.quizbot.{short,long}.plist"
