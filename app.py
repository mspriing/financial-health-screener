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
from commentary import explain
from benchmark import load_universe, sector_stats, position
from screener import value_targets, strategic_targets, sectors as snapshot_sectors, fmt_z

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
/* one-line "what it measures + which way is good", secondary to the title */
.score .smeasure{color:var(--muted);font-size:.84rem;line-height:1.45;margin-top:10px;max-width:34ch;}
.score .smeasure b{color:var(--text);font-weight:600;}
/* threshold mini-legend: colored zone swatches lifted out of the old tiny caption */
.score .legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:13px;}
.score .legend .lg{display:inline-flex;align-items:center;gap:6px;color:var(--muted);
  font-size:.76rem;line-height:1.3;font-variant-numeric:tabular-nums;}
.score .legend .lg i{width:10px;height:10px;border-radius:3px;flex:none;}
.score .legend .lg b{color:var(--text);font-weight:600;}
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

/* ---- "why the scores read this way" — analyst commentary ---- */
.why{padding:6px 26px 20px;}
.why .wrow{display:flex;gap:18px;padding:15px 0;border-bottom:1px solid var(--border);}
.why .wtag{flex:0 0 86px;font-family:'Sora',sans-serif;font-size:.7rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.11em;color:var(--faint);padding-top:3px;}
.why .wtext{margin:0;color:var(--muted);font-size:.97rem;line-height:1.58;flex:1;
  font-variant-numeric:tabular-nums lining-nums;}
.why .wtext b,.why .wtext strong{color:var(--text);font-weight:600;}
.why .woverall{display:flex;gap:18px;margin-top:18px;padding-top:18px;
  border-top:1px solid var(--border-2);}
.why .woverall .wtag{color:var(--accent);}
.why .woverall .wtext{color:var(--text);font-weight:500;}

/* ---- sector benchmark — where the company lands vs its peers ---- */
.bench{padding:4px 26px 14px;}
.bench .intro{color:var(--muted);font-size:.9rem;line-height:1.58;margin:0 0 4px;}
.bench .intro b{color:var(--text);font-weight:600;}
.bench .brow{padding:20px 0;border-bottom:1px solid var(--border);}
.bench .brow:last-child{border-bottom:none;}
.bench .bhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;
  gap:8px 16px;margin-bottom:6px;}
.bench .bname{font-family:'Sora',sans-serif;font-size:.98rem;font-weight:600;color:var(--text);
  letter-spacing:-.01em;}
.bench .bname .sub{color:var(--faint);font-weight:600;font-size:.7rem;margin-left:9px;
  text-transform:uppercase;letter-spacing:.12em;}
.bench .bvals{font-size:.82rem;color:var(--muted);font-variant-numeric:tabular-nums lining-nums;}
.bench .bvals b{color:var(--text);font-weight:600;}
.bench .bvals .sep{color:var(--faint);margin:0 9px;}
/* the bar: a neutral rail, an accent interquartile band (p25–p75), a median tick,
   and the company's pointer — color carries no sole meaning (read line + labels back it). */
.bench .track{position:relative;height:8px;border-radius:6px;background:rgba(255,255,255,.07);
  margin:26px 0 11px;}
.bench .iqr{position:absolute;top:0;bottom:0;border-radius:6px;background:var(--accent-soft);
  border:1px solid var(--accent-line);animation:trk-in .6s var(--ease) both;}
.bench .med{position:absolute;top:-4px;bottom:-4px;width:2px;border-radius:2px;
  background:var(--muted);}
.bench .med .lbl{position:absolute;top:-19px;left:50%;transform:translateX(-50%);white-space:nowrap;
  font-size:.66rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);}
.bench .you{position:absolute;top:50%;width:15px;height:15px;border-radius:50%;
  transform:translate(-50%,-50%);background:var(--text);
  box-shadow:0 0 0 3px var(--bg-2),0 2px 7px rgba(0,0,0,.55);animation:ptr-in .55s var(--ease) both;}
.bench .you.good{background:var(--green);}
.bench .you.bad{background:var(--amber);}
.bench .scale{display:flex;justify-content:space-between;font-size:.7rem;color:var(--faint);
  font-variant-numeric:tabular-nums lining-nums;}
