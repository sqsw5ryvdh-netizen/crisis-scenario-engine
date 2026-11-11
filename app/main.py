"""Streamlit entry point. Run with: streamlit run app/main.py"""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Crisis Scenario Engine", layout="wide")
page = st.navigation(
    [
        st.Page("views/simulation.py", title="Simulation", default=True),
        st.Page("views/assumptions.py", title="Assumptions"),
    ]
)
page.run()
