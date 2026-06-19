"""
Financial Health & Red-Flag Screener — Streamlit app.

Type in a company (or pick a sample), and three published finance models read the
financial statements and return one call:
  • Is it financially healthy, on watch, or distressed?   (Altman Z + Piotroski F)
  • Do the earnings look clean, or are there manipulation red flags?  (Beneish M)

Run locally:   python3 -m streamlit run app.py
"""
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from data import PRESETS, blank_payload, fetch_live, run_models, LINE_ITEMS

st.set_page_config(page_title="Financial Health & Red-Flag Screener",
                   layout="wide", initial_sidebar_state="collapsed")

# ============================================================================
# THEME  — one cohesive injected stylesheet (lifted dark fintech)
# ============================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@500;600;700;800&display=swap');

:root{
  --bg:#12161F; --bg-2:#161B26; --surface:#1C2230; --surface-2:#232B3C;
  --border:rgba(255,255,255,.10); --border-2:rgba(255,255,255,.18);
  --text:#E6EAF0; --muted:#A7B0C0; --faint:#8A95A6;
  --accent:#36D5C4; --accent-soft:rgba(54,213,196,.14); --accent-line:rgba(54,213,196,.40);
  --green:#4FD08F; --green-bg:rgba(79,208,143,.15); --green-trk:rgba(79,208,143,.30);
  --amber:#EBB454; --amber-bg:rgba(235,180,84,.15); --amber-trk:rgba(235,180,84,.30);
  --red:#F0697B;  --red-bg:rgba(240,105,123,.16);  --red-trk:rgba(240,105,123,.30);
  --gray:#9099A8; --gray-bg:rgba(144,153,168,.14);
  --shadow:0 1px 2px rgba(0,0,0,.35),0 14px 34px -14px rgba(0,0,0,.55);
  --r:14px;
  --ease:cubic-bezier(.2,.7,.2,1);
}

/* ---- canvas ---- */
.stApp{
  background:
    radial-gradient(1150px 640px at 80% -10%, rgba(54,213,196,.10), transparent 60%),
    radial-gradient(900px 600px at 5% 2%, rgba(76,141,255,.06), transparent 55%),
    var(--bg);
  color:var(--text);
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  font-feature-settings:"tnum" 1,"lnum" 1;
}
.block-container{max-width:1140px;padding-top:2.4rem;padding-bottom:4rem;}

/* ---- hide streamlit chrome (sidebar is unused, so nothing is trapped behind it) ---- */
header[data-testid="stHeader"]{display:none;}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stStatusWidget"]{display:none !important;}
[data-testid="stHeadingWithActionElements"]:has(h1){display:none !important;}
h1{display:none;}

/* ---- cursor glow (injected into body by the helper script, kept on a stable node) ---- */
#fhs-glow{
  position:fixed;top:0;left:0;width:540px;height:540px;margin:-270px 0 0 -270px;
  border-radius:50%;pointer-events:none;z-index:0;
  background:radial-gradient(circle,rgba(54,213,196,.14),rgba(54,213,196,0) 62%);
  will-change:transform;transition:opacity .4s ease;opacity:0;
}

/* ---- typography ---- */
h2,h3,h4{font-family:'Sora','Inter',sans-serif;color:var(--text);letter-spacing:-.01em;}
[data-testid="stMarkdownContainer"] p{color:var(--text);}

/* ---- hero ---- */
.hero{position:relative;z-index:1;margin:.2rem 0 1.4rem;}
.hero-eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:.72rem;font-weight:600;
  letter-spacing:.16em;text-transform:uppercase;color:var(--accent);
  padding:6px 12px;border:1px solid var(--accent-line);border-radius:999px;
  background:var(--accent-soft);margin-bottom:18px;}
.hero h1.title{display:block !important;font-family:'Sora',sans-serif;font-weight:700;
  font-size:clamp(2.1rem,4.6vw,3.2rem);line-height:1.04;letter-spacing:-.025em;margin:0;color:var(--text);}