.bench .read{margin:13px 0 0;color:var(--muted);font-size:.93rem;line-height:1.55;}
.bench .read b{color:var(--text);font-weight:600;}
.bench .read .pct{color:var(--accent);font-weight:600;}
.bench .thin{display:flex;gap:14px;padding:16px 0;border-bottom:1px solid var(--border);
  color:var(--faint);font-size:.89rem;line-height:1.55;}
.bench .thin:last-child{border-bottom:none;}
.bench .thin .tg{flex:0 0 86px;font-family:'Sora',sans-serif;font-size:.7rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.11em;padding-top:2px;}
.bench .thin b{color:var(--muted);font-weight:600;}

/* ---- M&A target screener ---- */
.maintro{color:var(--muted);font-size:.93rem;line-height:1.62;margin:0 0 4px;max-width:78ch;}
.maintro b{color:var(--text);font-weight:600;}
.macount{color:var(--faint);font-size:.82rem;letter-spacing:.04em;margin:14px 0 4px;}
.macount b{color:var(--text);font-weight:600;}
/* ranked target card */
.mcard{padding:18px 20px 17px;margin-bottom:11px;}
.mcard .mtop{display:flex;align-items:flex-start;gap:14px;}
.mcard .mrank{flex:0 0 auto;font-family:'Sora',sans-serif;font-variant-numeric:tabular-nums;
  font-size:.82rem;font-weight:700;color:var(--accent);background:var(--accent-soft);
  border:1px solid var(--accent-line);border-radius:8px;min-width:30px;height:26px;
  display:inline-flex;align-items:center;justify-content:center;padding:0 7px;margin-top:1px;}
.mcard .mid{flex:1 1 auto;min-width:0;}
.mcard .mname{font-size:1.02rem;line-height:1.3;}
.mcard .mname b{font-family:'Sora',sans-serif;font-weight:700;color:var(--text);letter-spacing:-.01em;}
.mcard .mname .mco{color:var(--muted);font-weight:500;margin-left:8px;}
.mcard .msector{color:var(--faint);font-size:.74rem;text-transform:uppercase;letter-spacing:.12em;
  font-weight:600;margin-top:4px;}
.mcard .mfit{flex:0 0 auto;color:var(--faint);font-size:.72rem;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;font-variant-numeric:tabular-nums;text-align:right;padding-top:3px;}
.mcard .mfit b{display:block;color:var(--text);font-family:'Sora',sans-serif;font-size:1.05rem;
  font-weight:700;letter-spacing:-.01em;}
/* stat strip */
.mcard .mstats{display:flex;flex-wrap:wrap;gap:8px 9px;margin:14px 0 0;}
.mcard .mstat{display:inline-flex;align-items:baseline;gap:7px;padding:6px 11px;border-radius:8px;
  background:rgba(255,255,255,.04);border:1px solid var(--border);
  font-size:.84rem;color:var(--text);font-variant-numeric:tabular-nums lining-nums;}
.mcard .mstat i{font-style:normal;color:var(--faint);font-size:.68rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.09em;}
.mcard .mstat em{font-style:normal;font-weight:600;font-size:.78rem;}
.mcard .mstat .z-green,.mcard .mstat .clean{color:var(--green);}
.mcard .mstat .z-amber{color:var(--amber);}
.mcard .mstat .z-red,.mcard .mstat .flag{color:var(--red);}
.mcard .mstat .z-gray{color:var(--gray);}
.mcard .mwhy{margin:13px 0 0;color:var(--muted);font-size:.92rem;line-height:1.55;
  font-variant-numeric:tabular-nums;}
.mcard .mwhy b{color:var(--text);font-weight:600;}

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


def legend(items):
    """items: list of (css_color_var, label_html). Renders the threshold zone legend."""
    cells = "".join(
        f'<span class="lg"><i style="background:var({c})"></i>{t}</span>' for c, t in items)
    return f'<div class="legend">{cells}</div>'


