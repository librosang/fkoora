#!/bin/sh
# docker-entrypoint.sh
#
# Docker container entrypoint for the football match center backend.
#
# The backend is SPLIT into two roles since the api/scraper separation -
# pick one per container with SERVICE_ROLE (default: api):
#
#   SERVICE_ROLE=api     read-only Flask API (gunicorn, port 9000). Serves
#                        the Next.js frontend from PostgreSQL; records data
#                        gaps as refresh_jobs rows. Never scrapes goal.com.
#   SERVICE_ROLE=worker  scraper worker (python -m scraper.cli worker):
#                        freshness scheduler + refresh_jobs consumer. The
#                        ONLY process that talks to goal.com. Also runs the
#                        optional historical bootstrap (BOOTSTRAP_ON_START).
#   SERVICE_ROLE=migrate ONE-SHOT init container: applies the idempotent
#                        schema script + the TEXT -> TIMESTAMPTZ/DATE type
#                        migration, prints a report and EXITS 0. Run it
#                        before api/worker (docker compose's
#                        `service_completed_successfully` gate) so both
#                        start against a fully typed database instead of
#                        racing each other's ALTERs on a legacy volume.
#   SERVICE_ROLE=all     legacy single-container mode: background worker
#                        process + gunicorn foreground (pre-split behavior).
#
# On every container start it:
#   1. (worker/all only, optional) launches the one-time historical
#      bootstrap in the background
#   2. execs gunicorn (api/all) or the worker loop (worker) as PID 1
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
#   SERVICE_ROLE             api (default) | worker | all
#   BOOTSTRAP_ON_START       1 = run bootstrap on start (worker/all roles),
#                            0 = skip (default 0)
#   BOOTSTRAP_YEARS_BACK     years back to walk               (default 10)
#   BOOTSTRAP_DAYS_AHEAD     days forward to walk             (default 365)
#   BOOTSTRAP_NO_DETAILS     1 = listings only (no enrichment) (default 0)
#   BOOTSTRAP_NO_SLOW        1 = use normal 1s rate-limit     (default 0, NOT recommended)
#   BOOTSTRAP_ALL            1 = enrich ALL competitions      (default 0 = majors only)
#   BOOTSTRAP_FORCE          1 = re-run even if marker exists (default 0)
#   BOOTSTRAP_LOG            log file path                    (default /app/bootstrap.log)
#   BOOTSTRAP_MARKER_PATH    marker file path                 (default /app/.bootstrap_complete)
#   FOOTBALL_DB_URL          PostgreSQL DSN                   (required)
#
# Worker tuning (see scraper/worker.py): SCHEDULER_ROLE, WORKER_POLL_SEC,
# REFRESH_*_SEC / ENRICH_*_SEC / AR_*_SEC / COMP_*_SEC / ON_DEMAND_RETRY_SEC.
# API tuning (see scraper/api.py): IMG_CACHE_DIR, IMG_MEM_CACHE_MB,
# DB_POOL_MIN/MAX, COMPETITION_TTL_SEC, API_CRON_SECRET.
# Shared response cache + live layer (see scraper/apicache.py,
# scraper/live.py, scraper/sse.py): REDIS_URL (set it on the api AND the
# worker container - the worker invalidates the API's cache, maintains the
# fk:live:v1:* hot cache and publishes SSE events), SSE_HEARTBEAT_SEC,
# SSE_POLL_SEC, LIVE_* knobs, adaptive polling PROVIDER_POLL_*_SECONDS,
# API_CACHE_TTL_* per-endpoint TTL overrides.
#
# Usage:
#   docker run -e FOOTBALL_DB_URL=... fkoora-api                        # api
#   docker run -e SERVICE_ROLE=worker -e FOOTBALL_DB_URL=... fkoora-api # worker
#   docker run -e SERVICE_ROLE=all -e BOOTSTRAP_ON_START=1 \
#              -e FOOTBALL_DB_URL=... fkoora-api          # legacy all-in-one
#
# Tail the bootstrap / worker logs (worker/all):
#   docker exec -it <container> tail -f /app/bootstrap.log
#   docker exec -it <container> tail -f /app/worker.log
#
# Stop the bootstrap (worker keeps running):
#   docker exec <container> pkill -f 'scraper.cli bootstrap'

set -e

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# ---- role ----------------------------------------------------------------
SERVICE_ROLE="${SERVICE_ROLE:-api}"

