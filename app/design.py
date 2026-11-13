"""The design shared between the pages: what the Design page sets and the Prepare page uses.

Kept in the session under a key that belongs to no widget, so it survives a change of page.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from engine.config import load_config
from engine.decisions import Choice, choices_of
from engine.schema import Bank, Config, Scenario

DESIGN_KEY = "design"
# Keys of the market-shock challenge injects switched on in Prepare.
MARKET_KEY = "market_challenges"
# Widget values kept when the user moves to another page and back (Streamlit drops the
# state of a widget that is not drawn). Exact keys and key prefixes.
KEEP_KEYS = ("bank", "scenario", "decision_path", "reference_path", "writedown_level", "demo_on",
             "mc_seed")
KEEP_PREFIXES = ("taxonomy:", "dec:", "script_", "challenge_")


@st.cache_resource
def get_config() -> Config:
    return load_config()


@dataclass(frozen=True)
class Design:
    bank_key: str
    scenario_key: str
    bank: Bank  # with the Advanced sliders applied
    scenario: Scenario  # with the Advanced sliders applied, before any market shock
    choices: tuple[Choice, ...]


def store_design(bank_key: str, scenario_key: str, bank: Bank, scenario: Scenario,
                 choices: tuple[Choice, ...]) -> None:
    st.session_state[DESIGN_KEY] = Design(bank_key, scenario_key, bank, scenario, tuple(choices))


def current_design(config: Config) -> Design:
    """The design set on the Design page, or the showcase run if none was set yet."""
    design = st.session_state.get(DESIGN_KEY)
    if design is not None:
        return design
    showcase = config.script.showcase
    return Design(
        showcase.bank, showcase.scenario, config.banks[showcase.bank],
        config.scenarios[showcase.scenario],
        choices_of(config.decisions.paths[showcase.path]),
    )


def initial(key: str, **defaults):
    """Default arguments for a widget, left out once the session holds a value for its key."""
    return {} if key in st.session_state else defaults


def keep_widget_state(config: Config) -> None:
    """Re-store the values of the Design and Prepare widgets so they survive a page change."""
    slider_prefixes = tuple(f"{bank}:" for bank in config.banks)
    for key in list(st.session_state):
        if not isinstance(key, str):
            continue
        if key in KEEP_KEYS or key.startswith(KEEP_PREFIXES + slider_prefixes):
            st.session_state[key] = st.session_state[key]