.hero h1.title .accent{background:linear-gradient(120deg,var(--accent),#7FE9DD);
  -webkit-background-clip:text;background-clip:text;color:transparent;}
.hero .tagline{color:var(--muted);font-size:1.07rem;line-height:1.62;max-width:64ch;margin:15px 0 0;}
.hero .tagline strong{color:var(--text);font-weight:600;}
.fineprint{margin-top:20px;padding:14px 17px;border:1px solid var(--border);border-radius:11px;
  background:rgba(255,255,255,.025);color:var(--muted);font-size:.82rem;line-height:1.62;max-width:92ch;}
.fineprint .lbl{color:var(--text);font-weight:600;}

/* ---- data-source nav (radio styled as a segmented control, always visible) ---- */
.navlabel{font-family:'Sora',sans-serif;font-size:.74rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.16em;color:var(--faint);margin:6px 0 6px;}
/* helper line naming the three as switchable modes */
.navhelp{color:var(--muted);font-size:.85rem;line-height:1.5;margin:0 0 12px;max-width:62ch;}
[data-testid="stRadio"] [role="radiogroup"]{display:inline-flex;flex-direction:row;flex-wrap:wrap;
  gap:6px;background:var(--bg-2);border:1px solid var(--border);border-radius:13px;padding:5px;}
/* every segment reads as a button at all times: visible surface + border + readable text */
[data-testid="stRadio"] [role="radiogroup"] label{margin:0 !important;padding:9px 18px !important;
  display:inline-flex;align-items:center;gap:8px;min-height:44px;
  border-radius:9px;border:1px solid var(--border);background:var(--surface);cursor:pointer;
  transition:color .18s var(--ease),background .18s var(--ease),border-color .18s var(--ease);}
/* hide the native radio dot so each option reads as a segment button */
[data-testid="stRadio"] [role="radiogroup"] label > div:first-child{display:none !important;}
[data-testid="stRadio"] [role="radiogroup"] label p{color:var(--text);font-weight:600 !important;
  transition:color .18s var(--ease);}
/* leading glyph per mode, in source order (Sample / Live / Manual) */
[data-testid="stRadio"] [role="radiogroup"] label::before{font-size:.95rem;line-height:1;opacity:.85;}
[data-testid="stRadio"] [role="radiogroup"] label:nth-of-type(1)::before{content:"\\25A6";}
[data-testid="stRadio"] [role="radiogroup"] label:nth-of-type(2)::before{content:"\\25C9";}
[data-testid="stRadio"] [role="radiogroup"] label:nth-of-type(3)::before{content:"\\270E";}
[data-testid="stRadio"] [role="radiogroup"] label:hover{background:var(--surface-2);border-color:var(--border-2);}
/* selected tab: solid accent fill so the active mode is unmistakable */
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked){
  background:var(--accent);border-color:var(--accent);
  box-shadow:0 2px 14px -4px rgba(54,213,196,.55);}
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) p{color:#04201C !important;}
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked)::before{opacity:1;color:#04201C;}
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked):hover{background:var(--accent);}
.panelcap{color:var(--muted);font-size:.84rem;line-height:1.55;margin:8px 0 2px;}
/* sample-mode callout — only shown while viewing built-in sample data */
.samplenote{display:flex;align-items:center;gap:9px;margin:0 0 14px;padding:9px 14px;
  border:1px solid var(--accent-line);border-left:3px solid var(--accent);border-radius:10px;
  background:var(--accent-soft);color:var(--text);font-size:.86rem;line-height:1.5;}
.samplenote::before{content:"\\25A6";color:var(--accent);font-size:1rem;line-height:1;}
.samplenote strong{font-weight:600;}

/* ---- card primitive ---- */
.card{position:relative;z-index:1;background:linear-gradient(180deg,var(--surface),var(--bg-2));
  border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--shadow);
  transition:transform .18s var(--ease),border-color .18s var(--ease),box-shadow .18s var(--ease);}
.card:hover{transform:translateY(-2px);border-color:var(--border-2);
  box-shadow:0 1px 2px rgba(0,0,0,.4),0 20px 44px -16px rgba(0,0,0,.6);}

/* ---- verdict banner (the hero of the results) ---- */
.verdict{padding:30px 32px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:24px;
  align-items:center;justify-content:space-between;border-left:3px solid var(--border-2);
  background:
    radial-gradient(120% 150% at 0% 0%, rgba(54,213,196,.09), transparent 55%),
    linear-gradient(180deg,var(--surface),var(--bg-2));}
.verdict.v-green{border-left-color:var(--green);box-shadow:0 1px 2px rgba(0,0,0,.4),0 26px 64px -32px rgba(79,208,143,.5);}
.verdict.v-amber{border-left-color:var(--amber);box-shadow:0 1px 2px rgba(0,0,0,.4),0 26px 64px -32px rgba(235,180,84,.46);}
.verdict.v-red{border-left-color:var(--red);box-shadow:0 1px 2px rgba(0,0,0,.4),0 26px 64px -32px rgba(240,105,123,.5);}
.verdict.v-gray{border-left-color:var(--gray);}
.verdict .vname{font-family:'Sora',sans-serif;font-size:1.9rem;font-weight:700;
  letter-spacing:-.025em;margin:0 0 14px;}
.verdict .vmeta{color:var(--faint);font-size:.8rem;margin-top:15px;letter-spacing:.01em;}
.verdict .vcall{font-size:1.06rem;line-height:1.55;color:var(--muted);max-width:42ch;}
.verdict .vcall .lead{display:block;font-size:.72rem;font-weight:600;letter-spacing:.14em;
  text-transform:uppercase;color:var(--accent);margin-bottom:7px;}
.verdict .vcall strong{color:var(--text);font-weight:600;}
.vleft{min-width:240px;flex:1 1 320px;}
.vright{flex:0 1 360px;border-left:1px solid var(--border);padding-left:24px;}

