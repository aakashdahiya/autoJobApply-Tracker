#!/usr/bin/env bash
# What the launchd job runs. Not meant to be run by hand — use update.sh.
#
# Wraps update.sh rather than repeating it, so there is exactly one definition
# of what "up to date" means. All this adds is the background manners:
#
#   * silent when nothing changed, so an hourly job is not an hourly interruption
#   * a macOS notification when an update actually lands, because Chrome will
#     still be running the old extension until you reload it
#   * a loud notification when the update fails, since a background job that
#     fails quietly is worse than no background job
#   * everything appended to a log, with the last run's detail kept separately
#
# It never resolves anything on your behalf: uncommitted work stops the pull
# (update.sh's own guard) and you get told, rather than having your changes
# stashed by a cron job you forgot about.

set -uo pipefail

cd "$(dirname "$0")/.."
repo=$(pwd)

log_dir="${HOME}/Library/Logs"
[ -d "$log_dir" ] || log_dir="$repo/data"
mkdir -p "$log_dir"
log="$log_dir/autojobapply-tracker-update.log"
last="$log_dir/autojobapply-tracker-update.last"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

notify() {
  # osascript is only there on macOS, and only reaches a notification centre
  # when this runs as a per-user LaunchAgent. Failure to notify is not failure.
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}

before=$(git rev-parse HEAD 2>/dev/null || echo unknown)

output=$(bash "$repo/scripts/update.sh" 2>&1)
status=$?

after=$(git rev-parse HEAD 2>/dev/null || echo unknown)

{
  printf '\n===== %s =====\n' "$(stamp)"
  printf 'exit=%s  %s -> %s\n' "$status" "${before:0:8}" "${after:0:8}"
  printf '%s\n' "$output"
} >>"$log"

# The full text of the most recent run, so debugging does not mean scrolling
# a log that grows forever.
printf '%s\n' "$output" >"$last"

if [ "$status" -ne 0 ]; then
  reason=$(printf '%s' "$output" | grep -iE "uncommitted|could not pull|failed" | head -1)
  notify "Job Tracker: update failed" "${reason:-see $last}"
  exit "$status"
fi

if [ "$before" != "$after" ]; then
  subjects=$(git --no-pager log --oneline "$before..$after" 2>/dev/null | head -3 | cut -c1-60)
  notify "Job Tracker updated — reload the extension" \
    "$(printf '%s' "$subjects" | tr '\n' ' ')"
fi

# Unchanged: say nothing. The log has it.
exit 0
