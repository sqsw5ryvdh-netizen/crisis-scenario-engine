"""Assumptions page: every parameter with its value and source (spec section 4.5)."""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st  # noqa: E402

from app.assumptions import (  # noqa: E402
    FLAG,
    assumption_counts,
    bank_table,
    model_table,
    scenario_table,
)
from engine.config import load_config  # noqa: E402


@st.cache_resource
def get_config():
    return load_config()


def show(table):
    """Show a table with the ASSUMPTION rows highlighted as well as labelled."""
    styled = table.style.apply(
        lambda row: [
            "background-color: rgba(250, 178, 25, 0.25)" if row["Status"] == FLAG else ""
        ] * len(row),
        axis=1,
    ).format(
        lambda value: f"{value:g}",
        subset=[c for c in ("Value", "Low", "High") if c in table.columns],
        na_rep="",
    )
    st.dataframe(styled, hide_index=True, width="stretch")


st.set_page_config(page_title="Assumptions", layout="wide")
config = get_config()
model, scenarios, banks = model_table(config), scenario_table(config), bank_table(config)
flagged, total = assumption_counts(model, scenarios, banks)

st.title("Assumptions")
st.warning(
    f"{flagged} of the {total} values on this page are assumptions: they have no verifiable "
    "source yet. They are labelled ASSUMPTION and highlighted. Results must not be read as "
    "calibrated estimates."
)
st.caption(
    "Sampled = drawn at random between Low and High in each Monte Carlo run "
    "(triangular distribution peaking at Value, each parameter independently)."
)

st.subheader("Model parameters")
show(model)

st.subheader("Scenarios")
show(scenarios)

st.subheader("Bank archetypes")
st.caption("Fictitious opening balance sheets.")
show(banks)