/* ---- pills ---- */
.pillrow{display:flex;flex-wrap:wrap;gap:9px;}
.pill{display:inline-flex;align-items:center;gap:8px;padding:7px 15px 7px 12px;border-radius:999px;
  font-size:.86rem;font-weight:600;letter-spacing:.01em;border:1px solid transparent;line-height:1;
  transition:transform .16s var(--ease),filter .16s var(--ease);}
.pill:hover{transform:translateY(-1px);filter:brightness(1.08);}
.pill .lbl{opacity:.78;font-weight:500;margin-right:-2px;}
.pill svg{flex:0 0 auto;}
.pill.green{background:var(--green-bg);border-color:rgba(79,208,143,.36);color:var(--green);}
.pill.amber{background:var(--amber-bg);border-color:rgba(235,180,84,.36);color:var(--amber);}
.pill.red{background:var(--red-bg);border-color:rgba(240,105,123,.38);color:var(--red);}
.pill.gray{background:var(--gray-bg);border-color:rgba(144,153,168,.34);color:var(--gray);}

/* ---- score cards ---- */
.score{padding:22px 22px 20px;height:100%;}
.score .icon{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;
  border-radius:10px;background:var(--accent-soft);border:1px solid var(--accent-line);
  color:var(--accent);margin-bottom:14px;}
.score .stitle{font-size:.98rem;font-weight:600;color:var(--text);letter-spacing:-.01em;}
.score .ssub{font-size:.74rem;color:var(--faint);text-transform:uppercase;letter-spacing:.12em;
  font-weight:600;margin-top:3px;}
.score .num{font-family:'Sora',sans-serif;font-variant-numeric:tabular-nums lining-nums;
  font-size:3rem;font-weight:700;line-height:1;letter-spacing:-.03em;color:var(--text);
  margin:16px 0 4px;}
.score .num.na{color:var(--faint);font-size:1.5rem;font-weight:600;}
.score .num .suffix{font-size:1.3rem;color:var(--muted);font-weight:500;letter-spacing:0;}
.score .cap{color:var(--muted);font-size:.79rem;line-height:1.5;margin-top:13px;
  font-variant-numeric:tabular-nums;}
.score .pillrow{margin-top:4px;}
/* equal-height score cards: stretch the column chain so all three match the tallest */
[data-testid="stHorizontalBlock"]:has(.card.score){align-items:stretch;}
[data-testid="stColumn"]:has(.card.score){display:flex;flex-direction:column;}
[data-testid="stColumn"]:has(.card.score) [data-testid="stVerticalBlock"],
[data-testid="stColumn"]:has(.card.score) [data-testid="stElementContainer"],
[data-testid="stColumn"]:has(.card.score) [data-testid="stMarkdown"],
[data-testid="stColumn"]:has(.card.score) [data-testid="stMarkdownContainer"]{
  display:flex;flex-direction:column;flex:1 1 auto;height:100%;}

/* ---- gauge ---- */
.gauge{position:relative;height:6px;border-radius:6px;margin:16px 0 4px;overflow:visible;
  background:rgba(255,255,255,.07);}
.gauge .trk{position:absolute;inset:0;border-radius:6px;animation:trk-in .6s var(--ease) both;}
.gauge .ptr{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;
  transform:translate(-50%,-50%);background:var(--text);
  box-shadow:0 0 0 3px var(--bg-2),0 2px 6px rgba(0,0,0,.5);animation:ptr-in .55s var(--ease) both;}
@keyframes trk-in{from{clip-path:inset(0 100% 0 0);}to{clip-path:inset(0 0 0 0);}}
@keyframes ptr-in{from{opacity:0;transform:translate(-50%,-50%) scale(.4);}to{opacity:1;}}

/* ---- entrance reveal (on load) + scroll reveal (IntersectionObserver) ---- */
.reveal{animation:rise .6s var(--ease) both;animation-delay:var(--d,0s);}
@keyframes rise{from{opacity:0;transform:translateY(13px);}to{opacity:1;transform:none;}}
.scroll-reveal.sr-ready{opacity:0;transform:translateY(22px);}
.scroll-reveal.sr-in{opacity:1;transform:none;
  transition:opacity .7s var(--ease),transform .7s var(--ease);transition-delay:var(--d,0s);}

/* ---- section label ---- */
.seclabel{display:flex;align-items:center;gap:12px;margin:32px 0 14px;}
.seclabel .t{font-family:'Sora',sans-serif;font-size:.78rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.16em;color:var(--muted);}
.seclabel .ln{flex:1;height:1px;background:linear-gradient(90deg,var(--border-2),transparent);}

/* ---- note card ---- */
.note{padding:17px 19px;border-radius:12px;border:1px solid var(--border);
  background:rgba(54,213,196,.05);border-left:2px solid var(--accent-line);
  color:var(--muted);font-size:.9rem;line-height:1.62;}
.note .lbl{color:var(--text);font-weight:600;}
.note a, .note strong{color:var(--text);}
.note.empty{border-left-color:var(--accent-line);background:rgba(255,255,255,.025);}

