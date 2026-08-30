"""HTTP client with rate limiting, retries and __NEXT_DATA__ extraction.

Both goal.com and kooora.com are Next.js apps that embed the full page
payload as JSON inside <script id="__NEXT_DATA__">. Fetching plain HTML and
parsing that script gives us clean structured data with zero browser
automation.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger("scraper.http")

# One shared session per process (connection pooling + cookie persistence)
_session: Optional[requests.Session] = None
_last_request_ts = 0.0
# The delay bookkeeping is shared by EVERY scraping thread (scheduler jobs,
# on-demand fetches, competition refresh workers). Without a lock two threads
# can both pass the "enough time passed" check simultaneously and fire a
# burst of near-simultaneous requests - exactly the pattern that triggers
# 429s on the provider. Serializing the pacing decision keeps the global
# request spacing intact no matter how many threads are scraping.
_pace_lock = threading.Lock()


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            }
        )
    return _session


def _polite_delay() -> None:
    """Block until enough time passed since the previous request.

    Thread-safe: the check-and-set on the shared timestamp happens under
    _pace_lock, so the minimum spacing between ANY two requests this
    process issues is always honoured, even across worker threads.
    """
    global _last_request_ts
    with _pace_lock:
        delay = config.RATE_LIMIT_DELAY + random.uniform(0, config.RATE_LIMIT_JITTER)
        elapsed = time.time() - _last_request_ts
        if elapsed < delay:
            time.sleep(delay - elapsed)
        _last_request_ts = time.time()


def fetch_html(url: str) -> str:
    """GET a URL with retries + rate limiting, return the response text."""
    session = _get_session()
    last_err: Optional[Exception] = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        _polite_delay()
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 404:
                # legit "not found" (e.g. empty day) - no point retrying
                raise FileNotFoundError(f"404 not found: {url}")
            if resp.status_code == 429:
                wait = config.RETRY_BACKOFF ** attempt * 5
                log.warning("429 rate-limited by %s - sleeping %.0fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except FileNotFoundError:
            raise
        except requests.RequestException as exc:
            last_err = exc
            wait = config.RETRY_BACKOFF ** attempt
            log.warning("attempt %d/%d failed for %s (%s) - retrying in %.1fs",
                        attempt, config.MAX_RETRIES, url, exc.__class__.__name__, wait)
            time.sleep(wait)

    raise ConnectionError(f"failed after {config.MAX_RETRIES} attempts: {url}") from last_err


def fetch_next_data(url: str) -> Dict[str, Any]:
    """Fetch a page and extract its __NEXT_DATA__ JSON payload."""
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        raise ValueError(f"no __NEXT_DATA__ found on {url}")
    return json.loads(script.string)


def fetch_next_data_quiet(url: str) -> Optional[Dict[str, Any]]:
    """Like fetch_next_data but returns None instead of raising."""
    try:
        return fetch_next_data(url)
    except (FileNotFoundError, ConnectionError, ValueError) as exc:
        log.debug("quiet fetch failed for %s: %s", url, exc)
        return None


def fetch_json(url: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """GET a JSON API endpoint with the same retry/rate-limit policy."""
    session = _get_session()
    last_err: Optional[Exception] = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        _polite_delay()
        try:
            resp = session.get(url, params=params, timeout=config.REQUEST_TIMEOUT,
                               headers={"Accept": "application/json"})
            if resp.status_code == 404:
                raise FileNotFoundError(f"404 not found: {url}")
            if resp.status_code == 429:
                wait = config.RETRY_BACKOFF ** attempt * 5
                log.warning("429 rate-limited by %s - sleeping %.0fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except FileNotFoundError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_err = exc
            wait = config.RETRY_BACKOFF ** attempt
            log.warning("attempt %d/%d failed for %s (%s) - retrying in %.1fs",
                        attempt, config.MAX_RETRIES, url, exc.__class__.__name__, wait)
            time.sleep(wait)
    raise ConnectionError(f"failed after {config.MAX_RETRIES} attempts: {url}") from last_err


def fetch_json_quiet(url: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """Like fetch_json but returns None instead of raising."""
    try:
        return fetch_json(url, params)
    except (FileNotFoundError, ConnectionError, ValueError) as exc:
        log.debug("quiet json fetch failed for %s: %s", url, exc)
        return None
