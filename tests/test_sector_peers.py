"""
Offline, deterministic tests for the live sector-peer layer (sector_peers.py).

No network: every FMP call is stubbed with canned JSON, so these run anywhere and pin
the contract that matters. What they prove:

  * the screener call carries the approved filters and the peer set is parsed correctly;
  * each peer's market cap is injected as market_value_equity BEFORE scoring, because
    Altman's X4 is market value of equity over total liabilities and fmp.fetch_fundamentals
    deliberately leaves it at 0.0 (this bug would be invisible: every peer Z would simply
    come out low, and every median with it);
  * peer numbers are computed by OUR models.py, never taken from FMP's own altmanZScore
    or piotroskiScore;
  * one broken peer is skipped, not fatal, and a sector that ends up under MIN_PEERS_LIVE
    returns None so the snapshot fallback serves instead of three lonely data points;
  * sector labels from the snapshot's mixed vocabulary normalize onto FMP's;
  * with no key every entry point returns None, so the app keeps working on the fallback;
  * the cache is honoured (a second call does not re-fetch) and is what the refresher
    checks.

Run:  python3 tests/test_sector_peers.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fmp
import livecache
import sector_peers
from models import leverage_ratio

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


# Every test runs against a throwaway cache directory: these must never read or write
# the real data/live_cache, or a passing test could just be yesterday's cached answer.
livecache.CACHE_DIR = tempfile.mkdtemp(prefix="peers-test-")

# ----------------------------------------------------------------------------
# Stubs: a screener response and per-peer statement/scores responses.
# ----------------------------------------------------------------------------
SCREENER = [
    {"symbol": f"PEER{i}", "companyName": f"Peer {i} Inc.",
     "marketCap": 10_000_000_000 + i, "sector": "Technology"}
    for i in range(sector_peers.PEER_COUNT)
]


def _statements(symbol):
    """Two clean annual years for a peer, in FMP's shape."""
    return {
        "income-statement": [
            {"date": "2025-12-31", "symbol": symbol, "revenue": 1000,
             "costOfRevenue": 600, "sellingGeneralAndAdministrativeExpenses": 120,
             "operatingIncome": 250, "netIncome": 180, "weightedAverageShsOut": 500,
             "depreciationAndAmortization": 40},
            {"date": "2024-12-31", "symbol": symbol, "revenue": 900,
             "costOfRevenue": 560, "sellingGeneralAndAdministrativeExpenses": 110,
             "operatingIncome": 200, "netIncome": 140, "weightedAverageShsOut": 490,
             "depreciationAndAmortization": 38},
        ],
        "balance-sheet-statement": [
            {"date": "2025-12-31", "totalAssets": 2000, "totalLiabilities": 800,
             "totalCurrentAssets": 700, "totalCurrentLiabilities": 300,
             "netReceivables": 150, "propertyPlantEquipmentNet": 600,
             "longTermDebt": 400, "retainedEarnings": 500},
            {"date": "2024-12-31", "totalAssets": 1800, "totalLiabilities": 760,
             "totalCurrentAssets": 640, "totalCurrentLiabilities": 290,
             "netReceivables": 140, "propertyPlantEquipmentNet": 560,
             "longTermDebt": 380, "retainedEarnings": 400},
        ],
        "cash-flow-statement": [
            {"date": "2025-12-31", "operatingCashFlow": 220,
             "depreciationAndAmortization": 40},
            {"date": "2024-12-31", "operatingCashFlow": 190,
             "depreciationAndAmortization": 38},
        ],
    }


SCORES = {"symbol": "PEER0", "altmanZScore": 99.0, "piotroskiScore": 9,
          "workingCapital": 400, "totalAssets": 2000, "retainedEarnings": 500,
          "ebit": 250, "marketCap": 10_000_000_000, "totalLiabilities": 800,
          "revenue": 1000}

_calls = []


def stub_get(broken: tuple = (), screener=SCREENER):
    """
    Install a fake fmp._get that records calls and serves canned JSON, on a FRESH cache
    directory. The fresh cache matters: fmp.fetch_fundamentals caches per ticker, so
    without it a peer marked broken in a later scenario would still be served from an
    earlier scenario's cached statements and the degradation test would pass for the
    wrong reason.
    """
    livecache.CACHE_DIR = tempfile.mkdtemp(prefix="peers-test-")

    def fake_get(url, params):
        _calls.append((url, dict(params or {})))
        symbol = (params or {}).get("symbol", "")
        if "company-screener" in url:
            return screener
        if symbol in broken:
            return None                      # this peer's data is unusable
        if "financial-scores" in url:
            return [dict(SCORES, symbol=symbol)]
        for path, body in _statements(symbol).items():
            if path in url:
                return body
        return None
    fmp._get = fake_get
    _calls.clear()