# ---- one-shot migration container (init job) -------------------------------
# Applies schema.sql (CREATE TABLE IF NOT EXISTS ...) + the idempotent
# TEXT -> TIMESTAMPTZ/DATE conversion, prints a report and exits 0. In the
# compose files this is the `fkoora-migrate` service that api/worker gate
# on with `condition: service_completed_successfully`. On an already
# migrated database it is a fast no-op (a couple of catalog reads).
if [ "$SERVICE_ROLE" = "migrate" ]; then
    echo "[$(timestamp)] [entrypoint] one-shot schema + type migration starting"
    exec python -m scraper.cli migrate-types
fi

if [ "$SERVICE_ROLE" = "api" ] && [ "${API_ENABLE_SCHEDULER:-1}" != "0" ]; then
    # old deployments expected THIS container to keep the data fresh
    echo "[$(timestamp)] [entrypoint] WARNING: the scheduler no longer runs inside the API" 1>&2
    echo "[$(timestamp)] [entrypoint] WARNING: run a SERVICE_ROLE=worker container (or set" 1>&2
    echo "[$(timestamp)] [entrypoint] WARNING:  SERVICE_ROLE=all) or the database goes stale" 1>&2
fi


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
# SSE (/api/events/live) holds one gthread thread per connected browser,
# so the default rose 8 -> 32 with the live-update layer. Estimate
# ~1 thread per concurrent SSE client + headroom for normal requests;
# for thousands of concurrent SSE clients switch the worker class to
# gevent (same image: pip install gevent, --worker-class gevent).
GUNICORN_THREADS="${GUNICORN_THREADS:-32}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-180}"
# how long gunicorn waits for in-flight requests (incl. open SSE streams)
# after SIGTERM before force-killing - keep >= stop_grace_period in compose
GUNICORN_GRACEFUL_TIMEOUT="${GUNICORN_GRACEFUL_TIMEOUT:-30}"
GUNICORN_KEEP_ALIVE="${GUNICORN_KEEP_ALIVE:-5}"
GUNICORN_LOG_LEVEL="${GUNICORN_LOG_LEVEL:-info}"

# ---- optional bootstrap (worker/all roles only) ---------------------------
if [ "$SERVICE_ROLE" = "api" ]; then
    if [ "$BOOTSTRAP_ON_START" = "1" ]; then
        echo "[$(timestamp)] [entrypoint] BOOTSTRAP_ON_START=1 ignored in the api role - set SERVICE_ROLE=worker (or all) to run the walk" 1>&2
    fi
elif [ "$BOOTSTRAP_ON_START" = "1" ]; then

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
    echo "[$(timestamp)] [entrypoint] to enable on next start (worker/all role): -e BOOTSTRAP_ON_START=1"
fi

# ---- foreground process ---------------------------------------------------
# api:     gunicorn as PID 1 (owns signal handling for graceful shutdowns)
# worker:  the worker loop as PID 1
# all:     worker as a background child (cleaned up with the container),
#          gunicorn in the foreground

if [ "$SERVICE_ROLE" = "worker" ]; then
    echo "[$(timestamp)] [entrypoint] starting scraper worker (SERVICE_ROLE=worker)"
    exec python -m scraper.cli worker
fi

if [ "$SERVICE_ROLE" = "all" ]; then
    echo "[$(timestamp)] [entrypoint] starting scraper worker in background (SERVICE_ROLE=all)"
    python -m scraper.cli worker >> /app/worker.log 2>&1 &
    echo "[$(timestamp)] [entrypoint] worker PID: $! (log: tail -f /app/worker.log)"
fi

echo "[$(timestamp)] [entrypoint] starting gunicorn on $GUNICORN_BIND (read-only API)"

# scraper/wsgi.py is the gunicorn entry: it builds the app via create_app()
# (env-driven: FOOTBALL_DB_URL, REDIS_URL, ...) AND configures logging so
# application logs reach `docker compose logs` alongside gunicorn's access
# lines. One gthread thread per open SSE stream + regular requests; exec
# hands over PID 1 so SIGTERM reaches gunicorn directly (graceful: in-flight
# requests + SSE streams get GUNICORN_GRACEFUL_TIMEOUT seconds to finish
# before the worker is killed).
exec gunicorn \
    --bind "$GUNICORN_BIND" \
    --workers "$GUNICORN_WORKERS" \
    --threads "$GUNICORN_THREADS" \
    --worker-class gthread \
    --timeout "$GUNICORN_TIMEOUT" \
    --graceful-timeout "$GUNICORN_GRACEFUL_TIMEOUT" \
    --keep-alive "$GUNICORN_KEEP_ALIVE" \
    --worker-tmp-dir /dev/shm \
    --access-logfile - \
    --error-logfile - \
    --log-level "$GUNICORN_LOG_LEVEL" \
    scraper.wsgi:app
