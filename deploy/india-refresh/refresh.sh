#!/usr/bin/env bash
# Daily filings refresh (run by cron; see setup.sh). Pulls the latest records, rebuilds the
# filings digest, and pushes it only if it changed. If BSE fails for most companies the
# build keeps the previous digest and leaves a marker; this script then exits non-zero.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$DIR"
echo "== $(date -u +%FT%TZ)"

git pull -q --rebase origin main || { echo "git pull failed"; exit 1; }
(cd backend && .venv/bin/python -m scripts.build_push filings) || { echo "build failed"; exit 1; }

git add web/public/data/filings.json
if git diff --cached --quiet; then
  echo "No changes."
else
  git commit -q -m "Refresh filings ($(date -u +%F))" && git push -q origin main && echo "Pushed."
fi

if [ -f .filings-stale ]; then
  echo "STALE: BSE failed for: $(cat .filings-stale)"
  rm -f .filings-stale
  exit 1
fi
