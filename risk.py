"""
risk.py - Portfolio-level risk and return: the Sharpe-ratio delta per holding, and the
concentration read that explains it.

The question this answers is the one no holdings table can: "what is each position
actually doing to the risk-adjusted return of the whole portfolio?" A holding is not
risky or safe on its own. A volatile stock that moves against everything else you own
makes the portfolio steadier, and a calm stock that is a near-duplicate of your other
nine makes it more fragile. That is a correlation question, so it is computed from the
covariance of the holdings' returns, not from any single-name statistic.

This is a PORTFOLIO analytic and it is strictly separate from the distress scores.
Nothing here touches Altman, Piotroski, Beneish, Merton, or the Spring Score, and no
number computed here ever flows back into them.

THE METHOD, stated in full (the glass box applies here too)

  Returns window   Trailing one year of daily closes, the same history the Merton
                   equity volatility already pulls (FMP primary, yfinance fallback).
                   SIMPLE returns, p_t/p_t-1 - 1, not log returns: a portfolio's return
                   is the weighted sum of its holdings' simple returns, and log returns
                   are not additive across assets. (Merton uses log returns because it
                   is estimating one asset's diffusion, a different question.)

  Alignment        Closes are intersected on common trading dates FIRST, then returns
                   are computed from the aligned closes, so every pair of holdings is
                   measured over exactly the same intervals. Gaps are never padded and
                   never zero-filled: a zero-filled gap makes a thin holding look
                   uncorrelated, which understates portfolio risk. That is the one
                   dishonest failure mode this module refuses.

  Minimum history  MIN_ALIGNED_DAYS (60) aligned trading days, about three months.
                   Below that a holding is excluded by name with its reason.

  Weights          Market value, shares times latest close, normalized. If any covered
                   holding has no share count in the uploaded CSV, the whole read falls
                   back to equal weight and says so in `basis`, the same honesty
                   portfolio.sector_concentration already practices.

  Risk-free proxy  models.DEFAULT_RISK_FREE (0.04 annual), IMPORTED not redefined, so
                   the Sharpe and the Merton model can never drift apart. Daily rate is
                   (1 + rf) ** (1/252) - 1.

  Sharpe           Mean daily excess return over the sample standard deviation (n-1) of
                   daily excess returns, annualized by sqrt(252).

  Correlations     Carried by the sample covariance matrix of the aligned daily returns.
                   No shrinkage, deliberately: the leave-one-out math below only ever
                   takes weighted sums and never inverts the covariance matrix, so the
                   ill-conditioning that motivates a Ledoit-Wolf estimator never arises,
                   and shrinking would bias the delta to fix a problem this calculation
                   does not have. The correlation matrix is reported so it is visible.

  The delta        For each holding i: drop it and renormalize the survivors pro rata,
                   w_j / (1 - w_i). In plain English, sell the position and spread the
                   proceeds across everything else you already own. Then
                   sharpe_delta = Sharpe(without i) - Sharpe(full).
                   POSITIVE means removing it would have improved risk-adjusted return.
                   The counterfactual is stated in the output because holding the
                   proceeds as cash is a different question with a different answer.

  Why, not just what
                   Each holding also reports its beta to the portfolio and its share of
                   total portfolio volatility (these sum to 100 percent). That is what
                   makes "a 10 percent position that is 25 percent of your risk"
                   legible, and it is the correlation story made visible.

Pure and framework-free, pure stdlib, no network and no globals inside these functions
(the same discipline as models.py and portfolio.py). The live fetch is injected as a
callable into portfolio_risk(), so tests run the whole analytic with zero network.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

from models import DEFAULT_RISK_FREE

# Annualization convention, shared with prices.annualized_volatility.
TRADING_DAYS_PER_YEAR = 252

# The shortest shared window worth reporting on: about three months of trading. Below
# this the covariance estimate is too thin to say anything honest about correlation.
MIN_ALIGNED_DAYS = 60

# A Sharpe move smaller than this is noise at these sample sizes, so the plain-English
# read calls the holding neutral rather than naming it a drag or a diversifier.
MATERIAL_DELTA = 0.05

# Average pairwise correlation at or above this means the holdings largely move as one.
HIGH_AVG_CORRELATION = 0.60

# Fewer independent-equivalent holdings than this is worth naming out loud.
LOW_EFFECTIVE_HOLDINGS = 4.0

# A daily return series whose standard deviation is below this is flat. It matters that
# this is not a literal zero test: summing a constant series leaves floating-point
# residue, so an unchanged price produces a standard deviation around 1e-18 rather than
# 0.0, and dividing by that would report an astronomical Sharpe ratio for a stock that
# never moved. No real daily return series has a standard deviation this small.
FLAT_SD = 1e-12

CAVEAT = (
    "One year of daily data makes the Sharpe LEVEL a noisy estimate, so read the deltas "
    "(the comparison between holdings) rather than the absolute number. Correlations "
    "also rise in a selloff, which means the diversification measured here is the "
    "friendliest case, not the worst one.")


# ----------------------------------------------------------------------------
# Pure statistics (sample convention throughout, n-1)
# ----------------------------------------------------------------------------
def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _variance(xs: List[float]) -> float:
    """Sample variance (n-1). Zero for a constant series, which callers check for."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def _stdev(xs: List[float]) -> float:
    return math.sqrt(_variance(xs))


