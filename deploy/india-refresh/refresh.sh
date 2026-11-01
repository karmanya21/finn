#!/usr/bin/env bash
# Daily filings refresh (run by cron; see setup.sh). Pulls the latest records, rebuilds the
# filings digest, and pushes it only if it changed. If BSE fails for most companies the
# build keeps the previous digest and leaves a marker; this script then exits non-zero.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$DIR"
echo "== $(date -u +%FT%TZ)"

# Everything this job writes is regenerated each run. Drop yesterday's leftovers first: an
# uncommitted audit.json once blocked every pull from 31 Oct.
git checkout -q -- web/public/data data/out/audit.json 2>/dev/null || true
git pull -q --rebase origin main || { echo "git pull failed"; exit 1; }
(cd backend && .venv/bin/python -m scripts.build_push filings) || { echo "build failed"; exit 1; }

# The digest, any promise progress a new results filing moved (company records and the
# index), and the audit of what was built.
git add web/public/data/filings.json web/public/data/*.json data/out/audit.json
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