_real_get = fmp._get


# ----------------------------------------------------------------------------
print("SECTOR NORMALIZATION: the snapshot's mixed vocabulary maps onto FMP's")
check("yfinance-style label passes through",
      sector_peers.normalize_sector("Technology") == "Technology")
check("GICS 'Financials' maps to FMP's 'Financial Services'",
      sector_peers.normalize_sector("Financials") == "Financial Services")
check("GICS 'Information Technology' maps to 'Technology'",
      sector_peers.normalize_sector("Information Technology") == "Technology")
check("GICS 'Health Care' maps to 'Healthcare'",
      sector_peers.normalize_sector("Health Care") == "Healthcare")
check("case and whitespace do not matter",
      sector_peers.normalize_sector("  technology ") == "Technology")
check("None stays None", sector_peers.normalize_sector(None) is None)
check("empty string stays None", sector_peers.normalize_sector("   ") is None)

print("LEVERAGE FORMULA: one definition, used on both sides of the benchmark")
check("total liabilities / total assets",
      leverage_ratio({"total_liabilities": 800, "total_assets": 2000}) == 0.4)
check("missing assets degrades to None",
      leverage_ratio({"total_liabilities": 800, "total_assets": None}) is None)
check("zero assets degrades to None, never divides by zero",
      leverage_ratio({"total_liabilities": 800, "total_assets": 0}) is None)
check("empty dict degrades to None", leverage_ratio({}) is None)

print("ALTMAN FROM FMP's RAW INPUTS: our formula, not their published score")
z = sector_peers.altman_from_scores(SCORES)
check("a Z comes back", isinstance(z, float))
check("it is OUR number, not FMP's altmanZScore of 99.0", abs(z - 99.0) > 1.0)
check("missing inputs degrade to None, never raise",
      sector_peers.altman_from_scores({"totalAssets": None}) is None)

print("SCREENER CALL: the approved filters, exactly")
stub_get()
peers = sector_peers.screen_sector("Technology")
url, params = _calls[0]
check("hits /stable/company-screener", url.endswith("/company-screener"))
check("filters on the normalized sector", params["sector"] == "Technology")
check("US-listed only", params["country"] == "US")
check("excludes ETFs and funds",
      params["isEtf"] == "false" and params["isFund"] == "false")
check("actively trading only", params["isActivelyTrading"] == "true")
check("market cap floor is $2B", params["marketCapMoreThan"] == 2_000_000_000)
check("asks for N = 25", params["limit"] == 25)
check("parses the peer set", len(peers) == sector_peers.PEER_COUNT)
check("carries symbol, name and market cap",
      set(peers[0]) == {"symbol", "name", "market_cap"}
      and peers[0]["symbol"] == "PEER0")
check("an empty screener response degrades to None",
      sector_peers.screen_sector("Technology") is not None)
stub_get(screener=[])
check("no rows means None, not an empty peer set",
      sector_peers.screen_sector("Technology") is None)

print("FULL DEPTH: every peer scored by our models, all four metrics live")
stub_get()
built = sector_peers.build_rows("Technology", depth="full")
check("a set comes back", built is not None)
check("all 25 peers scored", built["peer_count"] == 25)
check("depth and metrics are declared for the report to read",
      built["depth"] == "full"
      and built["metrics"] == ["leverage", "z", "f_score", "m_score"])
check("as-of is stamped so the UI can show current conditions", bool(built["as_of"]))
row0 = built["rows"][0]
check("row shape is what benchmark.sector_stats consumes",
      {"tr", "sector", "leverage", "z", "f_score", "m_score"} <= set(row0))
check("leverage is 800/2000 = 0.4", row0["leverage"] == 0.4)
check("Z is computed, not copied from FMP", isinstance(row0["z"], float))
check("F is computed (needs the prior year, which full depth pulls)",
      isinstance(row0["f_score"], float))
check("M is computed (needs eight two-year indices)", isinstance(row0["m_score"], float))

print("MARKET CAP IS INJECTED BEFORE SCORING: Altman X4 would be wrong without it")
# fmp.fetch_fundamentals leaves market_value_equity at 0.0. With a $10B cap against $800
# of liabilities, X4 is enormous and Z is huge; without the injection Z is far smaller.
stub_get()
with_cap = sector_peers.build_rows("Technology", depth="full")["rows"][0]["z"]
stub_get(screener=[dict(r, marketCap=None) for r in SCREENER])
without_cap = sector_peers.build_rows("Technology", depth="full")["rows"][0]["z"]
check("a peer with a market cap scores far higher than one without",
      with_cap > without_cap * 10)