/* ---- breakdown tables ---- */
.fin-table{width:100%;border-collapse:collapse;font-size:.87rem;}
.fin-table th{text-align:left;color:var(--faint);font-weight:600;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.08em;padding:0 0 9px;border-bottom:1px solid var(--border);}
.fin-table th.r,.fin-table td.r{text-align:right;font-variant-numeric:tabular-nums lining-nums;}
.fin-table td{padding:10px 0;border-bottom:1px solid var(--border);color:var(--text);}
.fin-table tr:last-child td{border-bottom:none;}
.fin-table tr:hover td{color:#fff;}
.fin-table td.label{color:var(--muted);}
.tag-pass{color:var(--green);font-weight:600;}
.tag-fail{color:var(--faint);font-weight:600;}
.formula{margin-top:12px;color:var(--faint);font-size:.79rem;line-height:1.5;
  font-variant-numeric:tabular-nums;}

/* ---- inputs / controls ---- */
.stTextInput input,.stNumberInput input,[data-baseweb="select"]>div{
  background:var(--surface) !important;border:1px solid var(--border) !important;
  border-radius:9px !important;color:var(--text) !important;
  font-variant-numeric:tabular-nums lining-nums;
  transition:border-color .15s var(--ease),box-shadow .15s var(--ease);}
.stTextInput input:focus,.stNumberInput input:focus{
  border-color:var(--accent-line) !important;box-shadow:0 0 0 3px var(--accent-soft) !important;}
[data-baseweb="select"]>div:focus-within{border-color:var(--accent-line) !important;box-shadow:0 0 0 3px var(--accent-soft) !important;}
.stButton button,.stFormSubmitButton button{border-radius:9px !important;font-weight:600 !important;
  border:1px solid var(--border-2) !important;transition:transform .16s var(--ease),filter .16s var(--ease) !important;}
.stButton button:hover,.stFormSubmitButton button:hover{transform:translateY(-1px);filter:brightness(1.06);}
.stButton button[kind="primary"],.stFormSubmitButton button[kind="primary"]{
  background:var(--accent) !important;color:#04201C !important;border-color:transparent !important;}
a{color:var(--accent);}

/* ---- visible keyboard focus rings (don't strip focus without a replacement) ---- */
.stButton button:focus-visible,.stFormSubmitButton button:focus-visible{
  outline:none !important;box-shadow:0 0 0 1px var(--accent-line),0 0 0 4px var(--accent-soft) !important;}
[data-testid="stRadio"] [role="radiogroup"] label:has(input:focus-visible){
  outline:2px solid var(--accent);outline-offset:2px;background:rgba(255,255,255,.05);}
[data-testid="stExpander"] summary:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;border-radius:8px;}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px;}

/* ---- expanders ---- */
[data-testid="stExpander"]{border:1px solid var(--border) !important;border-radius:12px !important;
  background:var(--bg-2);margin-bottom:10px;overflow:hidden;transition:border-color .16s var(--ease);}
[data-testid="stExpander"]:hover{border-color:var(--border-2) !important;}
[data-testid="stExpander"] summary{font-weight:600;color:var(--text);}
[data-testid="stExpander"] summary:hover{color:var(--accent);}

/* ---- alerts to theme ---- */
[data-testid="stAlert"]{border-radius:12px;border:1px solid var(--border);}

/* ---- collapse the helper component iframe ---- */
[data-testid="stCustomComponentV1"]{height:0 !important;}
.stElementContainer:has(> iframe[title="streamlit_component"]){height:0;min-height:0;margin:0;}