def score_card(icon, stitle, ssub, measure, body, delay):
    return (f'<div class="card score scroll-reveal" style="--d:{delay}s">'
            f'<div class="icon">{icon}</div>'
            f'<div class="stitle">{stitle}</div><div class="ssub">{ssub}</div>'
            f'<div class="smeasure">{measure}</div>'
            f'{body}</div>')


def fin_table(headers, rows):
    head = "".join(f'<th class="{c}">{h}</th>' for h, c in headers)
    body = ""
    for cells in rows:
        tds = "".join(f'<td class="{c}">{v}</td>' for v, c in cells)
        body += f"<tr>{tds}</tr>"
    return f'<table class="fin-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# ---- sector benchmarking (Live-ticker mode only) --------------------------
@st.cache_data(show_spinner=False)
def load_peers():
    """Load the committed S&P 500 snapshot once (cached across reruns)."""
    return load_universe()


# Per-metric display config. higher_better=False for the M-Score: a HIGH M means MORE
# earnings red flags than peers, i.e. worse — so the read line is phrased the other way.
_BENCH_META = {
    "z":       {"tag": "Altman Z", "short": "Z", "dec": 2, "higher_better": True,
                "na": "isn’t computed for {name} — banks and insurers lack the working-capital "
                      "structure the Altman model reads."},
    "f_score": {"tag": "Piotroski F", "short": "F", "dec": 0, "higher_better": True,
                "na": "isn’t computed for {name} — its statements are missing inputs the "
                      "Piotroski tests need."},
    "m_score": {"tag": "Beneish M", "short": "M", "dec": 2, "higher_better": False,
                "na": "isn’t computed for {name} — its statements are missing inputs the "
                      "Beneish indices need."},
}


def _ordinal(n: int) -> str:
    n = int(round(n))
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _bench_read(short, pct, higher_better, sector):
    """
    One-line plain-English read of the percentile, oriented by metric direction. `rank`
    is the position in the *good* direction (for the M-Score, lower is better, so we
    flip it). The M wording stays sector-relative — "less clean than peers", not an
    absolute fraud call — since the actual flag is the Beneish threshold, not the rank.
    """
    rank = pct if higher_better else 100 - pct
    if higher_better:
        tail = ("stronger than most peers" if rank >= 75 else
                "a little ahead of the sector" if rank >= 55 else
                "right around the sector median" if rank > 45 else
                "a little behind the sector" if rank > 25 else
                "weaker than most peers")
    else:
        tail = ("a cleaner earnings reading than most peers" if rank >= 75 else
                "a little cleaner than the sector" if rank >= 55 else
                "right around the sector median" if rank > 45 else
                "a little less clean than the sector" if rank > 25 else
                "a less clean earnings reading than most peers")
    return (f'{short} in the <span class="pct">{_ordinal(pct)} percentile</span> of {sector} '
            f'&mdash; {tail}.')


def bench_bar(metric, value, stat, sector):
    """Render one metric's benchmark row: company value, sector median, p25–p75 bar + read."""
    cfg = _BENCH_META[metric]
    dec = cfg["dec"]
    p25, p75, med = stat.p25, stat.p75, stat.median
    # display domain: the IQR, padded, always wide enough to show the company's marker
    lo = min(p25, value); hi = max(p75, value)
    span = (hi - lo) or 1.0
    pad = span * 0.14
    d0, d1 = lo - pad, hi + pad
    dom = (d1 - d0) or 1.0
    def x(v):
        return max(0.0, min(100.0, (v - d0) / dom * 100.0))
    iqr_l, iqr_w = x(p25), x(p75) - x(p25)

    pct = position(value, stat.values)
    rank = pct if cfg["higher_better"] else 100 - pct
    tone = "good" if rank >= 60 else "bad" if rank <= 40 else ""

    return (
        f'<div class="brow">'
        f'<div class="bhead">'
        f'<span class="bname">{cfg["tag"]}<span class="sub">vs {stat.count} {sector} peers</span></span>'
        f'<span class="bvals"><b>{value:.{dec}f}</b> this company'
        f'<span class="sep">·</span>sector median <b>{med:.{dec}f}</b></span>'
        f'</div>'
        f'<div class="track">'
        f'<div class="iqr" style="left:{iqr_l:.1f}%;width:{iqr_w:.1f}%"></div>'
        f'<div class="med" style="left:{x(med):.1f}%"><span class="lbl">Median</span></div>'
        f'<div class="you {tone}" style="left:{x(value):.1f}%"></div>'
        f'</div>'
        f'<div class="scale"><span>{d0:.{dec}f}</span><span>{d1:.{dec}f}</span></div>'
        f'<div class="read">{_bench_read(cfg["short"], pct, cfg["higher_better"], sector)}</div>'
        f'</div>')


