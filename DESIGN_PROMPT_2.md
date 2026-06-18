Follow-up design fixes for this Streamlit app (financial-health-screener). The first redesign is
close and the direction is right: dark, sleek, premium. Keep that feel. These are targeted fixes.

SAME HARD RULES AS BEFORE
- Presentation only. Do NOT change logic in models.py or data.py, or what the app computes, or
  which features exist.
- Keep it deployable on Streamlit Community Cloud. Streamlit + injected CSS/HTML/JS only.
- Keep all tests passing: tests/test_models.py, tests/test_robustness.py, tests/smoke_app.py.
- No emojis anywhere.

FIXES

1) The cursor glow breaks after switching modes.
The cursor-follow glow only works on the first view. After the user switches the data-source mode
it stops working and does not come back. This is because Streamlit rebuilds the DOM on every
interaction and the injected JavaScript is not re-attached. Make the glow re-initialize on every
rerun and bind it to a stable container so it works continuously across every mode and every
rerun. If you cannot make it reliable across reruns, remove it completely. A glow that flickers off
looks worse than no glow at all.

2) The collapsed sidebar cannot be reopened.
Hiding the Streamlit header also hid the button that reopens the sidebar, so once the user
collapses it there is no way back. There must ALWAYS be a visible, styled way to reopen it. The
cleaner fix: move the three data-source modes (Sample company, Live ticker, Manual entry) out of
the collapsible sidebar and into an always-visible, styled segmented control or tab bar at the top
of the main content area. Either way, never leave a dead-end where a control disappears with no way
to bring it back.

3) Improve the navigation.
Make switching modes feel intentional and obvious: a clean segmented control or tab bar with a
clear active state in the accent color, smooth transitions, and an obvious visual order so the
user always knows where they are and what to do next.

4) It feels too dark and gloomy. Add life and color, but keep it dark.
Do not switch to a light theme. Keep the dark, expensive feel, but lift it so it reads comfortably
without the user raising their screen brightness:
- Raise the base background from near-black to a softer dark (around #12161F) and make card
  surfaces a little lighter (around #1C2230). Make borders slightly more visible.
- Raise text contrast so secondary text is clearly readable (around #A7B0C0, not a dim gray).
- Use the accent color more, and on purpose: active navigation, the hero, a top accent or small
  inline SVG icon on each card, the gauge fills, links, and hover states. A subtle accent gradient
  in the hero is welcome.
- Make the green / amber / red status colors more present and legible: larger, clearer pills, and
  give the verdict a colored accent (a colored left border or a soft glow) so the headline result
  is the obvious focal point.
- Stay disciplined: one accent color plus the three semantic colors. The goal is more life, not a
  rainbow. It must still look expensive.

5) Proportion and hierarchy.
Make everything feel proportionate and guide the eye. The verdict is the hero (largest, most
emphasis), the three score cards come second, the breakdowns third. Use one consistent type and
size scale. Do not let any single element be oversized or cramped. The user should know instantly
where to look.

6) More motion on load and scroll.
Add tasteful scroll-triggered reveal animations (IntersectionObserver) so sections and cards fade
and rise in as they scroll into view, on top of the on-load entrance and the score count-up. Keep
it subtle. Make sure any JavaScript animation re-initializes across Streamlit reruns so it keeps
working after a mode switch (same root cause as fix 1).

7) Change the typeface.
Switch to a cleaner, more distinctive but still professional font. Use a Google Fonts pairing such
as headings in "Sora" or "Plus Jakarta Sans" with body in "Inter", and use tabular (lining)
numerals for all figures so numbers line up. Avoid generic system defaults.

8) Make all written copy sound human, not AI-generated.
Rewrite every piece of user-facing text (the tagline, the disclaimer, the bank / index / not-found
messages, the captions, the "what these models are" section, and any other sentences) so it reads
naturally and plainly:
- Remove every em dash and en dash used as punctuation. Use periods, commas, or parentheses
  instead.
- Avoid AI-tell phrasing: no "not just X but Y", no three-item lists for rhythm, no inflated words
  (robust, seamless, delve, leverage, comprehensive, and similar).
- Keep sentences short and direct, the way a person actually writes. Do not change the meaning of
  the disclaimer or the bank / index / not-found explanations. Just make them sound natural.

PROCESS
- Change app.py, the injected CSS, and .streamlit/config.toml (theme palette) only. Do not touch
  models.py or data.py.
- When done, run the app and all three test files, confirm nothing broke, and give me a short,
  plain-English summary of what changed.