@media (prefers-reduced-motion: reduce){
  *{animation:none !important;transition:none !important;}
  #fhs-glow{display:none;}
}
@media (max-width:640px){
  .vright{border-left:none;padding-left:0;border-top:1px solid var(--border);padding-top:16px;}
  .score .num{font-size:2.6rem;}
  /* stack the data-source segments as full-width, tappable vertical rows */
  [data-testid="stRadio"] [role="radiogroup"]{display:flex;flex-direction:column;width:100%;gap:8px;}
  [data-testid="stRadio"] [role="radiogroup"] label{width:100%;justify-content:center;
    min-height:48px;padding:13px 18px !important;}
}
</style>
""", unsafe_allow_html=True)


# ============================================================================
# PRESENTATION HELPERS  (pure markup — no change to computed results)
# ============================================================================
HEALTH_TONE = {"Healthy": "green", "Watch": "amber", "Distressed": "red", "Unknown": "gray"}
INTEG_TONE = {"Clean": "green", "Possible manipulation": "red", "Not enough data": "gray"}

_DOT = ('<svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true">'
        '<circle cx="4.5" cy="4.5" r="4.5" fill="currentColor"/></svg>')
_RING = ('<svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true">'
         '<circle cx="4.5" cy="4.5" r="3.4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>')

# small inline accent icons, one per model card
_ICON_ALTMAN = ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
                'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M3 12h4l2 5 4-12 2 7h6"/></svg>')
_ICON_PIO = ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
             '<line x1="5" y1="20" x2="5" y2="13"/><line x1="12" y1="20" x2="12" y2="6"/>'
             '<line x1="19" y1="20" x2="19" y2="10"/></svg>')
_ICON_BEN = ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M4 21V4M4 4h11l-2 4 2 4H4"/></svg>')


def pill(text, tone, lead=None, ring=False):
    glyph = _RING if ring else _DOT
    lead_html = f'<span class="lbl">{lead}</span>' if lead else ""
    return f'<span class="pill {tone}">{glyph}{lead_html}{text}</span>'


def gauge(fraction, segments):
    """fraction: 0..1 pointer position. segments: list of (cutoff_pct, css_var)."""
    f = max(0.0, min(1.0, fraction)) * 100
    stops = []
    prev = 0
    for cut, var in segments:
        stops.append(f"var({var}) {prev}%,var({var}) {cut}%")
        prev = cut
    grad = "linear-gradient(90deg," + ",".join(stops) + ")"
    return (f'<div class="gauge"><div class="trk" style="background:{grad}"></div>'
            f'<div class="ptr" style="left:{f:.1f}%"></div></div>')


def num(value, dec, suffix=""):
    sfx = f'<span class="suffix">{suffix}</span>' if suffix else ""
    return (f'<div class="num"><span class="js-countup" data-target="{value}" '
            f'data-dec="{dec}">{value:.{dec}f}</span>{sfx}</div>')


def num_na():
    return '<div class="num na">N/A</div>'


def score_card(icon, stitle, ssub, body, delay):
    return (f'<div class="card score scroll-reveal" style="--d:{delay}s">'
            f'<div class="icon">{icon}</div>'
            f'<div class="stitle">{stitle}</div><div class="ssub">{ssub}</div>'
            f'{body}</div>')


def fin_table(headers, rows):
    head = "".join(f'<th class="{c}">{h}</th>' for h, c in headers)
    body = ""
    for cells in rows:
        tds = "".join(f'<td class="{c}">{v}</td>' for v, c in cells)
        body += f"<tr>{tds}</tr>"
    return f'<table class="fin-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# Kept as a Streamlit title element only — hidden via CSS; the styled hero below is
# the real header. (Required so the page remains a labelled document landmark.)
st.title("Financial Health & Red-Flag Screener")

# ============================ HERO ==========================================
st.markdown("""
<div class="hero reveal">
  <span class="hero-eyebrow">First-pass financial screen</span>
  <h1 class="title">Financial Health <span class="accent">&amp;</span> Red-Flag Screener</h1>
  <p class="tagline">Three published finance models read a company's statements and give one clear
  read. They score <strong>distress risk</strong> and <strong>fundamental strength</strong>, then
  check the earnings for <strong>manipulation red flags</strong>. It is the quick first look an
  analyst runs before digging deeper.</p>
  <div class="fineprint"><span class="lbl">Disclaimer.</span> This is an educational tool, not
  investment advice. These models are probabilistic screens. They are <em>not</em> proof of
  financial distress or fraud, and the results can be incomplete or wrong. Always check against a
  company's primary filings. Use it at your own discretion.</div>
