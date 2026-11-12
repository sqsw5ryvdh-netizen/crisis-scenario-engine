"""Decision points (SPEC_V2 phase B): the data that goes into and comes out of a run.

Pure data classes, no model logic. Timing rule: a decision is taken after the close of a
day, on that day's figures, and is in effect from the next day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.schema import INTRADAY_BLOCKS, ActorInformation, DecisionPath, ModelParams

# The bank is still open at the end of the horizon:
OUTCOME_STABILISED = "stabilised"  # flag conditions gone, positive buffer, outflows near zero
OUTCOME_EMERGENCY_FUNDING = "emergency_funding"  # not stabilised, ELA outstanding
OUTCOME_UNDER_STRESS = "under_stress"  # not stabilised, without ELA
OPEN_OUTCOMES = (OUTCOME_STABILISED, OUTCOME_EMERGENCY_FUNDING, OUTCOME_UNDER_STRESS)
# The run ended before the horizon:
OUTCOME_DEFAULT = "payments_default"
OUTCOME_LIQUIDATION = "liquidation"
# ... or with a resolution tool: "bail_in" or "sale_of_business".


@dataclass(frozen=True)
class Choice:
    """One entry of a decision set: which option, and from which day it may be taken.

    `day` is the earliest close at which the decision is taken. If its enabling condition
    is not met by then, it is taken at the first later close at which the condition holds.
    """

    decision: str
    option: str
    day: int = 0


@dataclass(frozen=True)
class DecisionRecord:
    """One line of the decision log of a run."""

    decision: str
    decision_name: str
    option: str
    option_name: str
    actor: str
    scheduled_day: int
    decided_day: int  # taken after the close of this day
    effect_day: int  # in effect from this day
    # What the actor had when it decided: its own figures, at the age its delay implies.
    figures: tuple[tuple[str, float], ...]
    figures_as_of: int = 0  # the day those figures refer to


@dataclass(frozen=True)
class Scorecard:
    """Outcome of one path, for the side-by-side comparison."""

    outcome: str
    end_day: int
    deposits_lost: float
    deposits_lost_pct: float
    ela_outstanding: float
    central_bank_outstanding: float
    losses_booked: float
    uncovered_deposits: float  # deposits still in the bank that are not insured retail
    covered_deposits: float  # insured retail deposits still in the bank
    queued_withdrawals: float  # requested but not paid (moratorium or outage), still in the bank
    dgs_payout: float
    payout_days: Optional[int]  # days until covered depositors are paid; None if no payout


def lag_days(information: ActorInformation, day: int, faster_from: Optional[int]) -> int:
    """Age of an actor's figures on `day`; shorter once faster reporting is in effect."""
    if information.faster is not None and faster_from is not None and day >= faster_from:
        return information.faster.lag_days
    return information.lag_days


def choices_of(path: DecisionPath) -> tuple[Choice, ...]:
    return tuple(Choice(c.decision, c.option, c.day) for c in path.choices)


# ------------------------------------------------- tables for the CLI and the app

OUTCOME_LABELS = {
    OUTCOME_STABILISED: "Stabilised",
    OUTCOME_EMERGENCY_FUNDING: "Kept open on emergency funding",
    OUTCOME_UNDER_STRESS: "Still under stress, no default",
    OUTCOME_DEFAULT: "Payments default (disorderly)",
    "bail_in": "Resolution: bail-in",
    "sale_of_business": "Resolution: sale of business",
    OUTCOME_LIQUIDATION: "Liquidation",
}

# Figure -> (label, kind). kind: amount | pct (already in %) | ratio (share) | days.
FIGURE_LABELS = {
    "deposits_total": ("Deposits", "amount"),
    "cum_deposit_loss_pct": ("Deposits lost", "pct"),
    "outflow_total": ("Outflow of the day", "amount"),
    "queued_withdrawals": ("Requested, not yet settled", "amount"),
    "liquidity_buffer": ("Liquidity buffer", "amount"),
    "survival_days": ("Survival horizon", "days"),
    "lcr_proxy": ("LCR proxy", "ratio"),
    "cet1_ratio": ("CET1 ratio", "ratio"),
    "cet1_ratio_projected": ("CET1 ratio, projected", "ratio"),
    "unencumbered_collateral": ("Unencumbered collateral", "amount"),
    "central_bank_funding": ("Central bank funding", "amount"),
    "ela_funding": ("ELA outstanding", "amount"),
    "dep_insured_retail": ("Covered deposits", "amount"),
    "illiquid_assets": ("Loan book", "amount"),
    "loss": ("Loss of the day", "amount"),
    "unmet_outflow": ("Unpaid outflows", "amount"),
}


def format_figure(name: str, value: float, unit: str) -> str:
    label, kind = FIGURE_LABELS[name]
    if value == float("inf"):
        text = "no outflow"
    elif kind == "amount":
        text = f"{value:.2f} {unit}"
    elif kind == "pct":
        text = f"{value:.1f}%"
    elif kind == "ratio":
        text = f"{100 * value:.1f}%"
    else:
        text = f"{value:.1f} days"
    return f"{label} {text}"


def actor_label(actor: str) -> str:
    return actor.replace("_", " ").capitalize()


def decision_log_rows(log: tuple[DecisionRecord, ...], unit: str) -> list[dict[str, object]]:
    """The decision log as rows: who decided what, when, and on which figures."""
    return [
        {
            "Actor": actor_label(record.actor),
            "Decision": record.decision_name,
            "Option": record.option_name,
            "Decided after close of day": record.decided_day,
            "In effect from day": record.effect_day,
            "Figures as of day": record.figures_as_of,
            "Figures available at decision time": "; ".join(
                format_figure(name, value, unit) for name, value in record.figures
            ),
        }
        for record in log
    ]


def scorecard_rows(scorecard: Scorecard, unit: str) -> list[tuple[str, str]]:
    """The outcome scorecard as (label, value) pairs, in display order."""
    payout_days = "n/a" if scorecard.payout_days is None else f"{scorecard.payout_days} days"
    return [
        ("Outcome", OUTCOME_LABELS[scorecard.outcome]),
        ("Last simulated day", f"Day {scorecard.end_day}"),
        (f"Deposits lost ({unit})", f"{scorecard.deposits_lost:.2f} ({scorecard.deposits_lost_pct:.1f}%)"),
        (f"ELA outstanding at the end ({unit})", f"{scorecard.ela_outstanding:.2f}"),
        (f"Losses booked ({unit})", f"{scorecard.losses_booked:.2f}"),
        (f"Uncovered deposits still in the bank ({unit})", f"{scorecard.uncovered_deposits:.2f}"),
        (f"Covered deposits still in the bank ({unit})", f"{scorecard.covered_deposits:.2f}"),
        (f"Withdrawals requested but not paid ({unit})", f"{scorecard.queued_withdrawals:.2f}"),
        (f"DGS payout ({unit})", f"{scorecard.dgs_payout:.2f}"),
        ("Time to pay covered depositors", payout_days),
    ]


def intraday_allocation(outflow: float, params: ModelParams) -> list[tuple[str, float, float]]:
    """A day's outflow over the time blocks: (label, amount, cumulative amount).

    An allocation of the daily figure with fixed shares, not a simulation of the day.
    """
    rows, total = [], 0.0
    for key, label in INTRADAY_BLOCKS.items():
        amount = outflow * getattr(params, f"intraday_share_{key}").value
        total += amount
        rows.append((label, amount, total))
    return rows
