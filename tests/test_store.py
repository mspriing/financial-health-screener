"""
Tests for store.py: the swappable history store (Postgres in production, JSON file
everywhere else) plus the spring_score field newly remembered in snapshots.

No live database anywhere: the Postgres backend is exercised against a fake connection
that records SQL and plays back rows, which pins the semantics (latest per ticker,
unscored rows skipped, fallback on failure) without network or credentials.

Run:  python3 tests/test_store.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history
import store
from history import snapshot_of

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


def row(ticker, health="Healthy", z=4.0, f=8, m=-2.5, m_flag=False, spring=72.0,
        source="live"):
    return {"ticker": ticker, "name": ticker, "sector": "Technology", "z": z,
            "zone": "Safe", "f_score": f, "m_score": m, "m_flag": m_flag,
            "source": source, "spring_score": spring, "spring_tier": "Solid",
            "verdict": {"health": health, "integrity": "Clean"}}


# ----------------------------------------------------------------------------
# A minimal fake of the psycopg surface store.py touches.
# ----------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        self.db.executed.append((sql.strip(), params))
        if params is not None and "INSERT INTO score_history" in sql:
            ticker, snapshot, checked_at = params
            self.db.rows.append((ticker, snapshot, checked_at))

    def fetchall(self):
        # Play back what _SQL_LATEST would return: latest row per ticker,
        # snapshot as a dict the way psycopg deserializes jsonb.
        latest = {}
        for i, (ticker, snapshot, checked_at) in enumerate(self.db.rows):
            prev = latest.get(ticker)
            if prev is None or (checked_at, i) >= (prev[2], prev[3]):
                latest[ticker] = (ticker, snapshot, checked_at, i)
        return [(t, json.loads(s) if isinstance(s, str) else s, c)
                for t, s, c, _ in latest.values()]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDB:
    def __init__(self):
        self.rows = []
        self.executed = []


print("SNAPSHOT NOW REMEMBERS THE SPRING SCORE")
snap = snapshot_of(row("AAPL", spring=81.5))
check("spring_score is stored", snap["spring_score"] == 81.5)
check("spring_score is a tracked field", "spring_score" in history._TRACKED)
check("missing spring stores None, not a crash",
      snapshot_of({"ticker": "X", "verdict": {}})["spring_score"] is None)
check("diff semantics unchanged by the new field: same health means unchanged",
      history.diff_holding(row("A"), dict(snapshot_of(row("A")), checked_at=1.0))
      ["direction"] == "unchanged")

print("BACKEND SELECTION")
os.environ.pop("DATABASE_URL", None)
check("no DATABASE_URL means file backend", store.backend_name() == "file")
os.environ["DATABASE_URL"] = "postgresql://fake"
check("DATABASE_URL set means postgres backend", store.backend_name() == "postgres")

print("POSTGRES SAVE: APPEND-ONLY, UNSCORED SKIPPED")
db = FakeDB()
store._connect = lambda url: FakeConn(db)   # inject the fake for the rest of the run

summary = store.save_run([row("AAPL"), row("MMM", health="Watch"),
                          row("ZZZ", source="unscored")], now=1000.0)
check("two scored rows written", summary["written"] == 2)
check("unscored row was skipped", all(t != "ZZZ" for t, _, _ in db.rows))
check("append-only: table has exactly the rows written", len(db.rows) == 2)
check("ticker is uppercased on write",
      sorted(t for t, _, _ in db.rows) == ["AAPL", "MMM"])
check("snapshot is stored as json text with spring inside",
      json.loads(db.rows[0][1])["spring_score"] == 72.0)
check("schema creation ran before the insert",
      any("CREATE TABLE" in sql for sql, _ in db.executed))

print("POSTGRES LOAD: LATEST PER TICKER, FILE-STORE SHAPE")
store.save_run([row("AAPL", health="Watch", spring=55.0)], now=2000.0)
loaded = store.load_history()
check("load returns the file-store shape", set(loaded) >= {"schema_version", "tickers"})
check("latest AAPL run wins", loaded["tickers"]["AAPL"]["health"] == "Watch")
check("checked_at comes from the winning run",
      loaded["tickers"]["AAPL"]["checked_at"] == 2000.0)
check("older ticker still present", loaded["tickers"]["MMM"]["health"] == "Watch")
check("last_run_at is the newest run", loaded["last_run_at"] == 2000.0)
check("spring trail survives the round trip",
      loaded["tickers"]["AAPL"]["spring_score"] == 55.0)

print("THE DIFFER CANNOT TELL THE BACKENDS APART")
d = history.diff_portfolio([row("AAPL", health="Distressed")], loaded)
check("postgres-loaded history feeds the diff",
      d[0]["delta"]["direction"] == "deteriorated")
check("headline reads from the stored health",
      "Watch to Distressed" in d[0]["delta"]["headline"])

print("FALLBACK: A BROKEN DATABASE NEVER FAILS THE REQUEST")


def boom(url):
    raise ConnectionError("neon is down")


store._connect = boom
with tempfile.TemporaryDirectory() as td:
    fpath = os.path.join(td, "scores.json")
    real_load, real_save = history.load_history, history.save_run
    history.load_history = lambda path=fpath: real_load(fpath)
    history.save_run = lambda scored, path=fpath, now=None: real_save(scored, fpath, now)
    try:
        s = store.save_run([row("KO")], now=3000.0)
        check("save fell back to the file store", s["tickers"]["KO"]["checked_at"] == 3000.0)
        loaded = store.load_history()
        check("load fell back to the file store", "KO" in loaded["tickers"])
    finally:
        history.load_history, history.save_run = real_load, real_save

print("FILE BACKEND IS THE DEFAULT PATH, UNCHANGED")
os.environ.pop("DATABASE_URL", None)
with tempfile.TemporaryDirectory() as td:
    fpath = os.path.join(td, "scores.json")
    real_load, real_save = history.load_history, history.save_run
    history.load_history = lambda path=fpath: real_load(fpath)
    history.save_run = lambda scored, path=fpath, now=None: real_save(scored, fpath, now)
    try:
        store.save_run([row("PG", health="Healthy")], now=4000.0)
        loaded = store.load_history()
        check("no DATABASE_URL routes straight to the file",
              loaded["tickers"]["PG"]["health"] == "Healthy")
        check("file snapshot carries spring_score too",
              loaded["tickers"]["PG"]["spring_score"] == 72.0)
    finally:
        history.load_history, history.save_run = real_load, real_save

print(f"\n{passed} checks passed.")
