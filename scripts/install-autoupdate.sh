#!/usr/bin/env bash
# Install the launchd job that keeps this checkout current. macOS only.
#
#     ./scripts/install-autoupdate.sh                 # every hour
#     ./scripts/install-autoupdate.sh --interval 21600  # every 6 hours
#     ./scripts/install-autoupdate.sh --status
#     ./scripts/install-autoupdate.sh --uninstall
#
# Writes a LaunchAgent to ~/Library/LaunchAgents, which runs as you, in your
# login session — which is what lets it post a notification and read your git
# credentials if the repository ever stops being public.
#
# What this does and does not do, plainly: it pulls, reinstalls when the
# dependencies move, and runs the tests. It cannot reload the Chrome extension,
# so it notifies you instead. It will not touch uncommitted work.

set -euo pipefail

cd "$(dirname "$0")/.."
repo=$(pwd)

LABEL="com.autojobapply-tracker.update"
plist="${HOME}/Library/LaunchAgents/${LABEL}.plist"
interval=3600
action=install

while [ $# -gt 0 ]; do
  case "$1" in
    --interval) interval="${2:?--interval needs a number of seconds}"; shift 2 ;;
    --uninstall) action=uninstall; shift ;;
    --status) action=status; shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$interval" in
  ''|*[!0-9]*) echo "--interval must be whole seconds" >&2; exit 2 ;;
esac
[ "$interval" -ge 300 ] || {
  echo "--interval below 300s is pointless: a fetch that often is noise, not freshness." >&2
  exit 2
}

[ "$(uname -s)" = "Darwin" ] || {
  echo "This installs a macOS LaunchAgent; on Linux use a systemd timer or cron:" >&2
  echo "  0 * * * * cd $repo && ./scripts/autoupdate.sh" >&2
  exit 1
}

# launchctl gained bootstrap/bootout in 10.11 and deprecated load/unload; try
# the modern pair first and fall back so this works on an older machine too.
unload() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$plist" 2>/dev/null \
    || true
}

load() {
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null \
    || launchctl load -w "$plist"
}

if [ "$action" = status ]; then
  if launchctl list | grep -q "$LABEL"; then
    echo "Installed and loaded:"
    launchctl list | grep "$LABEL" | sed 's/^/  /'
    echo "  plist:    $plist"
  else
    echo "Not loaded. Install it with: ./scripts/install-autoupdate.sh"
  fi
  log="${HOME}/Library/Logs/autojobapply-tracker-update.log"
  [ -f "$log" ] && { echo; echo "Last run:"; tail -12 "$log" | sed 's/^/  /'; }
  exit 0
fi

if [ "$action" = uninstall ]; then
  unload
  rm -f "$plist"
  echo "Removed. Updates are manual again: ./scripts/update.sh"
  exit 0
fi

mkdir -p "$(dirname "$plist")"
# launchd will not spawn a job whose StandardOutPath it cannot create, and the
# plist below names a file in here. Normally present on macOS; cheap to be sure.
mkdir -p "${HOME}/Library/Logs"

# launchd gives a job almost no PATH, so git, python and node all have to be
# findable. Homebrew first (Apple silicon, then Intel), then the system.
cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${repo}/scripts/autoupdate.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${repo}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>

  <key>StartInterval</key>
  <integer>${interval}</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/autojobapply-tracker-launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/autojobapply-tracker-launchd.log</string>

  <key>ProcessType</key>
  <string>Background</string>
  <key>LowPriorityIO</key>
  <true/>
  <key>Nice</key>
  <integer>5</integer>
</dict>
</plist>
PLIST

plutil -lint "$plist" >/dev/null || { echo "generated plist is malformed" >&2; exit 1; }

unload
load

cat <<EOF
Installed.

  every:  ${interval}s
  runs:   ${repo}/scripts/autoupdate.sh
  plist:  ${plist}
  log:    ${HOME}/Library/Logs/autojobapply-tracker-update.log

It has just run once (RunAtLoad). Check it with:

  ./scripts/install-autoupdate.sh --status

macOS may ask to allow notifications the first time an update lands. Without
that permission everything still works, you just have to read the log to know
an update happened — and the extension needs a Chrome reload either way.

Stop it with: ./scripts/install-autoupdate.sh --uninstall
EOF
