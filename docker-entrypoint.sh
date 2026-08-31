#!/bin/sh
# docker-entrypoint.sh
#
# Docker container entrypoint for the football scraper API.
#
# On every container start it:
#   1. (optional) launches the one-time historical bootstrap in the background
#   2. execs gunicorn as the foreground / PID-1 process
#
# The bootstrap runs in PARALLEL with the API so the API can start serving
# immediately. The walk is slow + polite (~2.5s per request, 3s between days)
# and fully resumable: if the container restarts mid-walk, the next start
# simply picks up where it left off because `scrape_runs` already records
# every successful date.
#
# Once the full walk finishes, a marker file is written so subsequent restarts
# are instant (no DB scan). Delete the marker (or set BOOTSTRAP_FORCE=1) to
# re-run, e.g. after expanding BOOTSTRAP_YEARS_BACK.
#
# Environment variables (all optional):
#   BOOTSTRAP_ON_START        1 = run bootstrap on start, 0 = skip (default 0)
#   BOOTSTRAP_YEARS_BACK      years back to walk               (default 10)
#   BOOTSTRAP_DAYS_AHEAD      days forward to walk             (default 365)
#   BOOTSTRAP_NO_DETAILS      1 = listings only (no enrichment) (default 0)
#   BOOTSTRAP_NO_SLOW         1 = use normal 1s rate-limit     (default 0, NOT recommended)
#   BOOTSTRAP_ALL             1 = enrich ALL competitions      (default 0 = majors only)
#   BOOTSTRAP_FORCE           1 = re-run even if marker exists (default 0)
#   BOOTSTRAP_LOG             log file path                    (default /app/bootstrap.log)
#   BOOTSTRAP_MARKER_PATH     marker file path                 (default /app/.bootstrap_complete)
#   FOOTBALL_DB_URL           PostgreSQL DSN                   (required)
#
# Existing vars (still respected):
#   API_ENABLE_SCHEDULER      1 = in-process freshness scheduler (default 1)
#   IMG_CACHE_DIR             crest/logo disk cache            (default /app/img_cache)
#
# Usage:
#   docker run -e BOOTSTRAP_ON_START=1 -e FOOTBALL_DB_URL=... fkoora-api
#   docker run -e BOOTSTRAP_ON_START=1 -e BOOTSTRAP_YEARS_BACK=5 fkoora-api
#
# Tail the bootstrap log:
#   docker exec -it <container> tail -f /app/bootstrap.log
#
# Stop the bootstrap (API keeps running):
#   docker exec <container> pkill -f 'scraper.cli bootstrap'

set -e

# ---- defaults -------------------------------------------------------------
BOOTSTRAP_ON_START="${BOOTSTRAP_ON_START:-0}"
BOOTSTRAP_FORCE="${BOOTSTRAP_FORCE:-0}"
BOOTSTRAP_YEARS_BACK="${BOOTSTRAP_YEARS_BACK:-10}"
BOOTSTRAP_DAYS_AHEAD="${BOOTSTRAP_DAYS_AHEAD:-365}"
BOOTSTRAP_NO_DETAILS="${BOOTSTRAP_NO_DETAILS:-0}"
BOOTSTRAP_NO_SLOW="${BOOTSTRAP_NO_SLOW:-0}"
BOOTSTRAP_ALL="${BOOTSTRAP_ALL:-0}"
BOOTSTRAP_LOG="${BOOTSTRAP_LOG:-/app/bootstrap.log}"
BOOTSTRAP_MARKER_PATH="${BOOTSTRAP_MARKER_PATH:-/app/.bootstrap_complete}"

GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:9000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_THREADS="${GUNICORN_THREADS:-8}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-180}"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# ---- optional bootstrap ---------------------------------------------------
if [ "$BOOTSTRAP_ON_START" = "1" ]; then

    # Build the CLI flag list. We pass --no-details / --no-slow / --all only
    # when enabled, so the CLI defaults (details on, slow on, major-only)
    # stay in effect otherwise.
    BOOTSTRAP_ARGS="--years-back $BOOTSTRAP_YEARS_BACK --days-ahead $BOOTSTRAP_DAYS_AHEAD"
    [ "$BOOTSTRAP_NO_DETAILS" = "1" ] && BOOTSTRAP_ARGS="$BOOTSTRAP_ARGS --no-details"
    [ "$BOOTSTRAP_NO_SLOW"    = "1" ] && BOOTSTRAP_ARGS="$BOOTSTRAP_ARGS --no-slow"
    [ "$BOOTSTRAP_ALL"        = "1" ] && BOOTSTRAP_ARGS="$BOOTSTRAP_ARGS --all"

    if [ -f "$BOOTSTRAP_MARKER_PATH" ] && [ "$BOOTSTRAP_FORCE" != "1" ]; then
        echo "[$(timestamp)] [entrypoint] bootstrap marker exists ($BOOTSTRAP_MARKER_PATH), skipping walk"
        echo "[$(timestamp)] [entrypoint] to re-run: delete the marker OR set BOOTSTRAP_FORCE=1"
    else
        # Remove a stale marker if we are about to (re)run.
        rm -f "$BOOTSTRAP_MARKER_PATH"

        echo "[$(timestamp)] [entrypoint] launching historical bootstrap in background"
        echo "[$(timestamp)] [entrypoint] args: $BOOTSTRAP_ARGS"
        echo "[$(timestamp)] [entrypoint] log:  tail -f $BOOTSTRAP_LOG"
        echo "[$(timestamp)] [entrypoint] to stop: docker exec <ctr> pkill -f 'scraper.cli bootstrap'"

        # Detached subshell: runs bootstrap, writes the marker on full success.
        # Uses exec inside the subshell so signal forwarding works (Ctrl-C /
        # SIGTERM kills bootstrap directly, not sh -c).
        (
            echo "[$(timestamp)] [bootstrap] starting: python -m scraper.cli bootstrap $BOOTSTRAP_ARGS"
            # shellcheck disable=SC2086  # intentional word-splitting on flags
            python -m scraper.cli bootstrap $BOOTSTRAP_ARGS
            rc=$?
            if [ $rc -eq 0 ]; then
                # Write the marker ONLY on clean completion. On Ctrl-C / crash
                # the marker stays absent so the next start resumes the walk.
                : > "$BOOTSTRAP_MARKER_PATH"
                echo "[$(timestamp)] [bootstrap] walk complete, marker written: $BOOTSTRAP_MARKER_PATH"
            else
                echo "[$(timestamp)] [bootstrap] exited with code $rc - marker NOT written, will resume on next start"
            fi
        ) >> "$BOOTSTRAP_LOG" 2>&1 &

        BOOTSTRAP_PID=$!
        echo "[$(timestamp)] [entrypoint] bootstrap PID: $BOOTSTRAP_PID"
    fi
else
    echo "[$(timestamp)] [entrypoint] BOOTSTRAP_ON_START=0 - skipping historical bootstrap"
    echo "[$(timestamp)] [entrypoint] to enable on next start: -e BOOTSTRAP_ON_START=1"
fi

# ---- gunicorn as PID 1 ----------------------------------------------------
# We pass straight through to gunicorn so it owns signal handling for graceful
# shutdowns. The bootstrap (if any) is a child of THIS process and gets cleaned
# up by Docker when gunicorn exits.

echo "[$(timestamp)] [entrypoint] starting gunicorn on $GUNICORN_BIND"

exec gunicorn \
    --bind "$GUNICORN_BIND" \
    --workers "$GUNICORN_WORKERS" \
    --threads "$GUNICORN_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    scraper.wsgi:app
