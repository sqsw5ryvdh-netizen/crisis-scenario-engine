"""Streamlit entry point. Run with: streamlit run app/main.py"""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Crisis Scenario Engine", layout="wide")
if "player" in st.query_params:
    # A player's link: only that page, without the menu of the control team's pages.
    page = st.navigation([st.Page("views/player.py", title="Player")], position="hidden")
else:
    from app.design import get_config, keep_widget_state  # noqa: E402

    keep_widget_state(get_config())
    # The pages follow the phases of the EBA Handbook: design, prepare, deliver, close.
    page = st.navigation(
        [
            st.Page("views/design.py", title="Design", default=True),
            st.Page("views/prepare.py", title="Prepare", url_path="prepare"),
            st.Page("views/console.py", title="Deliver", url_path="console"),
            st.Page("views/close.py", title="Close", url_path="close"),
            st.Page("views/assumptions.py", title="Assumptions and limits", url_path="assumptions"),
        ]
    )
page.run()
