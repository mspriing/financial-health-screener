"""
store.py - where score history lives: Postgres in production, the JSON file everywhere else.

history.py deliberately split the deterioration logic in two: pure diff functions, and a
small JSON store. This module swaps the store without touching the diff, exactly the swap
the history.py docstring promised. server.py imports load_history/save_run from here and
nothing else changes.

Backend selection is one rule: if DATABASE_URL is set (Render production, pointed at a
free Neon Postgres), use Postgres; otherwise use the existing JSON file (local dev, tests,
demos). If the database is configured but unreachable, we log a warning and fall back to
the file for that call: history is an enhancement, and a portfolio score must never fail
because the memory layer hiccupped. Same philosophy history.py applies to a corrupt file.

The Postgres table is append-only, one row per (ticker, run):

    score_history(id, ticker, snapshot jsonb, checked_at double precision)

The diff only needs the latest row per ticker (load_history reads DISTINCT ON), but the
full trail is what the weekly deterioration email and a future per-ticker history view
will read. Storing the trail now costs nothing and starts the clock.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import List, Optional

import history as _file

SCHEMA_VERSION = _file.SCHEMA_VERSION

logger = logging.getLogger("store")

_SQL_CREATE = """
CREATE TABLE IF NOT EXISTS score_history (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    checked_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_score_history_ticker
    ON score_history (ticker, checked_at DESC);
"""

# Latest run per ticker, which is all the differ ever needs.
_SQL_LATEST = """
SELECT DISTINCT ON (ticker) ticker, snapshot, checked_at
FROM score_history
ORDER BY ticker, checked_at DESC, id DESC
"""

_SQL_INSERT = """
INSERT INTO score_history (ticker, snapshot, checked_at)
VALUES (%s, %s::jsonb, %s)
"""


def backend_name() -> str:
    """Which store this process is configured to use. Reported by /health so a deploy
    can be verified without waiting for a portfolio run."""
    return "postgres" if os.environ.get("DATABASE_URL") else "file"


def _connect(url: str):
    """Open a Postgres connection. Imported lazily so local runs without psycopg
    installed still work on the file backend."""
    import psycopg
    return psycopg.connect(url, connect_timeout=5)


# ----------------------------------------------------------------------------
# Postgres operations, written against a plain connection so tests can pass a fake.
# ----------------------------------------------------------------------------
def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_SQL_CREATE)


def _pg_load(conn) -> dict:
    """Read the latest snapshot per ticker into the exact dict shape the file store
    returns, so diff_portfolio cannot tell the backends apart."""
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(_SQL_LATEST)
        rows = cur.fetchall()

    tickers = {}
    last_run_at = None
    for ticker, snapshot, checked_at in rows:
        if isinstance(snapshot, str):          # driver returned raw json text
            snapshot = json.loads(snapshot)
        entry = dict(snapshot)
        entry["checked_at"] = checked_at
        tickers[str(ticker).upper()] = entry
        if last_run_at is None or checked_at > last_run_at:
            last_run_at = checked_at

    out = {"schema_version": SCHEMA_VERSION, "tickers": tickers}
    if last_run_at is not None:
        out["last_run_at"] = last_run_at
    return out


def _pg_save(conn, scored: List[dict], now: float) -> dict:
    """Append this run's snapshots. Unscored holdings are skipped for the same reason
    the file store skips them: nulls must not overwrite a good prior reading."""
    _ensure_schema(conn)
    written = 0
    with conn.cursor() as cur:
        for row in scored:
            if row.get("source") == "unscored":
                continue
            entry = _file.snapshot_of(row)
            cur.execute(_SQL_INSERT,
                        (str(row.get("ticker", "")).upper(), json.dumps(entry), now))
            written += 1
    return {"schema_version": SCHEMA_VERSION, "last_run_at": now, "written": written}


# ----------------------------------------------------------------------------
# The public interface server.py uses. Same names and semantics as history.py's store.
# ----------------------------------------------------------------------------
def load_history() -> dict:
    url = os.environ.get("DATABASE_URL")
    if url:
        try:
            with _connect(url) as conn:
                return _pg_load(conn)
        except Exception as e:
            logger.warning("history load fell back to file store: %s", e)
    return _file.load_history()


def save_run(scored: List[dict], now: Optional[float] = None) -> dict:
    """Remember this run. Returns a small summary dict; no caller reads it, it exists
    so tests can pin what was written."""
    now = time.time() if now is None else now
    url = os.environ.get("DATABASE_URL")
    if url:
        try:
            with _connect(url) as conn:
                return _pg_save(conn, scored, now)
        except Exception as e:
            logger.warning("history save fell back to file store: %s", e)
    return _file.save_run(scored, now=now)
