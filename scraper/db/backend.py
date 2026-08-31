"""PostgreSQL connection management for the scraper, API server and web view.

The database URL (DSN) is resolved from (first match wins):

    1. an explicit argument (``Database("postgresql://...")``,
       ``--db postgresql://...``)
    2. the ``FOOTBALL_DB_URL`` environment variable
    3. ``config.DEFAULT_DB_URL``

Accepted forms are anything psycopg understands, e.g.::

    postgresql://user:pass@host:5432/dbname
    postgresql://user:pass@host/dbname?sslmode=require
    postgres://postgres@localhost/football

Connections always use ``dict_row`` so every row behaves like a mapping
(``row["column"]``), matching what the rest of the codebase expects.

For the API server, :func:`connection` returns a pooled connection when
``psycopg_pool`` is installed (one shared pool per DSN); otherwise it falls
back to a fresh connection per call. The context manager commits on success
and rolls back on error, so request-scoped usage is exception-safe.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .. import config

log = logging.getLogger("scraper.db")

DSN_ENV = "FOOTBALL_DB_URL"


# ---------------------------------------------------------------------------
# DSN resolution
# ---------------------------------------------------------------------------
def resolve_dsn(explicit: Optional[str] = None) -> str:
    """Resolve the database URL: argument > FOOTBALL_DB_URL > config default."""
    dsn = (explicit or "").strip() or (os.environ.get(DSN_ENV) or "").strip()
    if not dsn:
        dsn = config.DEFAULT_DB_URL
    return dsn


def display_dsn(dsn: str) -> str:
    """DSN with the password masked - safe for logs and /api/health."""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return dsn
    if parts.password is None:
        return dsn
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = parts.username or ""
    if netloc:
        netloc += ":***@"
    elif parts.password:
        netloc = ":***@"
    return f"{parts.scheme}://{netloc}{host}{parts.path}"


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------
def connect(dsn: Optional[str] = None) -> psycopg.Connection:
    """Open a new connection (dict rows, explicit transactions)."""
    resolved = resolve_dsn(dsn)
    conn = psycopg.connect(resolved, row_factory=dict_row)
    return conn


_pools: dict[str, Any] = {}
_pools_lock = threading.Lock()

# Pool sizing is env-tunable so heavier deployments can raise it without code
# changes (each API process holds ONE pool; total connections across all
# processes must stay within PostgreSQL's max_connections).
DB_POOL_MIN = max(1, int(os.environ.get("DB_POOL_MIN", "1")))
DB_POOL_MAX = max(1, int(os.environ.get("DB_POOL_MAX", "8")))


def _get_pool(dsn: str):
    """One shared psycopg_pool.ConnectionPool per DSN (created lazily)."""
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is not None:
            return pool
        try:
            from psycopg_pool import ConnectionPool
        except ImportError:
            return None
        pool = ConnectionPool(
            dsn, min_size=DB_POOL_MIN, max_size=DB_POOL_MAX, open=True, timeout=15,
            max_lifetime=60 * 60,
            kwargs={"row_factory": dict_row},   # same dict rows as direct connects
        )
        _pools[dsn] = pool
        return pool


@contextmanager
def connection(dsn: Optional[str] = None, *, pooled: bool = True) -> Iterator[psycopg.Connection]:
    """Yield a database connection; commit on success, roll back on error.

    pooled=True (API server / web view): the connection comes from the shared
    pool and is returned to it on exit. Without psycopg_pool installed - or
    with pooled=False - a fresh connection is opened and closed instead.
    """
    resolved = resolve_dsn(dsn)
    if pooled:
        pool = _get_pool(resolved)
        if pool is not None:
            with pool.connection() as conn:
                yield conn
            return
    conn = connect(resolved)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# script execution (schema.sql)
# ---------------------------------------------------------------------------
# Schema statements run with a short lock timeout: the API/worker apply the
# idempotent schema on every start, and waiting forever behind a long scrape
# transaction (or another process mid-apply) would hang startup. A statement
# that cannot get its lock is skipped and retried on the next boot - every
# statement is CREATE ... IF NOT EXISTS / ALTER ... IF NOT EXISTS, so a
# partial apply is always safe.
SCHEMA_LOCK_TIMEOUT_SEC = int(os.environ.get("SCHEMA_LOCK_TIMEOUT_SEC", "5"))
_SKIP_LOCK_ERRORS = ("55P03", "57014", "40P01")   # lock_timeout/cancel/deadlock


def run_script(conn: psycopg.Connection, script: str) -> None:
    """Execute a multi-statement SQL script (schema.sql), statement by
    statement.

    ``--`` comments are stripped first (they can contain ';'), then the script
    is split on ';' - the schema contains no semicolons inside string
    literals, so a plain split is safe once comments are gone. Each statement
    runs in its own transaction with a short lock timeout; statements that
    time out on a lock are skipped (best effort, retried next start) rather
    than blocking startup.
    """
    stripped = []
    for line in script.splitlines():
        idx = line.find("--")
        stripped.append(line if idx < 0 else line[:idx])
    cleaned = "\n".join(stripped)
    statements = [s.strip() for s in cleaned.split(";")]
    skipped = 0
    for stmt in statements:
        if not stmt:
            continue
        try:
            with conn.transaction():
                conn.execute(f"SET LOCAL lock_timeout = "
                             f"'{SCHEMA_LOCK_TIMEOUT_SEC}s'")
                conn.execute(stmt)
        except psycopg.Error as exc:
            # only lock-timeout / statement-cancel / deadlock errors are
            # skippable - everything else is a real failure and propagates
            code = getattr(exc, "sqlstate", None)
            if code in _SKIP_LOCK_ERRORS:
                skipped += 1
                log.warning("schema statement skipped (lock timeout): %.60s", stmt)
            else:
                raise
    if not conn.autocommit and conn.info.transaction_status != \
            psycopg.pq.TransactionStatus.IDLE:
        conn.commit()
    if skipped:
        log.warning("schema apply: %d statement(s) skipped on lock timeouts - "
                    "they will apply on the next start", skipped)
