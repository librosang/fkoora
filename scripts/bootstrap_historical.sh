#!/usr/bin/env bash
# scripts/bootstrap_historical.sh
#
# Launch the one-time, slow, polite historical bootstrap as a detached daemon
# so it survives the shell that started it. Output goes to bootstrap.log in
# the project root; per-day progress is appended to bootstrap.progress.log.
#
# Resumable: re-running the same command picks up where the previous run left
# off (any date with a successful scrape_runs row of the matching mode is
# skipped). Safe to interrupt with Ctrl-C or by killing the daemon - just
# re-run this script.
#
# Usage:
#   ./scripts/bootstrap_historical.sh                       # defaults: 10y back + 1y ahead, slow + details
#   ./scripts/bootstrap_historical.sh --years-back 5        # only last 5 years
#   ./scripts/bootstrap_historical.sh --days-ahead 30       # only next 30 days
#   ./scripts/bootstrap_historical.sh --no-details          # listings only (faster, no lineups/events)
#   ./scripts/bootstrap_historical.sh --no-slow             # use the normal (1s) rate limit - NOT recommended
#
# Tail the log:
#   tail -f bootstrap.log
#
# Stop the daemon:
#   pkill -f 'scraper.cli bootstrap' || true

set -euo pipefail

# Resolve project root from this script's location so it works from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_FILE="$PROJECT_ROOT/bootstrap.log"
PID_FILE="$PROJECT_ROOT/bootstrap.pid"

# Pass through any extra CLI flags to `scraper.cli bootstrap`.
EXTRA_ARGS=("$@")

# Default invocation: 10 years back, 1 year forward, slow + details.
# The defaults live in scraper/config.py and are surfaced by `bootstrap --help`.
ARGS=(bootstrap --years-back "${BOOTSTRAP_YEARS_BACK:-10}" --days-ahead "${BOOTSTRAP_DAYS_AHEAD:-365}")
ARGS+=("${EXTRA_ARGS[@]}")

echo "[$(date -u +%FT%TZ)] starting bootstrap: ${ARGS[*]}" | tee -a "$LOG_FILE"

# Use the bundled daemon.py to detach into the background. It survives the
# calling shell and re-execs the python module so we keep the same venv /
# PYTHONPATH. Falls back to a plain foreground invocation if daemon.py is
# unavailable (rare - it ships in scripts/).
if [[ -x "$SCRIPT_DIR/daemon.py" ]]; then
    DAEMON=(python3 "$SCRIPT_DIR/daemon.py" "$LOG_FILE")
else
    DAEMON=()
fi

# Make sure the DB URL is set; the scraper will use FOOTBALL_DB_URL or its
# built-in default (postgresql://localhost:5432/football).
export FOOTBALL_DB_URL="${FOOTBALL_DB_URL:-postgresql://postgres:postgres@localhost:5432/football}"

# Launch. We record the python process PID for convenience.
if [[ ${#DAEMON[@]} -gt 0 ]]; then
    nohup "${DAEMON[@]}" python3 -m scraper.cli "${ARGS[@]}" >/dev/null 2>&1 &
    echo $! > "$PID_FILE"
else
    nohup python3 -m scraper.cli "${ARGS[@]}" >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
fi

PID=$(cat "$PID_FILE")
cat <<EOF

Bootstrap launched as PID $PID (detached).

Logs:           tail -f $LOG_FILE
Progress log:   tail -f $PROJECT_ROOT/bootstrap.progress.log
PID file:       $PID_FILE

To stop:   pkill -f 'scraper.cli bootstrap'   (or: kill \$(cat $PID_FILE))
To resume: re-run this script - already-done dates are skipped automatically.

EOF
