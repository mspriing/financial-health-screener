"""
Quick confirmation that the Finnhub key is wired up and live.

Run AFTER pasting your key into .streamlit/secrets.toml:
    python3 tests/check_finnhub.py

It tells you plainly whether the price layer is using Finnhub or the yfinance fallback.
Nothing here is committed and no key is ever printed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prices

key = prices._api_key()
if not key:
    print("Finnhub key: NOT SET.")
    print("Open .streamlit/secrets.toml and replace PASTE_YOUR_FINNHUB_KEY_HERE with your key.")
    print("The app still works meanwhile: prices fall back to yfinance.")
    sys.exit(0)

print(f"Finnhub key: found (ends in ...{key[-4:]}).")
info = prices.fetch_price("AAPL")
if not info:
    print("Price fetch returned nothing. Check the key is valid at https://finnhub.io/.")
    sys.exit(1)

src = info.get("source")
mve = info.get("market_cap") or 0
print(f"AAPL price source: {src}")
print(f"AAPL market value of equity: ${mve:,.0f}")
if src == "Finnhub":
    print("\nSUCCESS: Finnhub is live and serving prices.")
else:
    print("\nKey is set but Finnhub did not answer, so it fell back to yfinance.")
    print("Usually means the key is not active yet or the free-tier rate limit was hit. "
          "Wait a minute and re-run.")
