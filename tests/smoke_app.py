"""Smoke test: render the Streamlit app for every sample company, assert no errors."""
import os, sys
from streamlit.testing.v1 import AppTest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(APP_DIR, "app.py")
sys.path.insert(0, APP_DIR)  # mirror `streamlit run`, which puts the script dir on sys.path

at = AppTest.from_file(APP).run(timeout=60)
assert not at.exception, f"App raised on load: {at.exception}"
assert any("Financial Health" in (m.value or "") for m in at.title), "title missing"
print("[PASS] default render (Sample: Bluechip) — no exception")

# Cycle through all three sample companies via the sidebar selectbox.
from data import PRESETS  # noqa: E402
labels = list(PRESETS.keys())
for lbl in labels:
    at.selectbox[0].set_value(lbl).run(timeout=60)
    assert not at.exception, f"App raised for {lbl}: {at.exception}"
    print(f"[PASS] rendered sample: {lbl}")

print("\nSmoke test passed.")
