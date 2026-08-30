"""Gunicorn entry point for the read-only JSON API (production / Docker).

    gunicorn --bind 0.0.0.0:9000 --workers 2 --threads 8 scraper.wsgi:app

Pure consumer since the API/scraper split
-----------------------------------------
This process ONLY reads the database (plus the tiny refresh_jobs /
competition_views bookkeeping rows that tell the scraper worker what to
fetch). It never talks to goal.com and runs no scheduler - data freshness
is the job of the worker process:

    python -m scraper.cli worker          # or SERVICE_ROLE=worker container

Without a worker the API keeps serving whatever is stored (stale-while-
revalidate with refreshing=true flags), but nothing new arrives.

The API is I/O bound (PostgreSQL queries + the image proxy), so threads
scale it well; one worker + 8 threads is plenty for a personal site, and
several gunicorn workers are safe - none of them scrape, so the old
leader-election constraint is gone (mind the per-process DB pool,
DB_POOL_MIN / DB_POOL_MAX defaults 1/8, against PostgreSQL's
max_connections when you add workers).
"""

from __future__ import annotations

import logging
import os

# gunicorn configures only its own loggers - without this, the application's
# INFO logs would be dropped and `docker compose logs fkoora-api` would show
# access lines only.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

from .api import create_app

log = logging.getLogger("scraper.wsgi")

app = create_app(os.environ.get("FOOTBALL_DB_URL") or None)

log.info("read-only API app ready (scraper worker runs separately: "
         "`python -m scraper.cli worker`)")