_ZONE_CLASS = {"Safe": "z-green", "Grey": "z-amber", "Distress": "z-red"}


def _mfmt(v, dec):
    """Format a numeric stat, or an em-dash when it's missing."""
    return f"{v:.{dec}f}" if isinstance(v, (int, float)) else "&mdash;"


def screener_card(rank, item, delay):
    """One ranked M&A-target card: identity, a scannable stat strip, and the thesis line."""
    z, zone = item["z"], item["zone"]
    zcls = _ZONE_CLASS.get(zone, "z-gray")
    zone_html = (f'{fmt_z(z)} <em class="{zcls}">{zone}</em>' if z is not None
                 else '<em class="z-gray">N/A</em>')
    f = item["f_score"]
    f_html = f"{f}<span style='color:var(--faint)'>/9</span>" if f is not None else "&mdash;"
    m_html = ('<em class="flag">Flag</em>' if item["m_flag"] else '<em class="clean">Clean</em>')

    stats = (
        f'<span class="mstat"><i>Z</i> {zone_html}</span>'
        f'<span class="mstat"><i>F</i> {f_html}</span>'
        f'<span class="mstat"><i>M</i> {m_html}</span>'
        f'<span class="mstat"><i>P/B</i> {_mfmt(item["price_to_book"], 2)}</span>'
        f'<span class="mstat"><i>EV/EBITDA</i> {_mfmt(item["ev_ebitda"], 1)}</span>'
    )
    return (
        f'<div class="card mcard reveal" style="--d:{delay:.2f}s">'
        f'<div class="mtop">'
        f'<span class="mrank">{rank:02d}</span>'
        f'<div class="mid"><div class="mname"><b>{item["ticker"]}</b>'
        f'<span class="mco">{item["name"]}</span></div>'
        f'<div class="msector">{item["sector"] or "—"}</div></div>'
        f'<div class="mfit">fit<b>{item["fit_score"]:.1f}</b></div>'
        f'</div>'
        f'<div class="mstats">{stats}</div>'
        f'<p class="mwhy">{item["why"]}</p>'
        f'</div>')


def bench_thin(metric, value, name):
    """A skipped metric: either the company has no score, or the sector is too thin."""
    cfg = _BENCH_META[metric]
    if value is None:
        msg = f'<b>{cfg["short"]}</b> {cfg["na"].format(name=name)}'
    else:
        msg = (f'<b>{cfg["short"]}</b> is too thin to benchmark — fewer than 8 peers in this '
               f'sector report it, so a percentile would be misleading.')
    return f'<div class="thin"><span class="tg">{cfg["tag"]}</span><span>{msg}</span></div>'


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

# ============================ TOP-LEVEL VIEW TOGGLE =========================
# Two views: the original single-company flow (DEFAULT — unchanged below) and the new
# sector-wide M&A target screener. Styled as the same segmented control as Data source.
st.markdown('<div class="navlabel reveal" style="--d:.03s">View</div>', unsafe_allow_html=True)
VIEWS = ["Screen a company", "M&A target screener"]
view = st.radio("View", VIEWS, horizontal=True, label_visibility="collapsed", key="view")

