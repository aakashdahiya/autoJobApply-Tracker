#!/usr/bin/env bash
# Pull the latest code and make the checkout runnable again.
#
#     ./scripts/update.sh
#
# Does the four things that are easy to half-do by hand: pull, reinstall if the
# dependencies moved, re-run the tests, and tell you the extension needs a
# reload. Refuses to pull over uncommitted work rather than deciding for you
# what to do with it.

set -euo pipefail

cd "$(dirname "$0")/.."
root=$(pwd)

# Colour only for a human at a terminal. When this is piped -- into a log, or
# into the launchd job's notification -- escape codes are noise, and a macOS
# notification renders them as literal garbage.
if [ -t 1 ]; then
  bold=$'\033[1m'; yellow=$'\033[33m'; red=$'\033[31m'; off=$'\033[0m'
else
  bold=''; yellow=''; red=''; off=''
fi

say() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
warn() { printf '%s%s%s\n' "$yellow" "$*" "$off"; }
die() { printf '%s%s%s\n' "$red" "$*" "$off" >&2; exit 1; }

branch=$(git rev-parse --abbrev-ref HEAD)

# --- 1. don't clobber your own work --------------------------------------
if ! git diff --quiet || ! git diff --cached --quiet; then
  git status --short
  die "You have uncommitted changes. Commit or stash them, then run this again."
fi

# --- 2. pull -------------------------------------------------------------
say "Pulling $branch"
before=$(git rev-parse HEAD)
# The network is the one step worth retrying: a dropped fetch is not a reason
# to leave the checkout stale.
for delay in 0 2 4 8; do
  [ "$delay" -gt 0 ] && { warn "fetch failed; retrying in ${delay}s"; sleep "$delay"; }
  if git pull --ff-only origin "$branch"; then
    pulled=yes
    break
  fi
done
[ "${pulled:-}" = yes ] || die "Could not pull origin/$branch. Check your network, then retry."
after=$(git rev-parse HEAD)

if [ "$before" = "$after" ]; then
  say "Already up to date at ${after:0:8}."
else
  say "Updated ${before:0:8} → ${after:0:8}"
  git --no-pager log --oneline "$before..$after"
fi

# --- 3. reinstall only when the dependencies actually moved ---------------
if [ ! -x .venv/bin/python ]; then
  say "Creating the virtualenv"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install -e ".[dev]"
elif [ "$before" != "$after" ] && ! git diff --quiet "$before" "$after" -- pyproject.toml; then
  say "Dependencies changed — reinstalling"
  .venv/bin/pip install -e ".[dev]"
fi

# --- 4. prove it still works ---------------------------------------------
say "Running the tests"
if [ ! -f profile.yaml ]; then
  warn "No profile.yaml yet — the suite uses profile.example.yaml, so this still passes,"
  warn "but nothing will render your real history until you fill one in."
fi
.venv/bin/python -m pytest -q

if command -v node >/dev/null 2>&1; then
  say "Running the extension tests"
  (cd extension && npm install --silent && npm test)
else
  warn "node not found — skipping the extension tests"
fi

# --- 5. what only you can do ---------------------------------------------
say "Done."
cat <<EOF
Chrome does not pick up extension changes on its own:

  1. chrome://extensions  ->  Job Tracker  ->  Reload (the circular arrow)
  2. Accept any new-permission prompt. Host permissions added since you
     installed are NOT granted silently; if no prompt appears, remove the
     extension and "Load unpacked" $root/extension again.
  3. Hard-refresh any LinkedIn or Indeed tab already open (Cmd+Shift+R).

Then start the API if it is not already running:

  .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --reload
EOF
