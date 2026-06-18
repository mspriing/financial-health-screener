You are working in this repo: a Streamlit app called "Financial Health & Red-Flag Screener."
Files: app.py (the UI), models.py (the three scoring models), data.py (data layer / presets / live fetch).

GOAL
Redesign ONLY the visual presentation in app.py so it looks premium, sleek, and distinctly
"finance" — a polished tool someone actually wants to click into. Right now it looks plain,
generic, and obviously AI-generated. Make it feel intentional and expensive, with tasteful
micro-interactions. Keep it professional and restrained — this is finance, not a game.

HARD CONSTRAINTS
- Do NOT change any logic in models.py or data.py, and do NOT change what the app computes or
  which features exist (Sample / Live ticker / Manual modes, the three scores, the verdict, the
  breakdown expanders, the disclaimer, the bank/index/not-found messages). Presentation only.
- Keep it deployable on Streamlit Community Cloud — no paid or unavailable dependencies. Use
  Streamlit plus injected CSS/HTML/JS via st.markdown(unsafe_allow_html=True) and/or
  st.components.v1.html. Any JS must be self-contained.
- Keep all tests passing: python3 tests/test_models.py, tests/test_robustness.py, tests/smoke_app.py.
- Remove ALL emojis. Where an icon helps, use clean inline SVG or a refined typographic treatment.
- Maintain strong contrast and readability. Don't break the layout on a narrow window.

AESTHETIC DIRECTION  (default to a refined DARK fintech theme; define the palette as CSS
variables so it can be flipped to light easily)
- Background: near-black / deep charcoal (e.g. #0B0E14 base, #11151C). Card surfaces slightly
  elevated (#161B24). Hairline borders (rgba(255,255,255,0.08)).
- Text: near-white primary (#E6EAF0), muted secondary (#8A93A3).
- ONE confident accent used sparingly (pick a refined cool tone, e.g. teal #36D5C4 or electric
  blue #4C8DFF). Semantic colors — green = healthy/clean, amber = watch, red = distress/
  manipulation — but sophisticated, muted versions, not primary RGB.
- Typography: load a modern Google Font via @import (e.g. Space Grotesk or Inter for headings,
  Inter for body). Use tabular / lining numerals so all figures align. Make the score numbers
  large and confident.
- Spacing & shape: generous whitespace, consistent 8px rhythm, ~12–16px card radii, soft layered
  shadows (subtle, not heavy).
- Hide Streamlit chrome: hide the top header / hamburger menu and the "Made with Streamlit"
  footer via CSS.

ELEMENTS TO BUILD
- A real header/hero: product name in a strong type treatment, a one-line tagline, and the
  disclaimer rendered as elegant fine print.
- Verdict banner: make this the hero result — large, with the two status indicators (Health,
  Earnings) as refined pills (no emoji; a small SVG dot/shape + label), plus the one-line summary.
- Score cards: three elevated cards (Altman Z, Piotroski F, Beneish M), each with the big number,
  a labeled status pill, the threshold caption, and a subtle accent. Add a thin gauge/progress bar
  showing where the value falls in its range.
- Breakdowns: keep the expanders, style them cleanly; style tables with subtle row separation and
  right-aligned tabular numbers.
- Sidebar: refine the controls to match the theme.

MICRO-INTERACTIONS  (subtle and professional — no bounce, no confetti)
- Smooth hover transitions on cards/pills/buttons (e.g. translateY(-2px), border/shadow brighten,
  ~150–200ms ease).
- Gentle staggered entrance: cards/sections fade + rise in on load.
- Animated count-up on the score numbers on load (~600ms).
- Optional and tasteful: a soft, low-opacity radial glow that follows the cursor on the hero/
  background, and a clean focus ring on inputs. Keep it performant.

PROCESS
1. First, restyle within Streamlit using one cohesive injected stylesheet plus minimal HTML for
   the cards/pills. This preserves the simple one-command deploy.
2. If you conclude a genuinely premium, animated result is NOT achievable within Streamlit's
   limits, STOP and propose replacing the front-end with a custom HTML/CSS/JS frontend served by a
   small FastAPI/Flask backend that reuses models.py and data.py. Explain the tradeoff (better
   design/animation vs. a more complex deploy) and wait for my confirmation before doing that.
3. After changes: run the app, run all three test files, and confirm nothing broke. Then give me a
   short summary of what you changed.

The bar is "looks like a real, well-designed fintech product" — sleek and expensive, not flashy.
