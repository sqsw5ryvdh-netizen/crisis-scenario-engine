"""Guided demo: the six steps of the demo story (spec section 2) and small display helpers.

No Streamlit imports, so everything here can be tested alone. The values below are display
settings of the app, not model parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Selection the app opens on, which is the one the demo story uses.
DEMO_BANK = "digital"
DEMO_SCENARIO = "fast_run"

# Seconds each day stays on screen during playback.
PLAYBACK_SECONDS = 0.5

# Tabs of the simulation page.
TAB_TIMELINE = "Timeline"
TAB_DECISIONS = "Decisions"
TAB_FUNDING = "Funding"
TAB_UNCERTAINTY = "Uncertainty"
TAB_SCRIPT = "Exercise script"
TAB_CONTAGION = "Contagion"
TABS = [TAB_TIMELINE, TAB_DECISIONS, TAB_FUNDING, TAB_UNCERTAINTY, TAB_SCRIPT, TAB_CONTAGION]

HIGHLIGHT_COLOR = "#2a78d6"


@dataclass(frozen=True)
class DemoStep:
    title: str
    instruction: str
    control: str  # key of the container holding the control to use
    tab: Optional[str] = None  # tab opened when the step starts


DEMO_STEPS = (
    DemoStep(
        "Pick a bank archetype",
        "In the sidebar, choose the bank that will be in crisis. The demo uses the digital "
        "deposit bank. All banks are fictitious.",
        "ctl_bank",
    ),
    DemoStep(
        "Pick a trigger",
        "In the sidebar, choose what starts the crisis. The demo uses the fast digital run.",
        "ctl_scenario",
    ),
    DemoStep(
        "Press run",
        "Press Run the simulation. The engine computes the 30-day crisis and its uncertainty "
        "range.",
        "ctl_run",
    ),
    DemoStep(
        "Watch the crisis unfold",
        "The crisis plays day by day. Press Play to see it again, or drag the day slider to "
        "stop on a day. The stages appear as dashed lines, up to the payments default.",
        "ctl_play",
        TAB_TIMELINE,
    ),
    DemoStep(
        "Move a slider",
        "In the sidebar, move Uninsured deposits and watch the payments default day. The line under "
        "it compares with the starting values.",
        "ctl_uninsured",
        TAB_TIMELINE,
    ),
    DemoStep(
        "Export the exercise script",
        "Download the exercise pack: one document per authority, with only what it knows and "
        "the decisions open to it, plus a facilitator pack with full information.",
        "ctl_export",
        TAB_SCRIPT,
    ),
)

# Results are shown from this step on (index into DEMO_STEPS): the step after "Press run".
FIRST_STEP_WITH_RESULTS = 3


def highlight_css(control: str) -> str:
    """Style block that outlines the container holding the control of the current step."""
    return (
        f"<style>.st-key-{control} {{ outline: 2px solid {HIGHLIGHT_COLOR}; "
        "outline-offset: 6px; border-radius: 6px; }</style>"
    )


def describe_shift(failure_day: Optional[int], reference_day: Optional[int]) -> Optional[str]:
    """How the failure day compares with the run on the starting values; None if unchanged."""
    if failure_day == reference_day:
        return None
    if reference_day is None:
        return "No payments default with the starting values"
    if failure_day is None:
        return f"Payments default on day {reference_day} with the starting values"
    days = abs(failure_day - reference_day)
    unit = "day" if days == 1 else "days"
    direction = "earlier" if failure_day < reference_day else "later"
    return f"{days} {unit} {direction} than with the starting values (day {reference_day})"
