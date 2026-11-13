"""Guided demo: the six steps of the demo story (spec section 2) and small display helpers.

No Streamlit imports, so everything here can be tested alone. The values below are display
settings of the app, not model parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# The run the app opens on and the demo follows is set in config/script.yaml (`showcase`).

# Seconds each day stays on screen during playback.
PLAYBACK_SECONDS = 0.5

# Tabs of the simulation page.
TAB_TIMELINE = "Timeline"
TAB_DECISIONS = "Decisions"
TAB_FUNDING = "Funding"
TAB_UNCERTAINTY = "Uncertainty"
TAB_SCRIPT = "Exercise script"
# The contagion tab is hidden until SPEC_V2 phase F replaces it; its engine code stays.
TABS = [TAB_TIMELINE, TAB_DECISIONS, TAB_FUNDING, TAB_UNCERTAINTY, TAB_SCRIPT]

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
        "In the sidebar, choose the bank that will be in crisis. The demo uses the mid-size "
        "retail bank. All banks are fictitious.",
        "ctl_bank",
    ),
    DemoStep(
        "Pick a trigger",
        "In the sidebar, choose what starts the crisis. The demo uses the geopolitical shock, "
        "which starts on a Wednesday.",
        "ctl_scenario",
    ),
    DemoStep(
        "Press run",
        "Press Run the simulation. The engine computes the crisis day by day and its "
        "uncertainty range.",
        "ctl_run",
    ),
    DemoStep(
        "Watch the crisis unfold",
        "The crisis plays day by day, across a weekend (shaded). Press Play to see it again, "
        "or drag the day slider. The stages appear as dashed lines; on the second Friday the "
        "bank is declared failing or likely to fail.",
        "ctl_play",
        TAB_TIMELINE,
    ),
    DemoStep(
        "Change a decision",
        "In the sidebar, under Decisions, pick another decision path, for example "
        "Authorities act late. This tab compares the outcome with the other branch.",
        "ctl_decisions",
        TAB_DECISIONS,
    ),
    DemoStep(
        "Export the exercise script",
        "Download the exercise pack: the dossier and rules of play for the players, the "
        "observer sheets, and for the control team the charter draft, the session plan, the "
        "main events list, the inject sheets, the role cards and the full-information pack.",
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
