"""
livecache.py - a tiny time-to-live file cache shared by the live data adapters.

Why it exists: EDGAR fundamentals change only when a new filing appears (quarterly),
and prices only need 15-minute freshness for a holdings health check (this is not day
trading). Caching keeps us well inside EDGAR's ~10 req/s fair-use band and Finnhub's
60 calls/min free tier, and it makes the app fast and offline-friendly for a demo.

Pure and framework-free (no Streamlit): the whole module moves into the FastAPI
service verbatim at migration time. It stores plain JSON on disk under data/live_cache/.
"""
from __future__ import annotations

import json
import os
import time

CACHE_DIR = "data/live_cache"


def _path(namespace: str, key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key.upper())
    return os.path.join(CACHE_DIR, namespace, f"{safe}.json")


def load(namespace: str, key: str, ttl_seconds: float):
    """
    Return (value, fetched_at_epoch) if a fresh cache entry exists, else None.
    ttl_seconds <= 0 means "never expires" (used for slow-changing reference data).
    """
    path = _path(namespace, key)
    try:
        with open(path) as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None
    fetched_at = blob.get("_fetched_at", 0)
    if ttl_seconds and ttl_seconds > 0 and (time.time() - fetched_at) > ttl_seconds:
        return None
    return blob.get("value"), fetched_at


def store(namespace: str, key: str, value) -> float:
    """Write value to the cache, returning the fetch timestamp we recorded."""
    path = _path(namespace, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fetched_at = time.time()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"_fetched_at": fetched_at, "value": value}, fh)
    os.replace(tmp, path)          # atomic, so a crash mid-write never corrupts the cache
    return fetched_at


def cached(namespace: str, key: str, ttl_seconds: float, fetch_fn):
    """
    Return (value, fetched_at, from_cache). On a miss, call fetch_fn() and store it.
    If fetch_fn raises but a STALE entry exists, serve the stale entry rather than
    failing the request (a slightly old score beats a broken page).
    """
    hit = load(namespace, key, ttl_seconds)
    if hit is not None:
        value, fetched_at = hit
        return value, fetched_at, True
    try:
        value = fetch_fn()
    except Exception:
        stale = load(namespace, key, ttl_seconds=0)   # ignore TTL: any entry will do
        if stale is not None:
            return stale[0], stale[1], True
        raise
    fetched_at = store(namespace, key, value)
    return value, fetched_at, False
