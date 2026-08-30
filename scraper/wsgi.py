"""Gunicorn entry point for the JSON API (production / Docker).

    gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 8 scraper.wsgi:app

Workers & the scheduler
-----------------------
The API is I/O bound (PostgreSQL queries + the image proxy), so threads
scale it well; one worker + 8 threads is plenty for a personal site.

Scaling to several workers is now SAFE: the built-in scrape scheduler
elects exactly one leader across all API processes with a PostgreSQL
advisory lock (SCHEDULER_ROLE=auto, the default). Only the leader scrapes
goal.com; the others stand by and take over automatically if it dies, so
request capacity can grow without multiplying goal.com traffic. Mind the
per-process DB pool (DB_POOL_MIN / DB_POOL_MAX, default 1/8) against
PostgreSQL's max_connections when you add workers.

To disable the scheduler entirely (external `/api/cron/refresh` setups):
API_ENABLE_SCHEDULER=0 or SCHEDULER_ROLE=off. To force a process to always
scrape (dedicated scraper box alongside scheduler-less API replicas):
SCHEDULER_ROLE=force.
"""

from __future__ import annotations

import logging
import os
import threading

# gunicorn configures only its own loggers - without this, the application's
# INFO logs (scheduler ticks, scrape runs, image downloads) would be dropped
# and `docker compose logs fkoora-api` would show access lines only.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

from .api import create_app, run_scheduler

log = logging.getLogger("scraper.wsgi")

app = create_app(os.environ.get("FOOTBALL_DB_URL") or None)

# Start the freshness scheduler inside the API process unless explicitly
# disabled (gunicorn never calls api.run(), which normally starts it).
if os.environ.get("API_ENABLE_SCHEDULER", "1") != "0":
    _scheduler_started = False

    def _start_scheduler() -> None:
        global _scheduler_started
        if _scheduler_started:
            return
        _scheduler_started = True
        run_scheduler()

    threading.Thread(target=_start_scheduler, name="scheduler", daemon=True).start()
    log.info("scheduler enabled (in-process, single instance)")
else:
    log.info("scheduler disabled (API_ENABLE_SCHEDULER=0 - use /api/cron/refresh)")
