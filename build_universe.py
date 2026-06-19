"""
build_universe.py — one-time snapshot builder for the S&P 500 universe.

Step 4 (data layer) of the Financial Health & Red-Flag Screener. The sector and
relative-value features that follow need scores for hundreds of companies; fetching
those live on every page load would be slow and would hammer Yahoo's rate limits.
So we snapshot the universe ONCE into data/universe_snapshot.csv and let the app
read that file instead.

Methodology is identical to the live tool: this script REUSES fetch_live() and
run_models() from data.py, which in turn call the Altman/Piotroski/Beneish formulas
in models.py. The only extra calls are to yfinance's .info for the descriptive and
relative-value fields (sector, price/book, EV/EBITDA, market cap, long name).

It is deliberately resilient: each ticker is wrapped in try/except with one retry,
failures are skipped (not fatal), and whatever succeeds is written out. Expect it to
take several minutes — it is fetching ~500 companies with a polite delay between each.

Run:  python3 build_universe.py
"""
from __future__ import annotations

import csv
import datetime
import os
import time
import traceback
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

from data import fetch_live, run_models

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; universe-snapshot/1.0)"}
OUT_PATH = "data/universe_snapshot.csv"
SLEEP_SECONDS = 0.7          # polite delay between tickers (rate limits)

CSV_COLUMNS = ["tr", "name", "sector", "z", "zone", "f_score", "m_score", "m_flag",
               "price_to_book", "ev_ebitda", "market_cap", "as_of_date"]

# yfinance occasionally returns inconsistent sector labels for a handful of names
# (e.g. "Financials" instead of "Financial Services"). Normalize so peers group together.
SECTOR_ALIASES = {"Financials": "Financial Services"}


def get_sp500() -> list[tuple[str, str]]:
    """Return [(ticker, gics_sector), ...] from the Wikipedia S&P 500 list."""
    resp = requests.get(WIKI_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    df = pd.read_html(StringIO(resp.text))[0]
    pairs = []
    for _, row in df.iterrows():
        # yfinance uses dashes where Wikipedia uses dots (e.g. BRK.B -> BRK-B).
        symbol = str(row["Symbol"]).strip().replace(".", "-")
        sector = str(row["GICS Sector"]).strip()
        if symbol:
            pairs.append((symbol, sector))
    return pairs


def snapshot_one(ticker: str, gics_sector: str, as_of: str) -> dict:
    """
    Build one universe row for a ticker. Reuses fetch_live()/run_models() for the
    scores (same methodology as the live app) and yfinance .info for the descriptive
    and relative-value fields. Raises on any failure so the caller can retry/skip.
    """
    payload = fetch_live(ticker)
    altman, piotroski, beneish, _verdict, _notes = run_models(payload)

    info = {}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}

    return {
        "tr": ticker,
        "name": info.get("longName") or payload["meta"].get("name") or ticker,
        # prefer yfinance's sector; fall back to the Wikipedia GICS sector; normalize aliases
        "sector": SECTOR_ALIASES.get(info.get("sector") or gics_sector,
                                     info.get("sector") or gics_sector),
        "z": altman.z if altman is not None else None,
        "zone": altman.zone if altman is not None else None,
        "f_score": piotroski.score if piotroski is not None else None,
        "m_score": beneish.m if beneish is not None else None,
        "m_flag": beneish.flag if beneish is not None else None,
        "price_to_book": info.get("priceToBook"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "market_cap": info.get("marketCap") or (payload.get("market_value_equity") or None),
        "as_of_date": as_of,
    }


def main() -> None:
    as_of = datetime.date.today().isoformat()

    # 1) make sure the output folder exists before we try to write into it
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    # 2) get the universe; if the Wikipedia scrape/parse fails, say so loudly
    try:
        tickers = get_sp500()
    except Exception as e:
        print("FATAL: could not fetch/parse the S&P 500 list from Wikipedia.")
        print(f"  {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    total = len(tickers)
    print(f"Fetched {total} S&P 500 tickers from Wikipedia. Building snapshot "
          f"(~{SLEEP_SECONDS}s/ticker, this takes several minutes)...\n", flush=True)

    fetched = 0          # companies fetched THIS run
    failed = []

    # RESUME SUPPORT: if the snapshot already exists, collect the tickers it already
    # holds so we can skip them and APPEND the rest (no overwrite, no repeated header).
    done = set()
    resuming = os.path.exists(OUT_PATH) and os.path.getsize(OUT_PATH) > 0
    if resuming:
        try:
            with open(OUT_PATH, newline="") as rf:
                for rec in csv.DictReader(rf):
                    t = (rec.get("tr") or "").strip()
                    if t:
                        done.add(t)
        except Exception as e:
            print(f"Warning: couldn't read existing {OUT_PATH} ({e}); starting fresh.")
            done, resuming = set(), False
    if done:
        print(f"Resuming: {len(done)} tickers already in {OUT_PATH}, skipping those.\n",
              flush=True)

    # 3) write INCREMENTALLY: append when resuming (header already present), else
    #    create with a header. Either way, flush every row so a crash or Ctrl-C never
    #    throws away the companies already fetched.
    with open(OUT_PATH, "a" if resuming else "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not resuming:
            writer.writeheader()
            f.flush()

        for i, (ticker, gics_sector) in enumerate(tickers, start=1):
            if ticker in done:                       # already snapshotted in a prior run
                continue
            row = None
            for attempt in (1, 2):                   # try once, retry once
                try:
                    row = snapshot_one(ticker, gics_sector, as_of)
                    break
                except Exception as e:
                    if attempt == 1:
                        time.sleep(1.5)              # brief backoff before the retry
                        continue
                    print(f"[{i}/{total}] {ticker} FAIL ({type(e).__name__}): "
                          f"{str(e)[:80]}", flush=True)
            if row is not None:
                writer.writerow({k: ("" if row[k] is None else row[k]) for k in CSV_COLUMNS})
                f.flush()                            # persist this row immediately
                done.add(ticker)
                fetched += 1
                print(f"[{i}/{total}] {ticker} ok", flush=True)
            else:
                failed.append(ticker)
            time.sleep(SLEEP_SECONDS)

    # --- summary (reflects the FULL file, not just this run) ---
    with open(OUT_PATH, newline="") as rf:
        all_rows = list(csv.DictReader(rf))
    remaining = [t for (t, _) in tickers if t not in done]

    print("\n" + "=" * 60)
    print(f"This run: {fetched} fetched, {len(failed)} failed.")
    print(f"Total rows now in {OUT_PATH}: {len(all_rows)} (as of {as_of}).")
    print(f"Tickers remaining (rerun to continue): {len(remaining)}")
    if failed:
        print(f"Failed this run: {', '.join(failed)}")

    sector_counts = {}
    for row in all_rows:
        sector_counts[row["sector"]] = sector_counts.get(row["sector"], 0) + 1
    print("\nPer-sector counts (full file):")
    for sector in sorted(sector_counts, key=lambda s: (-sector_counts[s], s)):
        print(f"  {sector_counts[sector]:>4}  {sector}")


if __name__ == "__main__":
    # 4) any fatal error prints a full traceback (instead of dying silently)
    try:
        main()
    except Exception:
        print("\nFATAL ERROR: build aborted.")
        traceback.print_exc()
        raise