# --------------------------- M&A TARGET SCREENER ---------------------------
if view == "M&A target screener":
    st.markdown('<div class="seclabel scroll-reveal" style="margin-top:22px"><span class="t">'
                'M&amp;A target screener</span><span class="ln"></span></div>', unsafe_allow_html=True)
    st.markdown(
        '<p class="maintro">Companies matching a classic acquisition <b>profile</b>, pulled from '
        'a committed S&amp;P 500 snapshot — a starting point for diligence, <b>not a prediction '
        'that any deal will happen</b>. Valuations are screened for quality: it excludes '
        'financials the Altman model can’t read (banks/insurers with no Z) and distorted '
        'valuations (negative or near-zero price-to-book).</p>', unsafe_allow_html=True)

    MODES = ["Value / distress targets", "Strategic targets"]
    ma_mode = st.radio("Screen", MODES, horizontal=True, label_visibility="collapsed",
                       key="ma_mode")
    _explain = ("<b>Strong business, weak balance sheet.</b> Operationally strong (F&ge;6) but in "
                "real, non-terminal stress (grey-zone Z), clean earnings, and cheap versus sector "
                "peers — the classic leveraged-buyout shape."
                if ma_mode == MODES[0] else
                "<b>Strong, clean operators.</b> Safe-zone Z or high F with clean earnings — the "
                "kind of healthy business a strategic buyer wants to own.")
    st.markdown(f'<p class="maintro" style="margin-top:8px">{_explain}</p>', unsafe_allow_html=True)

    _rows = load_peers()
    _sector_opts = ["All sectors"] + snapshot_sectors(_rows)
    sector_pick = st.selectbox("Sector", _sector_opts, label_visibility="collapsed")

    _targets = (value_targets(_rows, sector=sector_pick) if ma_mode == MODES[0]
                else strategic_targets(_rows, sector=sector_pick))

    if not _targets:
        where = "any sector" if sector_pick == "All sectors" else sector_pick
        reason = (" The value screen also excludes banks/insurers, which have no Altman Z."
                  if ma_mode == MODES[0] else "")
        st.markdown(
            f'<div class="note empty reveal" style="--d:.05s">No companies in '
            f'<strong>{where}</strong> match this profile in the snapshot.{reason} Try another '
            f'sector or switch modes.</div>', unsafe_allow_html=True)
    else:
        scope = "across all sectors" if sector_pick == "All sectors" else f"in {sector_pick}"
        st.markdown(f'<p class="macount">Showing <b>{len(_targets)}</b> '
                    f'{"match" if len(_targets) == 1 else "matches"} {scope}, ranked by fit.</p>',
                    unsafe_allow_html=True)
        cards = "".join(screener_card(i + 1, item, min(i * 0.04, 0.4))
                        for i, item in enumerate(_targets))
        st.markdown(cards, unsafe_allow_html=True)

    st.markdown(
        '<div class="scroll-reveal" style="margin-top:26px;color:var(--faint);font-size:.8rem;'
        'line-height:1.6;">Profiles read precomputed Altman Z, Piotroski F and Beneish M from the '
        'snapshot — no scoring math is re-run here. Educational screen, not investment advice.</div>',
        unsafe_allow_html=True)
    st.stop()

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
                + legend([("--red", "Distress &lt; 1.81"),
                          ("--amber", "Grey 1.81&ndash;2.99"),
                          ("--green", "Safe &ge; 2.99")]))
    st.markdown(score_card(_ICON_ALTMAN, "Altman Z-Score", "Distress risk",
                           "Distance from bankruptcy risk &mdash; <b>higher is safer</b>.",
                           body, 0.06),
                unsafe_allow_html=True)

# --- Piotroski F ---
with c2:
    if piotroski is None:
        body = (num_na() + f'<div class="pillrow">{pill("Not applicable", "gray")}</div>'
                + '<div class="cap">Not computable for this company.</div>')
    else:
        # bands: 7-9 Strong, 3-6 Moderate, 0-2 Weak (label, color, gauge, legend all agree)
        ft = "green" if piotroski.score >= 7 else "amber" if piotroski.score >= 3 else "red"
        flbl = ("Strong" if piotroski.score >= 7 else "Moderate" if piotroski.score >= 3 else "Weak")
        g = gauge(piotroski.score / 9.0, [(28, "--red-trk"), (72, "--amber-trk"), (100, "--green-trk")])
        body = (num(piotroski.score, 0, suffix="/ 9") + g
                + f'<div class="pillrow">{pill(flbl, ft)}</div>'
                + legend([("--red", "Weak 0&ndash;2"),
                          ("--amber", "Moderate 3&ndash;6"),
                          ("--green", "Strong 7&ndash;9")]))
    st.markdown(score_card(_ICON_PIO, "Piotroski F-Score", "Fundamental strength",
                           "Nine pass/fail fundamentals (0&ndash;9) &mdash; <b>more passed is stronger</b>.",
                           body, 0.1),
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
                + legend([("--green", "Clean below &minus;1.78"),
                          ("--red", "Possible manipulation above &minus;1.78")]))
    st.markdown(score_card(_ICON_BEN, "Beneish M-Score", "Earnings red flags",
                           "How closely the accounting resembles known earnings manipulators "
                           "&mdash; <b>lower (more negative) is cleaner</b>.",
                           body, 0.14),
                unsafe_allow_html=True)

