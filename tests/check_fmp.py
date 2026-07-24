"""
Quick confirmation that the Financial Modeling Prep key is wired up and live.

Run AFTER setting FMP_API_KEY (env var, or in .streamlit/secrets.toml):
    python3 tests/check_fmp.py

It tells you plainly, per ticker, which layer the live data came from (FMP vs the
Finnhub/yfinance fallbacks) and whether the premium analyst overlay is available on your
tier. This is NOT part of the offline suite: with no key it exits cleanly, because the
app is designed to keep working on the fallbacks. No key is ever printed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fmp
import data

key = fmp._api_key()
if not key:
    print("FMP key: NOT SET.")
    print("Set FMP_API_KEY (env var) or add it to .streamlit/secrets.toml, then re-run.")
    print("The app still works meanwhile: price/volatility/sector fall back to "
          "Finnhub/yfinance, and fundamentals to EDGAR.")
    sys.exit(0)

print(f"FMP key: found (ends in ...{key[-4:]}).")

# Direct adapter probes (do the FMP endpoints answer on this tier?).
print("\nAdapter probes:")
q = fmp.fetch_price("AAPL")
print(f"  price          : {'FMP' if q and q.get('source','').startswith('Financial') else 'none'}"
      f"  (AAPL MVE=${q['market_cap']:,.0f})" if q and q.get("market_cap") else "  price          : none")
h = fmp.fetch_daily_closes("AAPL")
print(f"  daily history  : {'FMP ('+str(h['n'])+' closes)' if h else 'none (Merton uses yfinance)'}")
p = fmp.fetch_profile("AAPL")
print(f"  sector         : {p['sector'] if p else 'none (falls back to snapshot/.info)'}")
fund = fmp.fetch_fundamentals("AAPL")
print(f"  fundamentals   : {'FMP available (fallback ready)' if fund else 'none'}")
a = fmp.fetch_analyst("AAPL")
print(f"  analyst overlay: {'AVAILABLE (paid tier)' if a else 'not available (free tier) - overlay will light up on upgrade'}")

# End-to-end: which source actually served each input through the orchestrator.
print("\nEnd-to-end fetch_live (which layer served each input):")
for tkr in ("AAPL", "INTC", "JPM"):
    try:
        live = data.fetch_live(tkr)
    except Exception as e:  # noqa: BLE001
        print(f"  {tkr}: could not fetch ({e})")
        continue
    prov = live["meta"]["provenance"]
    print(f"  {tkr}: fundamentals={prov['fundamentals']['source']} | "
          f"price={prov['price']['source']} | "
          f"vol={(prov['equity_volatility'] or {}).get('source')} | "
          f"sector={prov['sector']['source']} ({live['meta'].get('sector')}) | "
          f"analyst={'yes' if prov['analyst']['available'] else 'no'}")

print("\nDone. Nothing here is committed and no key was printed.")