</div>
""", unsafe_allow_html=True)

# ============================ DATA SOURCE NAV ===============================
# Always-visible segmented control in the main content area (no collapsible sidebar,
# so a control can never disappear with no way to bring it back).
st.markdown('<div class="navlabel reveal" style="--d:.04s">Data source</div>'
            '<div class="navhelp reveal" style="--d:.04s">Switch how you load a company — a built-in '
            'sample, a live ticker from Yahoo Finance, or your own numbers.</div>',
            unsafe_allow_html=True)
SOURCES = ["Sample company", "Live ticker", "Manual entry"]
source = st.radio("Data source", SOURCES, horizontal=True,
                  label_visibility="collapsed", key="source")

payload, err = None, None

if source == "Sample company":
    choice = st.selectbox("Pick a sample", list(PRESETS.keys()), label_visibility="collapsed")
    payload = PRESETS[choice]
    st.markdown('<div class="panelcap">Sample figures, built to show the range these models can '
                'produce. Not a real company\'s filings.</div>', unsafe_allow_html=True)

elif source == "Live ticker":
    tcol, _ = st.columns([1, 2])
    with tcol:
        ticker = st.text_input("Ticker", value="AAPL", label_visibility="collapsed").strip().upper()
        if st.button("Fetch financials", type="primary"):
            with st.spinner(f"Pulling {ticker} from Yahoo Finance..."):
                try:
                    st.session_state["live_payload"] = fetch_live(ticker)
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                    st.session_state.pop("live_payload", None)
    payload = st.session_state.get("live_payload")
    st.markdown('<div class="panelcap">Pulls annual statements from Yahoo Finance. Works best for '
                'large non-financial U.S. companies like AAPL, MSFT, KO, or GOOGL. Banks and private '
                'firms have limited data.</div>', unsafe_allow_html=True)

else:  # manual entry
    st.markdown('<div class="panelcap">Enter two years of figures. Any unit is fine as long as you '
                'keep it consistent.</div>', unsafe_allow_html=True)
    base = blank_payload()
    with st.form("manual"):
        st.subheader("Manual entry")
        mve = st.number_input("Market value of equity (market cap), current year",
                              value=0.0, step=100.0)
        cols = st.columns(2)
        labels = {
            "sales": "Revenue / Sales", "cogs": "Cost of goods sold",
            "receivables": "Accounts receivable", "current_assets": "Current assets",
            "current_liabilities": "Current liabilities", "ppe": "Net PP&E",
            "total_assets": "Total assets", "depreciation": "Depreciation & amort.",
            "sga": "SG&A expense", "long_term_debt": "Long-term debt",
            "net_income": "Net income", "cfo": "Operating cash flow",
            "retained_earnings": "Retained earnings", "ebit": "EBIT / operating income",
            "total_liabilities": "Total liabilities", "shares": "Shares outstanding",
        }
        with cols[0]:
            st.markdown("**Current year**")
            for k in LINE_ITEMS:
                base["curr"][k] = st.number_input(labels[k], value=0.0, step=10.0, key=f"c_{k}")
        with cols[1]:
            st.markdown("**Prior year**")
            for k in LINE_ITEMS:
                base["prior"][k] = st.number_input(labels[k], value=0.0, step=10.0, key=f"p_{k}")
        submitted = st.form_submit_button("Analyze", type="primary")
    if submitted:
        base["market_value_equity"] = mve
        payload = base

if err:
    st.warning(err)  # friendly explanation, not a red crash

if not payload:
    st.markdown(
        '<div class="note empty reveal" style="--d:.08s">Pick a sample company, fetch a live '
        'ticker, or enter your own figures above to run the screen.</div>',
        unsafe_allow_html=True)
    st.stop()

# ============================ RUN MODELS ====================================
altman, piotroski, beneish, verdict, notes = run_models(payload)
meta = payload["meta"]

# ============================ VERDICT BANNER ================================
health_tone = HEALTH_TONE.get(verdict["health"], "gray")
integ_tone = INTEG_TONE.get(verdict["integrity"], "gray")
health_pill = pill(verdict["health"], health_tone, lead="Health")
# ring marker for the earnings indicator so the two pills read as distinct categories
integ_pill = pill(verdict["integrity"], integ_tone, lead="Earnings", ring=True)

summary = {
    "Healthy": "strong balance sheet with improving fundamentals",
    "Watch": "mixed signals, some strength and some warning signs",
    "Distressed": "high distress risk",
    "Unknown": "not enough applicable data to make the call",
}.get(verdict["health"], "mixed signals")
integ = (", with <strong>earnings-quality red flags</strong>."
         if verdict["integrity"] == "Possible manipulation"
         else ", and the earnings look clean." if verdict["integrity"] == "Clean" else ".")

if source == "Sample company":
    st.markdown(
        '<div class="samplenote reveal" style="--d:.04s">You\'re viewing <strong>sample data</strong> '
        '— switch to <strong>Live ticker</strong> above to screen any public company.</div>',
        unsafe_allow_html=True)

st.markdown(f"""
<div class="card verdict v-{health_tone} reveal" style="--d:.05s">
  <div class="vleft">
    <p class="vname">{meta["name"]}</p>
    <div class="pillrow">{health_pill}{integ_pill}</div>
    <div class="vmeta">Source: {meta["source"]} &middot; {meta["period_curr"]} vs {meta["period_prior"]}</div>
  </div>
  <div class="vright vcall"><span class="lead">The call in one line</span>{summary}{integ}</div>