if notes:
    with st.expander("Why are some scores marked N/A?"):
        for k, v in notes.items():
            st.markdown(f"- **{k}:** {v}")

# ============================ WHY (analyst commentary) ======================
# Computed only from the models' own numbers (see commentary.py) — reproducible,
# no external data. One tight sentence per applicable model + an overall line.
why = explain(altman, piotroski, beneish, verdict)
st.markdown('<div class="seclabel scroll-reveal"><span class="t">Why the scores read this '
            'way</span><span class="ln"></span></div>', unsafe_allow_html=True)

_why_rows = "".join(
    f'<div class="wrow"><span class="wtag">{label}</span>'
    f'<p class="wtext">{why[key]}</p></div>'
    for label, key in (("Altman", "altman"), ("Piotroski", "piotroski"), ("Beneish", "beneish"))
    if why[key]
)
st.markdown(
    f'<div class="card why reveal" style="--d:.05s">{_why_rows}'
    f'<div class="woverall"><span class="wtag">Overall</span>'
    f'<p class="wtext">{why["overall"]}</p></div></div>',
    unsafe_allow_html=True)

# ============================ SECTOR BENCHMARK ==============================
# Live-ticker mode only: place this company's Z/F/M against its sector peers, using
# the committed S&P 500 snapshot. Sample/Manual companies have no real sector to peer
# against, so the section is skipped there. Scoring math is untouched — this only reads
# the already-computed scores and ranks them.
if source == "Live ticker":
    sector = meta.get("sector")
    company_vals = {
        "z": altman.z if altman else None,
        "f_score": float(piotroski.score) if piotroski else None,
        "m_score": beneish.m if beneish else None,
    }

    if not sector:
        # We couldn't classify the company — say so rather than guess a peer group.
        st.markdown('<div class="seclabel scroll-reveal"><span class="t">How it stacks up by '
                    'sector</span><span class="ln"></span></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="card bench reveal" style="--d:.05s"><p class="intro">We couldn’t '
            'determine a sector for this ticker, so there’s no peer group to benchmark against.'
            '</p></div>', unsafe_allow_html=True)
    else:
        stats = sector_stats(load_peers(), sector, exclude_ticker=meta.get("ticker"))
        blocks = []
        for m in ("z", "f_score", "m_score"):
            v, stat = company_vals[m], stats[m]
            if v is not None and not stat.thin:
                blocks.append(bench_bar(m, v, stat, sector))
            else:
                blocks.append(bench_thin(m, v, meta["name"]))

        shown = sum(1 for m in ("z", "f_score", "m_score")
                    if company_vals[m] is not None and not stats[m].thin)
        intro = (f'Where <b>{meta["name"]}</b> lands against its <b>{sector}</b> peers in the '
                 f'S&amp;P 500 snapshot — using the median and the middle 50% (p25–p75), not the '
                 f'average, so one outlier can’t skew the picture.') if shown else (
                 f'<b>{meta["name"]}</b> sits in <b>{sector}</b>, but none of the three scores can '
                 f'be benchmarked here — see why below.')

        st.markdown(
            f'<div class="seclabel scroll-reveal"><span class="t">How {meta["name"]} stacks up '
            f'in {sector}</span><span class="ln"></span></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="card bench reveal" style="--d:.05s">'
            f'<p class="intro">{intro}</p>{"".join(blocks)}</div>', unsafe_allow_html=True)

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
