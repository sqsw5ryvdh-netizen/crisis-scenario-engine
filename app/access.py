"""Simple code in front of the control team's pages (Deliver and Close).

A deterrent against opening them by mistake, for instance by a player who edits the link of
a player page. It is not security: there are no accounts, and the code is in the config.
"""

from __future__ import annotations

import os
import secrets

import streamlit as st

from engine.schema import ScriptConfig

UNLOCKED = "control_unlocked"


def console_code(script: ScriptConfig) -> str:
    return os.environ.get("CRISIS_CONSOLE_CODE") or script.setup.console_code


def _check(script: ScriptConfig) -> None:
    given = st.session_state.get("control_code_input", "")
    if secrets.compare_digest(given.strip(), console_code(script)):
        st.session_state[UNLOCKED] = True
        st.session_state["control_code_error"] = ""
    else:
        st.session_state["control_code_error"] = "That is not the control team's code."


def require_control_team(script: ScriptConfig, page: str) -> None:
    """Stop the page here unless the control team's code was entered in this session."""
    if st.session_state.get(UNLOCKED):
        return
    st.title(page)
    st.caption("SIMULATION - exercise material, not a real event.")
    st.info(
        "This page is for the control team. Enter the control team's code to open it. "
        "Players use the link they were given."
    )
    st.text_input("Control team's code", type="password", key="control_code_input")
    st.button("Open", on_click=_check, args=(script,), key="control_code_open", type="primary")
    if st.session_state.get("control_code_error"):
        st.error(st.session_state["control_code_error"])
    st.stop()
