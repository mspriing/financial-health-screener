"""
Offline, deterministic tests for the Financial Modeling Prep adapter (fmp.py) and the
data-layer LAYERING it drives. No network: every FMP HTTP call is stubbed with canned
JSON, so these run anywhere and pin the parsing + fallback contract exactly.

What they prove:
  * FMP JSON maps to all 16 LINE_ITEMS correctly (normal company AND a bank).
  * price / market-cap parsing, incl. the marketCap-vs-price*shares cross-check.
  * daily-close history parses and feeds the existing annualized_volatility().
  * the analyst overlay parses when present and returns None when every premium call is
    declined (the free-tier reality), so it degrades honestly and lights up unchanged.
  * every fetcher returns None (never raises) with no key and on empty / error JSON.
  * data.fetch_live keeps EDGAR primary for fundamentals, uses FMP for price / volatility
    / sector, and threads FMP -> yfinance fallbacks, with provenance stamped on each.

Run:  python3 tests/test_fmp.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data
import fmp
import prices
import report
from data import LINE_ITEMS

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


# ----------------------------------------------------------------------------
# A stubbed _get: routes by URL substring to canned JSON, no network.
# ----------------------------------------------------------------------------
def make_get(routes: dict):
    """routes maps a URL substring -> the JSON value _get should return."""
    def fake_get(url, params):
        for frag, value in routes.items():
            if frag in url:
                return value
        return None
    return fake_get


INCOME = [
    {"date": "2023-12-31", "symbol": "TST", "revenue": 1000, "costOfRevenue": 600,
     "sellingGeneralAndAdministrativeExpenses": 120, "operatingIncome": 250,
     "netIncome": 180, "weightedAverageShsOut": 500, "depreciationAndAmortization": 40},
    {"date": "2022-12-31", "symbol": "TST", "revenue": 900, "costOfRevenue": 560,
     "sellingGeneralAndAdministrativeExpenses": 110, "operatingIncome": 200,
     "netIncome": 140, "weightedAverageShsOut": 490, "depreciationAndAmortization": 38},
]
BALANCE = [
    {"date": "2023-12-31", "netReceivables": 90, "totalCurrentAssets": 400,
     "totalCurrentLiabilities": 200, "propertyPlantEquipmentNet": 300,
     "totalAssets": 1200, "longTermDebt": 150, "totalLiabilities": 700,
     "retainedEarnings": 260, "totalStockholdersEquity": 500},
    {"date": "2022-12-31", "netReceivables": 85, "totalCurrentAssets": 360,
     "totalCurrentLiabilities": 190, "propertyPlantEquipmentNet": 280,
     "totalAssets": 1100, "longTermDebt": 160, "totalLiabilities": 660,
     "retainedEarnings": 210, "totalStockholdersEquity": 440},
]
CASHFLOW = [
    {"date": "2023-12-31", "operatingCashFlow": 210, "depreciationAndAmortization": 40},
    {"date": "2022-12-31", "operatingCashFlow": 175, "depreciationAndAmortization": 38},
]

_orig_get = fmp._get

# ----------------------------------------------------------------------------
print("_num coerces cleanly (None / '' / NaN -> None)")
check("plain number", fmp._num("12.5") == 12.5 and fmp._num(3) == 3.0)
check("None and empty string are absent", fmp._num(None) is None and fmp._num("") is None)
check("NaN is dropped", fmp._num(float("nan")) is None)
check("garbage string is absent", fmp._num("n/a") is None)

# ----------------------------------------------------------------------------
print("fundamentals: FMP JSON -> all 16 LINE_ITEMS")
fmp._get = make_get({"income-statement": INCOME, "balance-sheet": BALANCE,
                     "cash-flow": CASHFLOW})
f = fmp._pull_fundamentals("TST")
check("two-year pull succeeds", f is not None)
check("all line items present in curr and prior",
      all(k in f["curr"] for k in LINE_ITEMS) and all(k in f["prior"] for k in LINE_ITEMS))
expect = {"sales": 1000, "cogs": 600, "receivables": 90, "current_assets": 400,
          "current_liabilities": 200, "ppe": 300, "total_assets": 1200,
          "depreciation": 40, "sga": 120, "long_term_debt": 150, "net_income": 180,
          "cfo": 210, "retained_earnings": 260, "ebit": 250, "total_liabilities": 700,
          "shares": 500}
check("every mapped value is exact", all(f["curr"][k] == v for k, v in expect.items()))
check("curr/prior fiscal ends resolved newest-first",
      f["curr_end"] == "2023-12-31" and f["prior_end"] == "2022-12-31")
check("a normal company is not flagged financial", f["is_financial"] is False)

print("fundamentals: sga falls back to G&A + selling when the combined field is absent")
inc_split = [dict(INCOME[0], sellingGeneralAndAdministrativeExpenses=None,
                  generalAndAdministrativeExpenses=70, sellingAndMarketingExpenses=55),
             INCOME[1]]
fmp._get = make_get({"income-statement": inc_split, "balance-sheet": BALANCE,
                     "cash-flow": CASHFLOW})
check("sga = G&A + selling when combined missing",
      fmp._pull_fundamentals("TST")["curr"]["sga"] == 125)

print("fundamentals: a bank (no current split, no tagged total liabilities) degrades right")
bank_bal = [{"date": "2023-12-31", "netReceivables": None, "propertyPlantEquipmentNet": 50,
             "totalAssets": 5000, "longTermDebt": 800, "retainedEarnings": 400,
             "totalStockholdersEquity": 600},
            {"date": "2022-12-31", "totalAssets": 4800, "totalStockholdersEquity": 560,
             "retainedEarnings": 350}]
fmp._get = make_get({"income-statement": INCOME, "balance-sheet": bank_bal,
                     "cash-flow": CASHFLOW})
bank = fmp._pull_fundamentals("BANK")
check("bank flagged is_financial (no current-asset split)", bank["is_financial"] is True)
check("total_liabilities derived as assets - equity", bank["curr"]["total_liabilities"] == 4400)
check("missing current_assets is None (Altman/Beneish then degrade)",
      bank["curr"]["current_assets"] is None)

print("fundamentals: thin or single-year pulls fall through to the next source (None)")
fmp._get = make_get({"income-statement": [INCOME[0]], "balance-sheet": [BALANCE[0]],
                     "cash-flow": CASHFLOW})
check("only one fiscal year -> None", fmp._pull_fundamentals("TST") is None)
thin_inc = [dict(INCOME[0], revenue=None), INCOME[1]]
fmp._get = make_get({"income-statement": thin_inc, "balance-sheet": BALANCE,
                     "cash-flow": CASHFLOW})
check("missing core revenue -> None", fmp._pull_fundamentals("TST") is None)
fmp._get = make_get({})
check("empty statements -> None", fmp._pull_fundamentals("TST") is None)

# ----------------------------------------------------------------------------
print("price / market cap parsing")
fmp._get = make_get({"quote": [{"price": 50.0, "marketCap": 500000, "sharesOutstanding": 10000,
                                "timestamp": 1_700_000_000}]})
q = fmp._pull_quote("TST")
check("market cap read straight through", q["market_cap"] == 500000)
check("price and shares carried", q["price"] == 50.0 and q["shares"] == 10000)
check("source is FMP", q["source"] == "Financial Modeling Prep")
check("as_of derived from the quote timestamp", q["as_of"] == "2023-11-14")

fmp._get = make_get({"quote": [{"price": 50.0, "sharesOutstanding": 10000}]})
check("market cap computed price*shares when absent",
      fmp._pull_quote("TST")["market_cap"] == 500000)
fmp._get = make_get({"quote": []})
check("empty quote -> None", fmp._pull_quote("TST") is None)

# ----------------------------------------------------------------------------
print("daily-close history for the Merton volatility")
hist_rows = [{"date": f"2023-01-{d:02d}", "close": 100 + d} for d in range(1, 26)]
# hand it to FMP newest-first (as the real API does) to prove we sort ascending
fmp._get = make_get({"historical-price-full": {"historical": list(reversed(hist_rows))}})
h = fmp._pull_history("TST")
check("history parses enough closes", h is not None and h["n"] == 25)
check("closes returned oldest-first (ascending)",
      h["closes"][0] == 101 and h["closes"][-1] == 125)
check("as_of is the most recent close date", h["as_of"] == "2023-01-25")
check("closes feed the existing volatility fn",
      prices.annualized_volatility(h["closes"]) is not None)
fmp._get = make_get({"historical-price-full": {"historical": hist_rows[:5]}})
check("too few closes -> None (Merton degrades)", fmp._pull_history("TST") is None)

# ----------------------------------------------------------------------------
print("sector / profile")
fmp._get = make_get({"profile": [{"sector": "Technology", "industry": "Software",
                                  "currency": "USD", "beta": 1.2, "companyName": "Test Co"}]})
prof = fmp._pull_profile("TST")
check("sector extracted", prof["sector"] == "Technology")
check("industry and name carried", prof["industry"] == "Software" and prof["name"] == "Test Co")
fmp._get = make_get({"profile": [{"sector": "", "industry": "Software"}]})
check("blank sector -> None (falls back to snapshot/.info)", fmp._pull_profile("TST") is None)

# ----------------------------------------------------------------------------
print("analyst overlay: parses when premium data is present, None when all declined")
fmp._get = make_get({
    "upgrades-downgrades-consensus": [{"consensus": "Buy", "strongBuy": 5, "buy": 8,
                                       "hold": 3, "sell": 1, "strongSell": 0}],
    "price-target-consensus": [{"targetConsensus": 220, "targetHigh": 260,
                                "targetLow": 180, "targetMedian": 225}],
    "analyst-estimates": [{"date": "2024-12-31", "estimatedEpsAvg": 6.5,
                           "estimatedRevenueAvg": 400000}],
})
ov = fmp._pull_analyst("TST")
check("consensus rating parsed", ov["consensus"]["rating"] == "Buy")
check("buy/hold/sell counts parsed", ov["consensus"]["buy"] == 8 and ov["consensus"]["hold"] == 3)
check("price target consensus parsed", ov["price_target"]["consensus"] == 220)
check("forward estimates parsed", ov["estimates"]["eps_avg"] == 6.5)
check("as_of taken from the estimate period", ov["as_of"] == "2024-12-31")
fmp._get = make_get({})       # free tier: every premium endpoint declined
check("all premium calls declined -> None (honest 'not available')",
      fmp._pull_analyst("TST") is None)

# ----------------------------------------------------------------------------
print("no key: every fetcher returns None and never raises")
_orig_key = fmp._api_key
fmp._api_key = lambda: None
fmp._get = _orig_get          # real _get, which short-circuits to None with no key
check("fetch_price None", fmp.fetch_price("NOKEYTST") is None)
check("fetch_daily_closes None", fmp.fetch_daily_closes("NOKEYTST") is None)
check("fetch_fundamentals None", fmp.fetch_fundamentals("NOKEYTST") is None)
check("fetch_profile None", fmp.fetch_profile("NOKEYTST") is None)
check("fetch_analyst None", fmp.fetch_analyst("NOKEYTST") is None)
check("has_key reports False", fmp.has_key() is False)
fmp._api_key = _orig_key

# ----------------------------------------------------------------------------
# LAYERING: data.fetch_live orchestration (EDGAR primary, FMP for the market side).
# Stub the module-level functions data.fetch_live calls so no network is touched.
# ----------------------------------------------------------------------------
print("layering: data.fetch_live keeps EDGAR primary, uses FMP for the market side")
import edgar

EDGAR_PAYLOAD = {
    "meta": {"name": "Edgar Co", "ticker": "LAY", "source": "SEC EDGAR (companyfacts)",
             "period_curr": "FY ending 2023-12-31", "period_prior": "FY ending 2022-12-31",
             "is_financial": False, "sector": "Industrials",
             "fundamentals_source": "SEC EDGAR (companyfacts)",
             "fundamentals_as_of": "2023-12-31", "fundamentals_fetched_at": 123.0},
    "market_value_equity": 0.0,
    "curr": {k: 1.0 for k in LINE_ITEMS}, "prior": {k: 1.0 for k in LINE_ITEMS},
}

_saved = (edgar.fetch_edgar, fmp.fetch_fundamentals, prices.fetch_price,
          prices.fetch_equity_volatility, fmp.fetch_profile, fmp.fetch_analyst,
          data.fetch_yfinance)

edgar.fetch_edgar = lambda t: dict(EDGAR_PAYLOAD, meta=dict(EDGAR_PAYLOAD["meta"]))
fmp.fetch_fundamentals = lambda t: (_ for _ in ()).throw(AssertionError("EDGAR should win"))
prices.fetch_price = lambda t: {"market_cap": 987.0, "as_of": "2026-07-24",
                                "source": "Financial Modeling Prep"}
prices.fetch_equity_volatility = lambda t: {"value": 0.33, "window": "1y",
                                            "as_of": "2026-07-24",
                                            "source": "Financial Modeling Prep (daily history)"}
fmp.fetch_profile = lambda t: {"sector": "Technology", "as_of": 1.0,
                               "source": "Financial Modeling Prep"}
fmp.fetch_analyst = lambda t: None      # free tier

live = data.fetch_live("LAY")
prov = live["meta"]["provenance"]
check("fundamentals stay EDGAR", "EDGAR" in prov["fundamentals"]["source"])
check("price came from FMP and filled MVE",
      live["market_value_equity"] == 987.0 and prov["price"]["source"] == "Financial Modeling Prep")
check("equity volatility came from FMP",
      "Financial Modeling Prep" in prov["equity_volatility"]["source"]
      and live["equity_volatility"] == 0.33)
check("FMP sector overrode the payload's own classification",
      live["meta"]["sector"] == "Technology" and prov["sector"]["source"] == "Financial Modeling Prep")
check("analyst overlay present but not-available on the free tier",
      prov["analyst"]["available"] is False and live["analyst"] is None)
check("provenance carries all five live inputs",
      {"fundamentals", "price", "equity_volatility", "sector", "analyst"} <= set(prov))

print("layering: EDGAR miss -> FMP fundamentals, before yfinance")
FMP_PAYLOAD = dict(EDGAR_PAYLOAD,
                   meta=dict(EDGAR_PAYLOAD["meta"], source="Financial Modeling Prep",
                             fundamentals_source="Financial Modeling Prep"))
edgar.fetch_edgar = lambda t: None
fmp.fetch_fundamentals = lambda t: {"meta": dict(FMP_PAYLOAD["meta"]),
                                    "market_value_equity": 0.0,
                                    "curr": dict(FMP_PAYLOAD["curr"]),
                                    "prior": dict(FMP_PAYLOAD["prior"])}
data.fetch_yfinance = lambda t: (_ for _ in ()).throw(AssertionError("FMP should win"))
live2 = data.fetch_live("LAY")
check("fundamentals fall to FMP when EDGAR misses",
      live2["meta"]["provenance"]["fundamentals"]["source"] == "Financial Modeling Prep")

print("layering: analyst overlay lights up automatically when the key is upgraded")
fmp.fetch_analyst = lambda t: {"consensus": {"rating": "Buy"}, "price_target": None,
                               "estimates": None, "as_of": "2024-12-31",
                               "source": "Financial Modeling Prep"}
live3 = data.fetch_live("LAY")
rep = report.build_report(live3)
check("report analyst overlay is available and labeled",
      rep["analyst"]["available"] is True
      and rep["analyst"]["consensus"]["rating"] == "Buy"
      and rep["analyst"]["source"] == "Financial Modeling Prep")
check("overlay never entered curr/prior (scores never read it)",
      "analyst" not in live3["curr"] and "analyst" not in live3["prior"])

# restore
(edgar.fetch_edgar, fmp.fetch_fundamentals, prices.fetch_price,
 prices.fetch_equity_volatility, fmp.fetch_profile, fmp.fetch_analyst,
 data.fetch_yfinance) = _saved
fmp._get = _orig_get

print(f"\n{passed} checks passed.")
