# Financial Health & Red-Flag Screener

A first-pass financial screen. Enter a public company and it reads the financial statements, scores the company with three published models, explains what is driving each score, and shows how the company stacks up against its sector. It also includes a separate screen that surfaces companies matching a classic acquisition profile.

Live app: https://financial-health-screener-kfwhy5nbvjj2vparws6w56.streamlit.app/

Built by Michael Spring.

## What it does

Three published models do the scoring:

| Model | Question it answers | Output |
|---|---|---|
| Altman Z-Score (Altman, 1968) | How close is the company to financial distress? | Safe / Grey / Distress |
| Piotroski F-Score (Piotroski, 2000) | How strong are the fundamentals, year over year? | 0 to 9 |
| Beneish M-Score (Beneish, 1999) | Do the earnings show manipulation red flags? | Clean / Possible manipulation |

The app combines them into one plain verdict, for example "Watch, with earnings-quality red flags," and lets you open each model to see the underlying ratios and which signals passed or failed.

Beyond the raw scores:

A why section reads the scores back in plain language and names which components are driving each one. It is computed only from each model's own inputs, so it stays reproducible and never invents anything. For a company that looks distressed, it might point out that the Z-Score is held down by thin working capital while the business itself scores well on Piotroski.

Sector benchmarking shows where a company's scores land against its sector peers, using the median and quartiles from a snapshot of the S&P 500. A high Z-Score means more once you can see it sits in the top quartile of its sector.

An M&A target screener is a separate view that scans the S&P 500 snapshot for companies matching an acquisition profile. It runs in two modes: value and distress targets (operationally strong but cheap and under balance-sheet stress, the classic strong-business, weak-balance-sheet buyout candidate) and strategic targets (strong, clean operators in a chosen sector). It is a screen for further diligence, not a prediction that any deal will happen, and it filters out financials and distorted valuation data so glitches do not surface as fake bargains.

## Three ways to enter a company

1. Live ticker pulls the two most recent annual statements from Yahoo Finance through yfinance.
2. Sample companies are three built-in profiles (healthy, distressed, earnings red flags) so the app always demos cleanly with no internet. They are clearly labeled as sample data.
3. Manual entry lets you type two years of figures yourself, which also works for private companies.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL it prints, usually http://localhost:8501.

## Tests

The scoring, commentary, benchmarking, and screening logic is pure and unit-tested. Each file runs on its own:

```bash
python3 tests/test_models.py
python3 tests/test_commentary.py
python3 tests/test_benchmark.py
python3 tests/test_screener.py
```

The math in models.py is checked against hand-computed values, including a known non-manipulator baseline and a constructed manipulator case.

## Project structure

```
financial-health-screener/
├── app.py                      # Streamlit UI
├── models.py                   # the three scoring models (pure, testable)
├── commentary.py               # the why engine: component attribution
├── benchmark.py                # sector benchmarking against the snapshot
├── screener.py                 # M&A target screener
├── data.py                     # yfinance fetch, sample presets, manual entry
├── build_universe.py           # one-time builder for the S&P 500 snapshot
├── data/
│   └── universe_snapshot.csv   # precomputed S&P 500 scores for benchmarking and screening
├── tests/
├── requirements.txt
└── .streamlit/config.toml      # theme
```

## Notes and limitations

yfinance is an unofficial Yahoo Finance wrapper, so field coverage varies by company. The Beneish M-Score needs detailed line items that are not always reported, and the app shows "Not enough data" rather than crashing when they are missing.

The Altman model here is the original public-firm Z-Score. It does not apply to banks and insurers, which lack a working-capital structure, and the app says so instead of forcing a number. For asset-light companies the Z-Score can read very high, which reflects a light balance sheet rather than extra safety.

The sector benchmarking and M&A screener read from a snapshot of the S&P 500 taken on a fixed date, so those scores are point-in-time rather than live.

This is an educational screening tool, not investment advice. The models are starting points for diligence, not verdicts.

## References

- Altman, E. (1968). Financial Ratios, Discriminant Analysis and the Prediction of Corporate Bankruptcy. Journal of Finance.
- Piotroski, J. (2000). Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers. Journal of Accounting Research.
- Beneish, M. (1999). The Detection of Earnings Manipulation. Financial Analysts Journal.
