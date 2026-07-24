"""
Live probe for the sector-peer data path (step 0 of the sector-benchmarking build).

This is the script that DECIDES the peer depth. It answers four questions against the
real key, none of which can be answered offline:

  1. Which /stable endpoints does this key actually serve? (company-screener,
     financial-scores, available-sectors, scores-bulk, stock-peers.) Last time the
     assumption that /api/v3 worked cost a whole build, so nothing here is assumed.
  2. Does FMP's sector vocabulary in the screener match what fmp.fetch_profile returns
     for a real company? If it does not, the peer set comes back empty and the whole
     benchmark silently vanishes, so this is the highest-risk check in the file.
  3. How many peers does each sector actually return at the approved filters
     (US-listed, actively trading, non-fund, market cap > $2B, N = 25)?
  4. What does one full sector build cost in calls and wall-clock, and does the key's
     rate limit tolerate it? That number picks "full" depth (3 calls per peer, all four
     metrics live) over "core" depth (1 call per peer, leverage and Z live).

Run AFTER setting FMP_API_KEY (env var, or in .streamlit/secrets.toml):
    python3 tests/check_sector_peers.py

Like tests/check_fmp.py this is NOT part of the offline suite: with no key it exits
cleanly, because the app is designed to keep working on the snapshot fallback. No key
is ever printed, and nothing here writes to the repo.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import fmp
import sector_peers

key = fmp._api_key()
if not key:
    print("FMP key: NOT SET.")
    print("Set FMP_API_KEY (env var) or add it to .streamlit/secrets.toml, then re-run.")
    print("The benchmark still works meanwhile: it falls back to the committed S&P 500 "
          "snapshot, labeled stale.")
    sys.exit(0)

print(f"FMP key: found (ends in ...{key[-4:]}).")


# ----------------------------------------------------------------------------
# 1. Raw endpoint probe. fmp._get swallows failures by design, so this one talks
#    to requests directly to see the actual HTTP status and the error body.
# ----------------------------------------------------------------------------
def probe(path: str, params: dict):
    """Return (status, note, payload). Never prints or returns the key."""
    q = dict(params)
    q["apikey"] = key
    try:
        r = requests.get(f"{fmp.BASE}/{path}", params=q, timeout=30)
    except Exception as e:  # noqa: BLE001
        return None, f"network error ({type(e).__name__})", None
    try:
        body = r.json()
    except ValueError:
        return r.status_code, "non-JSON body", None
    if isinstance(body, dict) and body.get("Error Message"):
        return r.status_code, str(body["Error Message"])[:90], None
    if isinstance(body, list):
        return r.status_code, f"{len(body)} rows", body
    return r.status_code, "object", body


print("\n1. ENDPOINT AVAILABILITY ON THIS KEY")
checks = [
    ("company-screener  (peer set)", "company-screener",
     {"sector": "Technology", "country": "US", "isEtf": "false", "isFund": "false",
      "isActivelyTrading": "true", "marketCapMoreThan": sector_peers.MIN_MARKET_CAP,
      "limit": 5}),
    ("financial-scores  (core depth)", "financial-scores", {"symbol": "AAPL"}),
    ("income-statement  (full depth)", "income-statement",
     {"symbol": "AAPL", "period": "annual", "limit": 2}),
    ("available-sectors (vocabulary)", "available-sectors", {}),
    ("scores-bulk       (whole market, 1 call, likely premium)", "scores-bulk",
     {"part": "0"}),
    ("stock-peers       (not used, probed for completeness)", "stock-peers",
     {"symbol": "AAPL"}),
]
available = {}
for label, path, params in checks:
    status, note, body = probe(path, params)
    available[path] = (status == 200 and body is not None)
    print(f"  [{'OK ' if available[path] else 'NO '}] {label:<58} "
          f"HTTP {status}  {note}")

if not available.get("company-screener"):
    print("\nSTOP: the screener is the peer set. Without it there is no live benchmark "
          "and the snapshot fallback stays the only source. Nothing else below matters.")
    sys.exit(1)


# ----------------------------------------------------------------------------
# 2. Sector vocabulary. The screener's sector names must match what the company
#    lookup returns, or every peer set comes back empty.
# ----------------------------------------------------------------------------
print("\n2. SECTOR VOCABULARY MATCH (the highest-risk integration point)")
_status, _note, sectors_body = probe("available-sectors", {})
listed = []
if isinstance(sectors_body, list):
    listed = [str(r.get("sector") or r).strip() for r in sectors_body if r]
    print(f"  FMP lists {len(listed)} sectors: {', '.join(listed) or '(none)'}")
else:
    listed = list(sector_peers.FMP_SECTORS)
    print("  available-sectors did not answer; falling back to the built-in list "
          f"({len(listed)} sectors).")

for tkr in ("AAPL", "INTC", "JPM"):
    prof = fmp.fetch_profile(tkr)
    raw = (prof or {}).get("sector")
    norm = sector_peers.normalize_sector(raw)
    known = norm in listed if listed else None
    print(f"  {tkr}: profile sector={raw!r} -> normalized={norm!r} "
          f"-> in FMP's sector list: {known}")


# ----------------------------------------------------------------------------
# 3. Peer counts per sector at the approved filters.
# ----------------------------------------------------------------------------
print(f"\n3. PEER COUNTS AT THE APPROVED FILTERS "
      f"(US, actively trading, non-fund, cap > ${sector_peers.MIN_MARKET_CAP:,}, "
      f"N = {sector_peers.PEER_COUNT})")
for sector in sector_peers.FMP_SECTORS:
    rows = sector_peers.screen_sector(sector, limit=sector_peers.PEER_COUNT)
    n = len(rows or [])
    verdict = "ok" if n >= sector_peers.MIN_PEERS_LIVE else "TOO THIN, snapshot will serve"
    print(f"  {sector:<24} {n:>3} peers   {verdict}")


# ----------------------------------------------------------------------------
# 4. The cost of one real sector build, at both depths. This picks the default.
# ----------------------------------------------------------------------------
print("\n4. BUILD COST (this is what decides full vs core depth)")
for depth in ("core", "full"):
    if depth == "core" and not available.get("financial-scores"):
        print("  core: financial-scores is not served on this key, so core depth is out.")
        continue
    if depth == "full" and not available.get("income-statement"):
        print("  full: statement endpoints are not served on this key, so full depth is out.")
        continue
    sector_peers.clear_cache("Technology")
    t0 = time.time()
    built = sector_peers.build_rows("Technology", depth=depth,
                                    limit=sector_peers.PEER_COUNT)
    elapsed = time.time() - t0
    rows = built["rows"] if built else []
    per_peer = 1 if depth == "core" else 3
    calls = 1 + sector_peers.PEER_COUNT * per_peer
    scored = {m: sum(1 for r in rows if r.get(m) is not None)
              for m in ("leverage", "z", "f_score", "m_score")}
    print(f"  {depth:<5}: {len(rows)} peers scored in {elapsed:.1f}s, "
          f"~{calls} calls ({per_peer}/peer + 1 screener)")
    print(f"         metrics with data: " +
          ", ".join(f"{m}={c}" for m, c in scored.items()))
    if rows:
        print(f"         sample peer: {rows[0]}")

print("\n5. CROSS-CHECK: our Altman Z vs FMP's own altmanZScore (AAPL)")
_s, _n, scores_body = probe("financial-scores", {"symbol": "AAPL"})
if isinstance(scores_body, list) and scores_body:
    theirs = scores_body[0].get("altmanZScore")
    ours = sector_peers.altman_from_scores(scores_body[0])
    print(f"  FMP altmanZScore={theirs}  |  our models.altman_z on their raw inputs={ours}")
    print("  These should be close. A large gap means FMP uses a different Altman variant, "
          "which is exactly why we recompute rather than trust their number.")
else:
    print("  financial-scores not served on this key, cross-check skipped.")

print("\nDone. Nothing was committed and no key was printed.")
print("Report section 4 back: the peer count, wall-clock, and whether the key survived "
      "the call burst is what sets DEFAULT_DEPTH.")