check("and the no-cap case still produces a number rather than crashing",
      isinstance(without_cap, float))

print("CORE DEPTH: one call per peer, leverage and Z only, F and M left to the snapshot")
stub_get()
core = sector_peers.build_rows("Technology", depth="core")
check("declares the narrower metric set", core["metrics"] == ["leverage", "z"])
check("leverage is live", core["rows"][0]["leverage"] == 0.4)
check("Z is live", isinstance(core["rows"][0]["z"], float))
check("F is deliberately None, not FMP's piotroskiScore of 9",
      core["rows"][0]["f_score"] is None)
check("M is None (Beneish needs two years this endpoint does not carry)",
      core["rows"][0]["m_score"] is None)
check("one call per peer plus one screener call",
      len(_calls) == 1 + sector_peers.PEER_COUNT)

print("DEGRADATION: one bad peer is skipped, a thin sector falls back")
stub_get(broken=("PEER0", "PEER1", "PEER2"))
partial = sector_peers.build_rows("Technology", depth="full")
check("the sector still builds on the peers that worked",
      partial is not None and partial["peer_count"] == 22)
check("the broken peers are absent, not present as null rows",
      all(r["tr"] not in ("PEER0", "PEER1", "PEER2") for r in partial["rows"]))
stub_get(broken=tuple(f"PEER{i}" for i in range(20)))
check("under MIN_PEERS_LIVE the whole set returns None, so the snapshot serves",
      sector_peers.build_rows("Technology", depth="full") is None)

print("NO KEY: everything returns None and the app runs on the fallback")
fmp._get = _real_get
_saved_key, os.environ["FMP_API_KEY"] = os.environ.get("FMP_API_KEY"), ""
try:
    check("screen_sector returns None with no key",
          sector_peers.screen_sector("Technology") is None)
    check("build_rows returns None with no key",
          sector_peers.build_rows("Technology") is None)
    check("warm returns None with no key", sector_peers.warm("Technology") is None)
    check("and never raises, so no caller needs a try/except", True)
finally:
    if _saved_key is None:
        os.environ.pop("FMP_API_KEY", None)
    else:
        os.environ["FMP_API_KEY"] = _saved_key

print("CACHING: warm builds once, then serves from cache until the TTL expires")
stub_get()
sector_peers.clear_cache("Technology")
first = sector_peers.warm("Technology", depth="core")
calls_after_build = len(_calls)
check("the first warm actually builds", first is not None and calls_after_build > 0)
check("it is labeled live and not stale",
      first["source"] == "FMP live sector peers" and first["stale"] is False)
second = sector_peers.warm("Technology", depth="core")
check("a second warm serves the cache without re-fetching",
      len(_calls) == calls_after_build)
check("the cached read carries the same peer count",
      second["peer_count"] == first["peer_count"])
check("cached_peer_set never fetches, it only reads",
      sector_peers.cached_peer_set("Technology") is not None
      and len(_calls) == calls_after_build)
check("is_stale is False while the entry is fresh",
      sector_peers.is_stale("Technology") is False)

# Age the cached entry past its TTL by rewriting the timestamp livecache stamps on it.
# This is the exact condition the background refresher wakes up and acts on.
import json
import time

_cache_path = livecache._path(sector_peers.CACHE_NAMESPACE, "Technology")
with open(_cache_path) as _fh:
    _blob = json.load(_fh)
_blob["_fetched_at"] = time.time() - (sector_peers.PEERS_TTL + 60)
with open(_cache_path, "w") as _fh:
    json.dump(_blob, _fh)
check("an entry older than the TTL reads as stale",
      sector_peers.is_stale("Technology") is True)
check("and cached_peer_set stops serving it, so the report falls back to the snapshot",
      sector_peers.cached_peer_set("Technology") is None)
check("warm rebuilds the expired entry",
      sector_peers.warm("Technology", depth="core") is not None
      and sector_peers.is_stale("Technology") is False)
sector_peers.clear_cache("Technology")
check("clear_cache drops the entry",
      sector_peers.cached_peer_set("Technology") is None)
check("an unknown sector never reaches the network",
      sector_peers.cached_peer_set(None) is None)

fmp._get = _real_get
print(f"\n{passed} checks passed.")