def covariance(a: List[float], b: List[float]) -> float:
    """Sample covariance (n-1) of two equal-length series."""
    n = len(a)
    if n < 2 or n != len(b):
        return 0.0
    ma, mb = _mean(a), _mean(b)
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


def correlation(a: List[float], b: List[float]) -> Optional[float]:
    """Pearson correlation, or None when either series is flat (undefined, not zero)."""
    sa, sb = _stdev(a), _stdev(b)
    if sa <= FLAT_SD or sb <= FLAT_SD:
        return None
    return covariance(a, b) / (sa * sb)


def daily_returns(closes: List[float]) -> List[float]:
    """
    Simple daily returns from a series of closes. See the module docstring on why these
    are simple and not log returns. A non-positive prior close makes a return undefined
    and is skipped; every internal caller feeds this a series already cleaned to
    positive closes, so lengths stay aligned across holdings.
    """
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
            if closes[i - 1] > 0]


def daily_risk_free(annual: float = DEFAULT_RISK_FREE) -> float:
    """The annual risk-free proxy as a daily rate, compounded not divided."""
    return (1.0 + annual) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0


def sharpe_ratio(returns: List[float], rf_daily: float) -> Optional[float]:
    """
    Annualized Sharpe ratio of a daily return series. None when the series is too short
    or has no variance, because a zero-volatility Sharpe is a divide by zero, not
    infinity, and reporting a number there would be a lie.
    """
    if len(returns) < 2:
        return None
    excess = [r - rf_daily for r in returns]
    sd = _stdev(excess)
    if sd <= FLAT_SD:
        return None
    return (_mean(excess) / sd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def portfolio_returns(weights: Dict[str, float],
                      returns_by_ticker: Dict[str, List[float]],
                      tickers: List[str]) -> List[float]:
    """
    The portfolio's daily return series at fixed weights: r_p,t = sum_i w_i * r_i,t.

    Fixed weights make this a constant-weight (daily rebalanced) counterfactual on
    today's portfolio, not a replay of the trades actually made. That is the honest
    reading of "what is this holding doing to my portfolio" and it is stated in the
    method block of the output.
    """
    n = len(returns_by_ticker[tickers[0]])
    return [sum(weights[t] * returns_by_ticker[t][i] for t in tickers)
            for i in range(n)]


def _renormalized_without(weights: Dict[str, float], drop: str,
                          tickers: List[str]) -> Optional[Dict[str, float]]:
    """
    Weights after selling `drop` and spreading the proceeds pro rata across the rest.
    None when the dropped position is the entire portfolio (nothing to spread into).
    """
    remaining = 1.0 - weights[drop]
    if remaining <= 1e-12:
        return None
    return {t: weights[t] / remaining for t in tickers if t != drop}


def _r(x, places: int = 4):
    """None-safe round, so every number in the output block is JSON-clean."""
    return None if x is None else round(float(x), places)


def _positive_float(v) -> Optional[float]:
    """
    A share count as a positive float, or None for anything unusable (missing, zero,
    negative, or a string a broker export left in the column). Never raises: a junk cell
    in one row must drop the read to equal weight, not crash the portfolio.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:                           # NaN or non-positive
        return None
    return f


# ----------------------------------------------------------------------------
# History cleaning and the shared trading window
# ----------------------------------------------------------------------------
def _clean_bars(history: dict):
    """
    A history payload to a sorted, deduped list of (date, close) with only positive
    closes. Later duplicates of a date win, which is what a corrected close should do.
    """
    dates = history.get("dates") or []
    closes = history.get("closes") or []
    by_date = {}
    for i in range(min(len(dates), len(closes))):
        c = closes[i]
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c != c or c <= 0:                       # NaN or non-positive
            continue
        by_date[str(dates[i])[:10]] = c
    return sorted(by_date.items())


def _shared_window(clean: Dict[str, list], candidates: List[str], excluded: List[dict]):
    """
    Intersect trading dates across `candidates` and return
    (surviving_candidates, dates, returns_by_ticker).

    When the intersection is too short to be honest about, the holding with the SHORTEST
    own history is dropped and the intersection retried, because one recent listing can
    otherwise truncate the shared window for an entire portfolio. Dropped holdings are
    appended to `excluded` by name, never silently. Ties break alphabetically so the
    result is deterministic.
    """
    live = list(candidates)
    while len(live) >= 2:
        common = set(d for d, _ in clean[live[0]])
        for t in live[1:]:
            common &= set(d for d, _ in clean[t])
        dates = sorted(common)
        if len(dates) - 1 >= MIN_ALIGNED_DAYS:
            rets = {}
            for t in live:
                by_date = dict(clean[t])
                rets[t] = daily_returns([by_date[d] for d in dates])
            return live, dates, rets
        shortest = min(live, key=lambda t: (len(clean[t]), t))
        live.remove(shortest)
        excluded.append({
            "ticker": shortest,
            "reason": (f"Shorter price history than the rest of the portfolio. Keeping "
                       f"it would have cut the shared window below {MIN_ALIGNED_DAYS} "
                       f"trading days.")})
    return live, [], {}


# ----------------------------------------------------------------------------
# Concentration read
# ----------------------------------------------------------------------------
def concentration_read(weights: Dict[str, float], tickers: List[str],
                       risk_pct: Dict[str, float],
                       returns_by_ticker: Dict[str, List[float]],
                       basis: str) -> dict:
    """
    The concentration answer in the three forms that actually matter, weight, risk, and
    correlation. Position count alone hides all three: ten holdings that are one bet are
    not a diversified portfolio, and the effective-holdings number says that in one
    figure.
    """
    ordered = sorted(tickers, key=lambda t: (-weights[t], t))
    hhi = sum(weights[t] ** 2 for t in tickers)
    effective = (1.0 / hhi) if hhi > 0 else None

    top = ordered[0]
    top3_pct = 100.0 * sum(weights[t] for t in ordered[:3])

    top_risk = max(tickers, key=lambda t: (risk_pct.get(t, 0.0), t))

    pairs = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            a, b = tickers[i], tickers[j]
            c = correlation(returns_by_ticker[a], returns_by_ticker[b])
            if c is not None:
                pairs.append((c, a, b))
    avg_corr = (sum(p[0] for p in pairs) / len(pairs)) if pairs else None
    top_pair = max(pairs, key=lambda p: (p[0], p[1], p[2])) if pairs else None

    n = len(tickers)
    bits = []
    if effective is not None:
        bits.append(f"Your {n} measured holdings carry about as much independent risk as "
                    f"{effective:.1f} equally sized ones.")
    if risk_pct.get(top_risk) is not None:
        bits.append(f"{top_risk} is {weights[top_risk] * 100:.0f}% of the portfolio by "
                    f"{basis} but {risk_pct[top_risk]:.0f}% of its risk.")
    if avg_corr is not None and avg_corr >= HIGH_AVG_CORRELATION:
        bits.append(f"They also move together (average pairwise correlation "
                    f"{avg_corr:.2f}), so the diversification is thinner than the count "
                    f"suggests.")

    return {
        "hhi": _r(hhi),
        "effective_holdings": _r(effective, 1),
        "top_weight_ticker": top,
        "top_weight_pct": _r(100.0 * weights[top], 1),
        "top3_weight_pct": _r(top3_pct, 1),
        "top_risk_ticker": top_risk,
        "top_risk_pct": _r(risk_pct.get(top_risk), 1),
        "avg_pairwise_correlation": _r(avg_corr),
        "most_correlated_pair": ({"tickers": [top_pair[1], top_pair[2]],
                                  "correlation": _r(top_pair[0])} if top_pair else None),
        "concentrated": bool(effective is not None and effective < LOW_EFFECTIVE_HOLDINGS),
        "headline": " ".join(bits) if bits else "Not enough holdings to read concentration.",
    }


# ----------------------------------------------------------------------------
# The analytic
# ----------------------------------------------------------------------------
def _unavailable(reason: str, excluded: List[dict], n_holdings: int) -> dict:
    return {
        "available": False,
        "reason": reason,
        "window": None,
        "basis": None,
        "portfolio": None,
        "holdings": [],
        "excluded": excluded,
        "coverage": {"n_holdings": n_holdings, "n_covered": 0,
                     "pct_of_value": None,
                     "note": reason},
        "concentration": None,
        "method": None,
        "caveat": CAVEAT,
        "headline": reason,
    }


def _mechanism(delta: Optional[float], own_sharpe: Optional[float],
               portfolio_sharpe: float) -> Optional[str]:
    """
    WHY a holding earns its place, which is not the same question as whether it does.

    A negative delta (removing the holding would hurt) has two completely different
    causes, and conflating them misleads. A position can be worth keeping because it
    moves differently from everything else, or because it is simply out-earning the
    portfolio per unit of its own risk. The second one is often the largest, most
    correlated position in the book, and calling that a "diversifier" would be wrong.
    Own Sharpe against portfolio Sharpe separates the two.
    """
    if delta is None or delta > -MATERIAL_DELTA or own_sharpe is None:
        return None
    return "return driver" if own_sharpe > portfolio_sharpe else "risk reducer"


def _holding_headline(ticker: str, delta: Optional[float], full: float,
                      without: Optional[float], mechanism: Optional[str] = None,
                      has_rest: bool = True) -> str:
    if delta is None or without is None:
        if not has_rest:
            return (f"{ticker} is effectively the whole portfolio, so there is nothing "
                    f"to compare it against.")
        return (f"With {ticker} removed, what is left has no measurable volatility over "
                f"this window, so the comparison is undefined.")
    if delta >= MATERIAL_DELTA:
        return (f"Selling {ticker} and spreading it across the rest would have raised "
                f"the portfolio's Sharpe from {full:.2f} to {without:.2f}. It is adding "
                f"more risk than return.")
    if delta <= -MATERIAL_DELTA:
        why = ("It is carrying more of the portfolio's return than its share of the risk."
               if mechanism == "return driver" else
               "It moves differently from the rest, which steadies the whole portfolio.")
        return (f"Selling {ticker} would have LOWERED the portfolio's Sharpe from "
                f"{full:.2f} to {without:.2f}. {why}")
    return (f"Dropping {ticker} barely moves the portfolio's Sharpe "
            f"({full:.2f} to {without:.2f}). It is carrying its weight, no more.")


def _role(delta: Optional[float]) -> str:
    """
    Deliberately named for what the delta SAYS, not for a mechanism it does not prove.
    "support" means removing this would hurt the risk-adjusted return; the separate
    `mechanism` field says whether that is diversification or return.
    """
    if delta is None:
        return "undefined"
    if delta >= MATERIAL_DELTA:
        return "drag"
    if delta <= -MATERIAL_DELTA:
        return "support"
    return "neutral"


def analyze_risk(histories: Dict[str, dict],
                 shares_by_ticker: Optional[Dict[str, float]] = None,
                 risk_free: float = DEFAULT_RISK_FREE,
                 order: Optional[List[str]] = None) -> dict:
    """
    The whole analytic, PURE: no network, no clock, no globals.

    histories          {ticker: {"dates": [...], "closes": [...]} or None}
    shares_by_ticker   {ticker: shares} where known. Any missing or non-positive entry
                       among the covered holdings drops the whole read to equal weight,
                       labeled, rather than mixing two weighting bases in one answer.
    order              explicit ticker order, so output is deterministic for a caller
                       that cares. Defaults to the histories' own key order.

    Returns one JSON-serializable block. `available` is False, with a reason and the
    per-holding exclusions intact, whenever the data cannot support an honest answer.
    """
    tickers = list(order) if order else list(histories)
    shares_by_ticker = shares_by_ticker or {}
    excluded: List[dict] = []

    # 1. Per-holding usability, before anything is compared to anything.
    clean: Dict[str, list] = {}
    usable: List[str] = []
    for t in tickers:
        h = histories.get(t)
        if not h or not h.get("closes"):
            excluded.append({"ticker": t,
                             "reason": "No price history available for this symbol."})
            continue
        bars = _clean_bars(h)
        if len(bars) < MIN_ALIGNED_DAYS + 1:
            excluded.append({
                "ticker": t,
                "reason": (f"Only {len(bars)} days of usable price history; "
                           f"{MIN_ALIGNED_DAYS} trading days are needed.")})
            continue
        if len(set(c for _, c in bars)) == 1:
            excluded.append({
                "ticker": t,
                "reason": ("This price has not moved, so it has no measurable risk or "
                           "correlation.")})
            continue
        clean[t] = bars
        usable.append(t)

    if len(usable) < 2:
        return _unavailable(
            "A Sharpe delta compares a holding against the rest of the portfolio, so it "
            "needs at least two holdings with a year of shared price history.",
            excluded, len(tickers))

    # 2. The shared window. Excluding a flat series can widen the window, so rebuild
    #    until the surviving set is stable (bounded by the number of holdings).
    candidates: List[str] = usable
    dates: List[str] = []
    rets: Dict[str, List[float]] = {}
    while True:
        candidates, dates, rets = _shared_window(clean, candidates, excluded)
        if len(candidates) < 2:
            break
        flat = [t for t in candidates if _stdev(rets[t]) <= FLAT_SD]
        if not flat:
            break
        for t in flat:
            excluded.append({
                "ticker": t,
                "reason": ("This price did not move over the window shared with the rest "
                           "of the portfolio, so it has no measurable correlation.")})
        candidates = [t for t in candidates if t not in set(flat)]

    if len(candidates) < 2:
        return _unavailable(
            "The holdings do not share enough overlapping trading history to compare "
            "them against each other.", excluded, len(tickers))

    # 3. Weights. One basis for the whole read, named in the output.
    latest = {t: clean[t][-1][1] for t in candidates}
    share_counts = {t: _positive_float(shares_by_ticker.get(t)) for t in candidates}
    have_shares = all(s is not None for s in share_counts.values())
    values = ({t: share_counts[t] * latest[t] for t in candidates}
              if have_shares else {})
    total_value = sum(values.values()) if values else 0.0
    if have_shares and total_value > 0:
        basis = "market value"
        weights = {t: values[t] / total_value for t in candidates}
    else:
        basis = "equal weight (no share counts in your file)"
        weights = {t: 1.0 / len(candidates) for t in candidates}

    rf_daily = daily_risk_free(risk_free)
    port = portfolio_returns(weights, rets, candidates)
    sharpe_full = sharpe_ratio(port, rf_daily)
    var_port = _variance(port)
    if sharpe_full is None or var_port <= FLAT_SD ** 2:
        return _unavailable(
            "The portfolio's combined return series has no measurable volatility over "
            "this window, so a Sharpe ratio is undefined.", excluded, len(tickers))
    sd_port = math.sqrt(var_port)

    # 4. Per holding: the delta, and the correlation facts that explain it.
    risk_pct: Dict[str, float] = {}
    rows: List[dict] = []
    for t in candidates:
        cov_ip = covariance(rets[t], port)
        beta = cov_ip / var_port
        rc_pct = 100.0 * weights[t] * beta        # these sum to 100 by construction
        risk_pct[t] = rc_pct

        rest_w = _renormalized_without(weights, t, candidates)
        if rest_w is None:
            sharpe_without = None
            delta = None
            corr_rest = None
        else:
            rest = [x for x in candidates if x != t]
            rest_series = portfolio_returns(rest_w, rets, rest)
            sharpe_without = sharpe_ratio(rest_series, rf_daily)
            delta = None if sharpe_without is None else sharpe_without - sharpe_full
            corr_rest = correlation(rets[t], rest_series)

        own_sharpe = sharpe_ratio(rets[t], rf_daily)
        mechanism = _mechanism(delta, own_sharpe, sharpe_full)

        rows.append({
            "ticker": t,
            "weight_pct": _r(100.0 * weights[t], 1),
            "sharpe_delta": _r(delta),
            "sharpe_without": _r(sharpe_without),
            "own_sharpe": _r(own_sharpe, 2),
            "annual_volatility_pct": _r(100.0 * _stdev(rets[t])
                                        * math.sqrt(TRADING_DAYS_PER_YEAR), 1),
            "beta_to_portfolio": _r(beta),
            "risk_contribution_pct": _r(rc_pct, 1),
            "correlation_to_rest": _r(corr_rest),
            "role": _role(delta),
            "mechanism": mechanism,
            "headline": _holding_headline(t, delta, sharpe_full, sharpe_without,
                                          mechanism=mechanism,
                                          has_rest=rest_w is not None),
        })

    # Largest improvement-on-removal first: the same weakest-first instinct the holdings
    # table uses, applied to risk-adjusted return instead of distress.
    rows.sort(key=lambda r: (-(r["sharpe_delta"] if r["sharpe_delta"] is not None
                               else -9e9), r["ticker"]))

    # 5. Coverage. Percent of value is only claimed when every holding can be valued.
    all_shares = {t: _positive_float(shares_by_ticker.get(t)) for t in tickers}
    all_valued = (have_shares
                  and all(all_shares[t] is not None for t in tickers)
                  and all(t in clean for t in tickers))
    if all_valued:
        grand = sum(all_shares[t] * clean[t][-1][1] for t in tickers)
        pct_of_value = (100.0 * total_value / grand) if grand > 0 else None
    else:
        pct_of_value = None

    n_excluded = len(excluded)
    if n_excluded == 0:
        note = f"All {len(tickers)} holdings are included."
    elif pct_of_value is not None:
        note = (f"{len(candidates)} of {len(tickers)} holdings, {pct_of_value:.0f}% of "
                f"the portfolio's value. The rest are listed with their reasons.")
    else:
        note = (f"{len(candidates)} of {len(tickers)} holdings. The rest are listed with "
                f"their reasons, and there is not enough data to say what share of your "
                f"value they represent.")

    conc = concentration_read(weights, candidates, risk_pct, rets, basis)

    drags = [r for r in rows if r["role"] == "drag"]
    supports = [r for r in rows if r["role"] == "support"]
    if drags:
        headline = (f"{drags[0]['ticker']} is the biggest drag on this portfolio's "
                    f"risk-adjusted return: dropping it would have lifted the Sharpe "
                    f"from {sharpe_full:.2f} to {drags[0]['sharpe_without']:.2f}.")
    else:
        headline = (f"No single holding is dragging this portfolio's risk-adjusted "
                    f"return; its Sharpe over the window is {sharpe_full:.2f}.")
    if supports:
        best = min(supports, key=lambda r: r["sharpe_delta"])
        why = ("carrying the most return for its risk"
               if best["mechanism"] == "return driver"
               else "doing the most diversification work")
        headline += f" {best['ticker']} is {why}."

    return {
        "available": True,
        "reason": None,
        "window": {"start": dates[0], "end": dates[-1], "n_days": len(port)},
        "basis": basis,
        "portfolio": {
            "sharpe": _r(sharpe_full, 2),
            "annual_return_pct": _r(100.0 * _mean(port) * TRADING_DAYS_PER_YEAR, 1),
            "annual_volatility_pct": _r(100.0 * sd_port
                                        * math.sqrt(TRADING_DAYS_PER_YEAR), 1),
        },
        "holdings": rows,
        "excluded": excluded,
        "coverage": {"n_holdings": len(tickers), "n_covered": len(candidates),
                     "pct_of_value": _r(pct_of_value, 1), "note": note},
        "concentration": conc,
        "method": {
            "returns": ("daily simple returns from a trailing year of closes, aligned on "
                        "shared trading dates"),
            "min_aligned_days": MIN_ALIGNED_DAYS,
            "risk_free_annual": risk_free,
            "risk_free_note": ("models.DEFAULT_RISK_FREE, the same constant the Merton "
                               "default-probability model uses"),
            "annualization_days": TRADING_DAYS_PER_YEAR,
            "weighting": basis,
            "correlations": ("sample covariance of the aligned daily returns, no "
                             "shrinkage: the leave-one-out math never inverts the matrix"),
            "counterfactual": ("sell the position and spread the proceeds pro rata across "
                               "the other holdings, at constant weights"),
            "sharpe": ("mean daily excess return over the sample standard deviation of "
                       "daily excess returns, annualized by sqrt(252)"),
        },
        "caveat": CAVEAT,
        "headline": headline,
    }


# ----------------------------------------------------------------------------
# Orchestration (the only place that touches the network, and only through the
# injected callable, exactly like portfolio.score_holdings)
# ----------------------------------------------------------------------------
def portfolio_risk(scored: List[dict], history_fn: Callable,
                   risk_free: float = DEFAULT_RISK_FREE) -> dict:
    """
    Run the analytic over already-scored portfolio rows, fetching each holding's price
    history through the injected `history_fn` (prices.fetch_price_history in the app, a
    stub in tests).

    Unscored holdings are included: you own them, so they belong in the risk picture,
    the same call portfolio.sector_concentration already makes. A history_fn that fails
    or returns None for a ticker degrades that holding to an exclusion with a reason,
    never an exception and never a guessed return series.
    """
    histories: Dict[str, dict] = {}
    shares: Dict[str, float] = {}
    order: List[str] = []
    for row in scored:
        t = str(row.get("ticker") or "").strip().upper()
        if not t or t in histories:
            continue
        order.append(t)
        try:
            histories[t] = history_fn(t)
        except Exception:                          # noqa: BLE001 - degrade, never crash
            histories[t] = None
        # portfolio.py stores the share count on the row as `weight`.
        s = row.get("weight")
        if s is None:
            s = row.get("shares")
        try:
            s = float(s) if s is not None else None
        except (TypeError, ValueError):
            s = None
        if s is not None and s > 0:
            shares[t] = s

    if not order:
        return _unavailable("No holdings to analyze.", [], 0)
    return analyze_risk(histories, shares, risk_free=risk_free, order=order)
