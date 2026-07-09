"""
Live data-layer tests: EDGAR fundamentals + price layer + the fetch_live orchestrator.

Verifies the new pipeline produces the exact payload contract the models and the app
already depend on, that EDGAR is the fundamentals source when it can serve a ticker,
that provenance is stamped on every payload, and that the per-ticker cache works.

These hit the network (SEC EDGAR is free and needs no key; the price layer falls back
to yfinance when FINNHUB_API_KEY is not set). If the network is unavailable the live
checks are skipped with a clear message rather than failing.

Run:  python3 tests/test_data_layer.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edgar
import data
from data import LINE_ITEMS, run_models

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


def _online():
    try:
        return edgar.resolve_cik("AAPL") is not None
    except Exception as e:  # noqa: BLE001
        print(f"  (network unavailable: {e})")
        return False


def _shape_ok(payload):
    """The payload contract every caller relies on."""
    if set(payload) < {"meta", "market_value_equity", "curr", "prior"}:
        return False
    for side in ("curr", "prior"):
        for k in LINE_ITEMS:
            if k not in payload[side]:
                return False
    return True


ONLINE = _online()

if not ONLINE:
    print("\nSKIPPED live checks (no network). Contract-only checks still run below.")
else:
    print("EDGAR ticker -> CIK resolution")
    cik = edgar.resolve_cik("AAPL")
    check("AAPL resolves to a 10-digit CIK", isinstance(cik, str) and len(cik) == 10)
    check("unknown ticker resolves to None", edgar.resolve_cik("NOTATICKER123") is None)

    print("EDGAR fundamentals payload (AAPL)")
    p = edgar.fetch_edgar("AAPL")
    check("returns a payload", p is not None)
    check("payload shape matches the models' contract", _shape_ok(p))
    check("fundamentals source is EDGAR", "EDGAR" in p["meta"]["fundamentals_source"])
    check("as-of is a filing period date", bool(p["meta"]["fundamentals_as_of"]))
    check("total_assets present in curr and prior",
          p["curr"]["total_assets"] and p["prior"]["total_assets"])
    check("sales present in curr", bool(p["curr"]["sales"]))

    print("EDGAR payload scores through the existing models unchanged")
    altman, piotroski, beneish, verdict, notes = run_models(p)
    check("verdict health is a real state",
          verdict["health"] in ("Healthy", "Watch", "Distressed"))
    check("at least Piotroski or Altman computed",
          altman is not None or piotroski is not None)
    print(f"    AAPL -> health={verdict['health']} integrity={verdict['integrity']} "
          f"(as of {p['meta']['fundamentals_as_of']})")

    print("fetch_live orchestrator (AAPL): EDGAR fundamentals + price layer + provenance")
    live = data.fetch_live("AAPL")
    check("orchestrator returns valid shape", _shape_ok(live))
    check("fundamentals came from EDGAR", "EDGAR" in live["meta"]["source"])
    check("provenance block present", "provenance" in live["meta"])
    check("fundamentals provenance recorded",
          live["meta"]["provenance"]["fundamentals"]["source"] is not None)
    prov_price = live["meta"]["provenance"]["price"]
    check("price layer filled market value of equity (Finnhub or yfinance fallback)",
          live["market_value_equity"] > 0 and prov_price["source"] is not None)
    print(f"    price source={prov_price['source']} "
          f"MVE=${live['market_value_equity']:,.0f}")

    print("per-ticker EDGAR cache is populated after the first pull")
    hit = __import__("livecache").load("edgar", "facts_AAPL", edgar.FACTS_TTL)
    check("AAPL companyfacts is cached", hit is not None)

    print("a second company also works end to end (KO)")
    ko = data.fetch_live("KO")
    check("KO returns valid shape", _shape_ok(ko))
    _a, _p, _b, ko_v, _n = run_models(ko)
    check("KO produces a health verdict",
          ko_v["health"] in ("Healthy", "Watch", "Distressed"))
    print(f"    KO -> health={ko_v['health']} integrity={ko_v['integrity']} "
          f"src={ko['meta']['source']}")

    print("fallback path: a non-company symbol still raises the friendly message")
    try:
        data.fetch_live("SPY")
        check("SPY should not silently succeed", False)
    except RuntimeError as e:
        check("SPY raises a plain-English 'not a single company' error",
              "company" in str(e).lower() or "fund" in str(e).lower())

print("\nCONTRACT (offline) checks")
sample = data.PRESETS["Bluechip Industries (sample: healthy)"]
check("presets still score without touching the network", run_models(sample) is not None)
check("blank_payload still has the full line-item set",
      all(k in data.blank_payload()["curr"] for k in LINE_ITEMS))

print(f"\n{passed} checks passed.")