</div>
""", unsafe_allow_html=True)

# Honest note for banks / financial institutions
if meta.get("is_financial") or (altman is None and piotroski is None):
    st.markdown(
        '<div class="note reveal" style="--d:.1s"><span class="lbl">This looks like a bank or '
        'financial institution.</span> Banks don\'t split their balance sheet into current versus '
        'long-term assets. It is mostly loans, deposits, and securities, so working-capital '
        'measures like the Altman Z-Score don\'t mean much here. The Beneish M-Score leans on '
        'operating ratios, so it runs into the same wall. Analysts judge banks with other gauges '
        'instead, like capital ratios, non-performing loans, and net interest margin. So this is a '
        'real limit of these classic models, not a glitch. Try a non-financial company like '
        '<strong>AAPL, MSFT, or KO</strong>.</div>',
        unsafe_allow_html=True)

# ============================ THREE SCORES ==================================
st.markdown('<div class="seclabel scroll-reveal"><span class="t">The three models'
            '</span><span class="ln"></span></div>', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)

# --- Altman Z ---
with c1:
    if altman is None:
        body = (num_na() + f'<div class="pillrow">{pill("Not applicable", "gray")}</div>'
                + '<div class="cap">Not computable for this company.</div>')
    else:
        zt = {"Safe": "green", "Grey": "amber", "Distress": "red"}[altman.zone]
        g = gauge(altman.z / 6.0, [(30, "--red-trk"), (50, "--amber-trk"), (100, "--green-trk")])
        body = (num(altman.z, 2) + g + f'<div class="pillrow">{pill(altman.zone, zt)}</div>'
                + '<div class="cap">Safe above 2.99 &middot; Grey 1.81 to 2.99 &middot; Distress below 1.81</div>')
    st.markdown(score_card(_ICON_ALTMAN, "Altman Z-Score", "Distress risk", body, 0.06),
                unsafe_allow_html=True)

# --- Piotroski F ---
with c2:
    if piotroski is None:
        body = (num_na() + f'<div class="pillrow">{pill("Not applicable", "gray")}</div>'
                + '<div class="cap">Not computable for this company.</div>')
    else:
        ft = "green" if piotroski.score >= 7 else "amber" if piotroski.score >= 4 else "red"
        flbl = ("Strong" if piotroski.score >= 7 else "Middling" if piotroski.score >= 4 else "Weak")
        g = gauge(piotroski.score / 9.0, [(33, "--red-trk"), (78, "--amber-trk"), (100, "--green-trk")])
        body = (num(piotroski.score, 0, suffix="/ 9") + g
                + f'<div class="pillrow">{pill(flbl, ft)}</div>'
                + '<div class="cap">8 to 9 strong &middot; 4 to 7 middling &middot; 0 to 3 weak</div>')
    st.markdown(score_card(_ICON_PIO, "Piotroski F-Score", "Fundamental strength", body, 0.1),
                unsafe_allow_html=True)

# --- Beneish M ---
with c3:
    if beneish is None:
        body = (num_na() + f'<div class="pillrow">{pill("Not applicable", "gray")}</div>'
                + '<div class="cap">Needs two years of detailed statements.</div>')
    else:
        # map M onto a -3.5..0.5 window; clean (below -1.78) green, manipulation red
        frac = (beneish.m + 3.5) / 4.0
        g = gauge(frac, [(43, "--green-trk"), (100, "--red-trk")])
        bt = "red" if beneish.flag else "green"
        blbl = "Possible manipulation" if beneish.flag else "Clean"
        body = (num(beneish.m, 2) + g + f'<div class="pillrow">{pill(blbl, bt)}</div>'
                + '<div class="cap">Above &minus;1.78 means possible manipulation</div>')
    st.markdown(score_card(_ICON_BEN, "Beneish M-Score", "Earnings red flags", body, 0.14),
                unsafe_allow_html=True)

if notes:
    with st.expander("Why are some scores marked N/A?"):
        for k, v in notes.items():
            st.markdown(f"- **{k}:** {v}")

# ============================ BREAKDOWNS ====================================
st.markdown('<div class="seclabel scroll-reveal"><span class="t">Under the hood</span><span class="ln"></span></div>',
            unsafe_allow_html=True)

with st.expander("Altman Z-Score: components", expanded=False):
    if altman is None:
        st.info("Not computable for this company.")
    else:
        rows = [[(k, "label"), (f"{v}", "r")] for k, v in altman.components.items()]
        st.markdown(fin_table([("Ratio", ""), ("Value", "r")], rows), unsafe_allow_html=True)
        st.markdown('<div class="formula">Z = 1.2&middot;X1 + 1.4&middot;X2 + 3.3&middot;X3 + '
                    '0.6&middot;X4 + 1.0&middot;X5  (Altman, 1968).</div>', unsafe_allow_html=True)

with st.expander("Piotroski F-Score: the 9 signals", expanded=False):
    if piotroski is None:
        st.info("Not computable for this company.")
    else:
        rows = []
        for k, v in piotroski.signals.items():
            tag = ('<span class="tag-pass">Pass</span>' if v
                   else '<span class="tag-fail">Fail</span>')
            rows.append([(k, "label"), (tag, "r")])
        st.markdown(fin_table([("Signal", ""), ("Result", "r")], rows), unsafe_allow_html=True)
        st.markdown('<div class="formula">Each passed test = 1 point (Piotroski, 2000).</div>',
                    unsafe_allow_html=True)

with st.expander("Beneish M-Score: the 8 indices", expanded=False):
    if beneish is None:
        st.info("Not enough statement detail to compute the M-Score for this company.")
    else:
        names = {"DSRI": "Days Sales in Receivables", "GMI": "Gross Margin Index",
                 "AQI": "Asset Quality Index", "SGI": "Sales Growth Index",
                 "DEPI": "Depreciation Index", "SGAI": "SG&A Index",
                 "LVGI": "Leverage Index", "TATA": "Total Accruals / Total Assets"}
        rows = [[(f"<strong>{k}</strong> &middot; {names[k]}", "label"), (f"{v}", "r")]
                for k, v in beneish.indices.items()]
        st.markdown(fin_table([("Index", ""), ("Value", "r")], rows), unsafe_allow_html=True)
        st.markdown('<div class="formula">M = &minus;4.84 + 0.92&middot;DSRI + 0.528&middot;GMI + '
                    '0.404&middot;AQI + 0.892&middot;SGI + 0.115&middot;DEPI &minus; 0.172&middot;SGAI '
                    '+ 4.679&middot;TATA &minus; 0.327&middot;LVGI  (Beneish, 1999).</div>',
                    unsafe_allow_html=True)

with st.expander("What these models are (and what they aren't)"):
    st.markdown("""
