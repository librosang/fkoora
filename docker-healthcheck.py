#!/usr/bin/env python3
"""Role-aware container healthcheck for the shared backend image.

The same image runs as several SERVICE_ROLEs (api / worker / all / migrate)
but only the API ever listens on :9000 - a plain HTTP probe would report
every worker container as unhealthy forever, so the probe dispatches:

  api | all    deep HTTP check: GET /api/health (opens a real DB
               connection, reports row counts + cache/live/sse stats).
               200 = healthy, anything else (or a connect error) = not.

  worker      process check: PID 1 must be alive AND be the scraper
               worker (the entrypoint `exec`s it, so PID 1 *is* the
               worker loop). A dead PID 1 means Docker already replaced
               the container; a wrong PID 1 cmdline means the entrypoint
               dispatched to the wrong process.

  migrate     one-shot role: containers exit within seconds, the
               healthcheck effectively never runs - same process check
               as worker if it ever does.

Exit code 0 = healthy, 1 = not (the Docker HEALTHCHECK contract).
"""
import os
import sys
import urllib.request

ROLE = os.environ.get("SERVICE_ROLE", "api")


def http_check() -> int:
    url = os.environ.get("HEALTHCHECK_URL",
                         "http://127.0.0.1:9000/api/health")
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except Exception:                            # noqa: BLE001
        return 1


def process_check() -> int:
    try:
        with open("/proc/1/cmdline", "rb") as fh:
            cmdline = fh.read().decode("utf-8", "replace")
    except OSError:
        return 1
    return 0 if "scraper" in cmdline else 1


if ROLE in ("api", "all"):
    sys.exit(http_check())
sys.exit(process_check())
