# Financial Health & Red-Flag Screener

A first-pass screen that reads a company's financial statements and returns one call:

> **Is this company financially healthy, on watch, or distressed — and do the earnings look clean or manipulated?**

Type in any public ticker (or pick a sample), and three published, finance-credentialed
models score the company on distress risk, fundamental strength, and earnings-manipulation
red flags — the kind of quick screen an analyst, risk team, or deal team runs before
digging into a name.

Built by **Michael Spring**.

---

## What it does

| Model | Question it answers | Output |
|---|---|---|
| **Altman Z-Score** (Altman, 1968) | How close is this company to financial distress? | Safe / Grey / Distress |
| **Piotroski F-Score** (Piotroski, 2000) | How strong are the fundamentals, year over year? | 0–9 score |
| **Beneish M-Score** (Beneish, *Financial Analysts Journal*, 1999) | Do the earnings show manipulation red flags? | Clean / Possible manipulation |

The app blends the three into a plain-English verdict — e.g. *"Watch — mixed signals,
with earnings-quality red flags"* — and lets you open each model to see the underlying
ratios and which signals passed or failed.

Why these three: together they cover the analyst's core diligence questions — **solvency
and distress risk** (Z), **fundamental quality and trend** (F), and **earnings integrity /
red flags** (M). That spread is what makes it useful for financial-analyst, risk, and M&A
diligence contexts rather than a single narrow metric.

## Three ways to feed it

1. **Live ticker** — pulls the two most recent annual statements from Yahoo Finance via
   `yfinance`. Works wherever the app has internet (e.g. Streamlit Community Cloud).
2. **Sample companies** — three illustrative profiles (healthy, distressed, earnings-red-flags)
   so the app always demos cleanly, even with no internet. *(Clearly labeled as sample data.)*
3. **Manual entry** — type two years of figures yourself; works for private companies too.

---

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL it prints (usually http://localhost:8501).

## Tests

```bash
python3 tests/test_models.py   # verifies the scoring math against hand-computed values
python3 tests/smoke_app.py     # renders the app for every sample company, asserts no errors
```

The math in `models.py` is validated against hand-computed expected values, including a
known non-manipulator baseline (M ≈ −2.48) and a constructed manipulator case.

---

## Deploy a live link (Streamlit Community Cloud — free)

See **DEPLOY.md** for step-by-step instructions (push to GitHub, then connect Streamlit
Community Cloud). The short version:

1. Push this folder to a public GitHub repo.
2. Go to <https://share.streamlit.io>, sign in with GitHub, **New app**.
3. Pick the repo, set the main file to `app.py`, and **Deploy**.
4. You get a public `https://…streamlit.app` URL to drop in your LinkedIn **Featured** section.

---

## Project structure

```
financial-health-screener/
├── app.py            # Streamlit UI
├── models.py         # the three scoring models (pure, testable)
├── data.py           # yfinance live fetch + sample presets + manual payloads
├── requirements.txt
├── .streamlit/
│   └── config.toml   # theme
├── tests/
│   ├── test_models.py
│   └── smoke_app.py
├── DEPLOY.md         # deployment walkthrough
└── README.md
```

## Notes & limitations

- `yfinance` is an unofficial Yahoo Finance wrapper; field coverage varies by company, and
  the Beneish M-Score needs detailed line items (SG&A, depreciation) that aren't always
  reported — the app degrades gracefully and shows "Not enough data" rather than crashing.
- The Altman model used is the **original** public-firm Z-Score; variants exist for private
  and non-manufacturing firms.
- **Educational screen, not investment advice.** These models are starting points for
  diligence, not verdicts.

## References

- Altman, E. (1968). *Financial Ratios, Discriminant Analysis and the Prediction of Corporate Bankruptcy.* Journal of Finance.
- Piotroski, J. (2000). *Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers.* Journal of Accounting Research.
- Beneish, M. (1999). *The Detection of Earnings Manipulation.* Financial Analysts Journal.
