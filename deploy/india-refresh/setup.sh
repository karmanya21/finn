#!/usr/bin/env bash
# One-time setup of the daily filings refresh on a server in India (Ubuntu 24.04).
#
#   git clone https://github.com/karmanya21/finn.git ~/finn && bash ~/finn/deploy/india-refresh/setup.sh
#
# Steps: install Python, check BSE answers this server (stop if not), create a deploy key
# that can push to this one repo, store the Gemini key on this server only, and schedule
# refresh.sh daily at 19:15 IST. Safe to re-run.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"
REPO="karmanya21/finn"
KEY="$HOME/.ssh/finn_deploy"

echo "== 1/5 Installing git and Python"
sudo apt-get update -qq && sudo apt-get install -y -qq git python3-venv > /dev/null
cd "$DIR/backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 2/5 Checking that BSE answers this server"
if ! .venv/bin/python -m scripts.check_bse; then
  echo "BSE refuses this server's address, so a daily filings refresh can't run here. Stopping."
  exit 1
fi

echo "== 3/5 Deploy key (lets this server push to $REPO, and nothing else)"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "finn-refresh@$(hostname)" -f "$KEY"
if ! grep -q "Host github-finn" ~/.ssh/config 2>/dev/null; then
  cat >> ~/.ssh/config <<EOF
Host github-finn
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
  chmod 600 ~/.ssh/config
fi
git -C "$DIR" remote set-url origin "git@github-finn:$REPO.git"
until ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -T github-finn 2>&1 | grep -q "successfully authenticated"; do
  echo
  echo "Add this public key at https://github.com/$REPO/settings/keys/new"
  echo "(title: finn-refresh, tick \"Allow write access\"):"
  echo
  cat "$KEY.pub"
  echo
  read -r -p "Press Enter once it's added... "
done
echo "GitHub accepts the deploy key."

echo "== 4/5 Gemini API key (stored only on this server, in $DIR/.env)"
if ! grep -q "^GEMINI_API_KEY=." "$DIR/.env" 2>/dev/null; then
  read -r -s -p "Paste your Gemini API key: " GK; echo
  printf 'GEMINI_API_KEY=%s\n' "$GK" > "$DIR/.env"
  chmod 600 "$DIR/.env"
fi

echo "== 5/5 Scheduling refresh.sh daily at 19:15 IST (13:45 UTC)"
git -C "$DIR" config user.name "finn-refresh"
git -C "$DIR" config user.email "finn-refresh@users.noreply.github.com"
chmod +x "$DIR/deploy/india-refresh/refresh.sh"
LINE="45 13 * * * $DIR/deploy/india-refresh/refresh.sh >> $HOME/finn-refresh.log 2>&1"
( crontab -l 2>/dev/null | grep -v "india-refresh/refresh.sh" || true; echo "$LINE" ) | crontab -

echo
echo "Done. Running the first refresh now (a few minutes; later runs reuse the cache)."
"$DIR/deploy/india-refresh/refresh.sh"