- **Altman Z-Score (1968):** combines five balance-sheet and earnings ratios into one
  bankruptcy-risk number. Common in credit and distressed-debt work.
- **Piotroski F-Score (2000):** nine pass/fail tests covering profitability, leverage and
  liquidity, and operating efficiency. A value-investing staple for telling strong firms from weak
  ones.
- **Beneish M-Score (1999):** eight indices that flag the statistical signs of earnings
  manipulation. Cornell students used it to flag Enron before it collapsed.

These are screens, not verdicts. Treat them as a starting point for deeper work, and as a teaching
tool. Not investment advice. The Altman and Beneish models are built for **non-financial**
companies and don't fit banks or insurers.
    """)

st.markdown('<div class="seclabel scroll-reveal"><span class="ln"></span></div>', unsafe_allow_html=True)
st.markdown(
    '<div class="scroll-reveal" style="color:var(--faint);font-size:.8rem;line-height:1.6;">'
    'Built by Michael Spring &middot; Educational screen, not investment advice &middot; '
    'Models: Altman (1968), Piotroski (2000), Beneish (1999).</div>',
    unsafe_allow_html=True)

# ============================================================================
# MICRO-INTERACTIONS  — self-contained script injected into the parent document.
# Streamlit rebuilds the DOM and recreates this component's iframe on every rerun,
# which strands any listeners the previous iframe attached to the parent. So on each
# run we first tear down the previous wiring (stored on window.parent.__fhs), then
# re-attach fresh listeners from the live iframe. That keeps the count-up, the scroll
# reveals, and the cursor glow working continuously across every mode switch.
# Numbers already render their final value, so this only enhances; it never blocks.
# ============================================================================
components.html("""
<script>
(function(){
  const W = window.parent, D = W && W.document;
  if (!D) return;
  const reduce = W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // tear down anything wired by a previous rerun (its iframe is now dead)
  if (W.__fhs && typeof W.__fhs.cleanup === 'function'){ try{ W.__fhs.cleanup(); }catch(e){} }
  const teardown = [];
  W.__fhs = { cleanup: function(){ teardown.forEach(function(fn){ try{ fn(); }catch(e){} }); } };

  // --- animated count-up on score numbers ---
  function countUp(){
    D.querySelectorAll('.js-countup').forEach(function(el){
      if (el.dataset.done) return;
      el.dataset.done = '1';
      const target = parseFloat(el.dataset.target);
      const dec = parseInt(el.dataset.dec || '0', 10);
      if (isNaN(target)) return;
      if (reduce){ el.textContent = target.toFixed(dec); return; }
      const dur = 650, t0 = W.performance.now();
      function frame(now){
        let p = Math.min(1, (now - t0) / dur);
        p = 1 - Math.pow(1 - p, 3);            // easeOutCubic
        el.textContent = (target * p).toFixed(dec);
        if (p < 1) W.requestAnimationFrame(frame);
        else el.textContent = target.toFixed(dec);
      }
      W.requestAnimationFrame(frame);
    });
  }
  setTimeout(countUp, 80);

  // --- scroll-triggered reveals (re-armed every rerun) ---
  if (!reduce && 'IntersectionObserver' in W){
    const io = new W.IntersectionObserver(function(entries){
      entries.forEach(function(en){
        if (en.isIntersecting){ en.target.classList.add('sr-in'); io.unobserve(en.target); }
      });
    }, {root:null, rootMargin:'0px 0px -8% 0px', threshold:0.08});
    D.querySelectorAll('.scroll-reveal').forEach(function(el){
      el.classList.add('sr-ready');
      io.observe(el);
    });
    teardown.push(function(){ io.disconnect(); });
  }

  // --- soft cursor glow, bound to one stable node, re-wired every rerun ---
  if (!reduce){
    let glow = D.getElementById('fhs-glow');
    if (!glow){ glow = D.createElement('div'); glow.id = 'fhs-glow'; D.body.appendChild(glow); }
    const onMove = function(e){
      glow.style.opacity = '1';
      glow.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)';
    };
    const onLeave = function(){ glow.style.opacity = '0'; };
    D.addEventListener('mousemove', onMove, {passive:true});
    D.addEventListener('mouseleave', onLeave, {passive:true});
    teardown.push(function(){
      D.removeEventListener('mousemove', onMove);
      D.removeEventListener('mouseleave', onLeave);
    });
  } else {
    const g = D.getElementById('fhs-glow'); if (g) g.remove();
  }
})();
</script>
""", height=0)
