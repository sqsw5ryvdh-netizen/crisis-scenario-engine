"""Resolution block (SPEC_V2 phase C2): what happens to capital and creditors at FoLtF.

Pure functions, computed after a run from its last day; nothing here feeds back into the
simulation. The valuation write-down is not known: the control team picks a low, central or
high value, all assumptions.

Order used in this model (not a statement of law): losses fall on CET1 first, then AT1/T2,
senior non-preferred debt, senior preferred debt, uncovered deposits. Covered deposits are
protected. Legal loss-absorption thresholds are not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.model import SimulationResult
from engine.schema import Bank, ModelParams

LEVELS = ("low", "central", "high")
RESOLUTION_TOOLS = ("bail_in", "sale_of_business")
EPS = 1e-9


@dataclass(frozen=True)
class StackLine:
    key: str
    label: str
    amount: float  # outstanding at the end of the run
    absorbed: float  # written down or converted (resolution), or lost (liquidation)

    @property
    def remaining(self) -> float:
        return self.amount - self.absorbed


@dataclass(frozen=True)
class LiquidationSummary:
    haircut: float  # share of the loan book's value lost
    asset_value: float  # what the assets fetch
    secured: float  # central bank funding and ELA, repaid from their collateral
    lines: tuple[StackLine, ...]  # claims in the order they are paid; absorbed = loss
    dgs_payout: float  # covered deposits paid out by the scheme
    dgs_recovery: float  # what the scheme gets back from the estate
    payout_days: Optional[int]


@dataclass(frozen=True)
class ResolutionSummary:
    tool: str  # bail_in | sale_of_business | liquidation
    level: str  # low | central | high
    foltf_day: Optional[int]
    end_day: int
    resolution_weekend: bool
    # Capital.
    losses_to_date: float  # booked during the run
    cet1_at_foltf: float
    writedown_share: float
    writedown: float  # valuation write-down of the loan book
    cet1_after_writedown: float  # may be negative: losses beyond CET1
    rwa_after: float
    requirement: float  # CET1 ratio to restore
    recapitalisation_need: float  # to bring CET1 back to the requirement
    # Who bears it, in order. Line 0 is CET1 (the shareholders).
    lines: tuple[StackLine, ...]
    covered_deposits: float  # protected
    shortfall: float  # what the stack cannot cover
    # MREL.
    mrel_requirement: float
    mrel_available: float  # CET1 at FoLtF + AT1/T2 + senior non-preferred
    mrel_sufficient: bool  # the need is covered without touching senior preferred debt
    # Write-down, as a share of the loan book, at which available MREL is no longer enough.
    # None: it is enough for any write-down up to the whole loan book.
    breakpoint_share: Optional[float]
    # Liquidity at reopening (resolution) or at the end of the run (liquidation).
    liquidity_need: float
    queued: float  # requested, not yet settled; included in the need
    liquidation: Optional[LiquidationSummary] = None


def _last(result: SimulationResult) -> dict:
    return result.rows[-1]


def _stack(bank: Bank, row: dict) -> list[tuple[str, str, float]]:
    """Loss-absorbing layers above CET1 at the end of the run, in the order they are used."""
    stack = bank.resolution
    at1_t2 = min(stack.at1_t2, row["other_liabilities"])
    non_preferred = min(stack.senior_non_preferred, row["wholesale_funding"])
    preferred = max(0.0, row["wholesale_funding"] - non_preferred)
    uncovered = row["deposits_total"] - row["dep_insured_retail"]
    return [
        ("at1_t2", "AT1 and Tier 2", at1_t2),
        ("senior_non_preferred", "Senior non-preferred debt", non_preferred),
        ("senior_preferred", "Senior preferred debt", preferred),
        ("uncovered_deposits", "Uncovered deposits", uncovered),
    ]


def liability_stack(bank: Bank, row: dict) -> list[tuple[str, str, float]]:
    """The loss-absorbing layers above CET1 on a given day, in the order they are used."""
    return _stack(bank, row)


def _need(cet1: float, rwa: float, loans: float, requirement: float, writedown: float) -> float:
    """Amount to find beyond CET1: losses above it, plus what restores the requirement.

    RWA fall in proportion to the loans written down, as they do when loans are sold.
    """
    rwa_after = rwa * (1.0 - writedown / loans) if loans > EPS else rwa
    return max(0.0, requirement * rwa_after - (cet1 - writedown))


def breakpoint_share(
    cet1: float, rwa: float, loans: float, requirement: float, mrel_above_cet1: float
) -> Optional[float]:
    """Write-down share of the loan book at which AT1/T2 and senior non-preferred debt no
    longer cover the need. 0 if they do not cover it even without a write-down; None if they
    cover it for any write-down up to the whole loan book."""
    if loans <= EPS:
        return None
    if _need(cet1, rwa, loans, requirement, 0.0) > mrel_above_cet1 + EPS:
        return 0.0
    if _need(cet1, rwa, loans, requirement, loans) <= mrel_above_cet1 + EPS:
        return None
    # The need is linear in the write-down between these two points.
    slope = 1.0 - requirement * rwa / loans
    writedown = (mrel_above_cet1 + cet1 - requirement * rwa) / slope
    return min(1.0, max(0.0, writedown / loans))


def level_value(parameter, level: str) -> float:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    return {"low": parameter.low, "central": parameter.value, "high": parameter.high}[level]


def resolution_summary(
    bank: Bank, params: ModelParams, result: SimulationResult, level: str = "central"
) -> Optional[ResolutionSummary]:
    """The resolution figures of a run that ends with a resolution tool or in liquidation.

    None for any other run: without FoLtF and a tool there is nothing to resolve.
    """
    tool = result.outcome
    if tool not in RESOLUTION_TOOLS + ("liquidation",):
        return None
    row = _last(result)
    requirement = bank.capital.capital_requirement_ratio
    cet1, rwa, loans = row["cet1"], row["rwa"], row["illiquid_assets"]
    share = level_value(params.resolution_writedown_share, level)
    writedown = share * loans
    rwa_after = rwa * (1.0 - share)
    cet1_after = cet1 - writedown
    need = _need(cet1, rwa, loans, requirement, writedown)

    layers = _stack(bank, row)
    lines = [StackLine("cet1", "CET1 (shareholders)", cet1, min(cet1, writedown))]
    left = need
    for key, label, amount in layers:
        used = min(amount, left)
        left -= used
        lines.append(StackLine(key, label, amount, used))
    above_cet1 = layers[0][2] + layers[1][2]
    funding = result.funding_need

    liquidation = None
    if tool == "liquidation":
        liquidation = _liquidation(bank, params, result, level)

    return ResolutionSummary(
        tool=tool,
        level=level,
        foltf_day=result.foltf_day,
        end_day=result.end_day,
        resolution_weekend=result.resolution_weekend,
        losses_to_date=bank.capital.cet1 - cet1,
        cet1_at_foltf=cet1,
        writedown_share=share,
        writedown=writedown,
        cet1_after_writedown=cet1_after,
        rwa_after=rwa_after,
        requirement=requirement,
        recapitalisation_need=need,
        lines=tuple(lines),
        covered_deposits=row["dep_insured_retail"],
        shortfall=max(0.0, left),
        mrel_requirement=bank.resolution.mrel_requirement_ratio * rwa,
        mrel_available=cet1 + above_cet1,
        mrel_sufficient=need <= above_cet1 + EPS,
        breakpoint_share=breakpoint_share(cet1, rwa, loans, requirement, above_cet1),
        liquidity_need=funding.need if funding is not None else 0.0,
        queued=funding.queued if funding is not None else 0.0,
        liquidation=liquidation,
    )


def _liquidation(
    bank: Bank, params: ModelParams, result: SimulationResult, level: str
) -> LiquidationSummary:
    """Wind-up: assets at liquidation value, claims paid in the order used in this model."""
    row = _last(result)
    haircut = level_value(params.liquidation_haircut, level)
    asset_value = row["total_assets"] - haircut * row["illiquid_assets"]
    secured = row["central_bank_funding"] + row["ela_funding"]
    estate = max(0.0, asset_value - secured)  # secured lenders are repaid from collateral
    covered = row["dep_insured_retail"]
    layers = {key: amount for key, _, amount in _stack(bank, row)}
    other = max(0.0, row["other_liabilities"] - layers["at1_t2"])
    claims = [
        ("covered_deposits", "Covered deposits (the scheme, after paying them out)", covered),
        ("uncovered_deposits", "Uncovered deposits", layers["uncovered_deposits"]),
        ("senior_preferred", "Senior preferred debt and other liabilities",
         layers["senior_preferred"] + other),
        ("senior_non_preferred", "Senior non-preferred debt", layers["senior_non_preferred"]),
        ("at1_t2", "AT1 and Tier 2", layers["at1_t2"]),
        ("cet1", "CET1 (shareholders)", max(0.0, row["cet1"])),
    ]
    lines = []
    for key, label, amount in claims:
        paid = min(amount, estate)
        estate -= paid
        lines.append(StackLine(key, label, amount, amount - paid))
    paid_out = result.scorecard.dgs_payout
    return LiquidationSummary(
        haircut=haircut,
        asset_value=asset_value,
        secured=secured,
        lines=tuple(lines),
        dgs_payout=paid_out,
        dgs_recovery=min(paid_out, lines[0].amount - lines[0].absorbed),
        payout_days=result.scorecard.payout_days,
    )


def indicative_bid(summary: ResolutionSummary, params: ModelParams) -> float:
    """Non-binding price named by the interested buyer (challenge inject, SPEC_V3 G5).

    An assumed share of the CET1 the bank holds once it is back at its requirement after the
    write-down. It changes no other figure.
    """
    return params.buyer_indicative_bid_share.value * summary.requirement * summary.rwa_after


def hostile_liquidity_need(summary: ResolutionSummary, params: ModelParams) -> float:
    """Liquidity need at reopening under hostile assumptions: a negative market reaction."""
    return summary.liquidity_need * (1.0 + params.hostile_liquidity_uplift.value)


def resolution_levels(
    bank: Bank, params: ModelParams, result: SimulationResult
) -> Optional[dict[str, ResolutionSummary]]:
    """The summary for each write-down level, or None if the run has no resolution."""
    if resolution_summary(bank, params, result) is None:
        return None
    return {level: resolution_summary(bank, params, result, level) for level in LEVELS}


# --------------------------------------------------------------------------- display


def breakpoint_text(summary: ResolutionSummary) -> str:
    """One sentence on the write-down at which available MREL stops being enough."""
    share = summary.breakpoint_share
    if share is None:
        return "Available MREL covers the need for any write-down up to the whole loan book."
    if share <= EPS:
        return "Available MREL does not cover the need even without a write-down."
    return (
        f"Available MREL covers the need up to a write-down of {100 * share:.1f}% of the loan "
        f"book; beyond that, senior preferred debt and then uncovered deposits are reached."
    )


def summary_rows(
    summary: ResolutionSummary, unit: str, hostile_need: Optional[float] = None
) -> list[tuple[str, str]]:
    """The resolution summary as (label, value) pairs, in display order.

    hostile_need: under hostile assumptions, the raised liquidity need, shown as one more line.
    """
    amount = lambda value: f"{value:.2f}"  # noqa: E731
    tool = {"bail_in": "Bail-in", "sale_of_business": "Sale of business",
            "liquidation": "Liquidation"}[summary.tool]
    rows = [
        ("Tool", tool),
        ("FoLtF in effect", "n/a" if summary.foltf_day is None else f"Day {summary.foltf_day}"
         + (" (after a Friday close: resolution weekend)" if summary.resolution_weekend else "")),
        (f"Losses booked before FoLtF ({unit})", amount(summary.losses_to_date)),
        (f"CET1 at FoLtF ({unit})", amount(summary.cet1_at_foltf)),
        ("Valuation write-down (assumed, share of the loan book)",
         f"{100 * summary.writedown_share:.0f}% ({summary.level})"),
        (f"Valuation write-down ({unit})", amount(summary.writedown)),
        (f"CET1 after the write-down ({unit})", amount(summary.cet1_after_writedown)),
        (f"Need: losses beyond CET1 plus recapitalisation to "
         f"{100 * summary.requirement:.1f}% ({unit})", amount(summary.recapitalisation_need)),
    ]
    if summary.liquidation is None:
        for line in summary.lines[1:]:
            rows.append(
                (f"{line.label}: written down or converted, of outstanding ({unit})",
                 f"{amount(line.absorbed)} of {amount(line.amount)}")
            )
        rows.append((f"Covered deposits, protected ({unit})", amount(summary.covered_deposits)))
        rows.append((f"Shortfall the stack cannot cover ({unit})", amount(summary.shortfall)))
    rows += [
        (f"MREL requirement ({unit})", amount(summary.mrel_requirement)),
        (f"Available MREL at FoLtF ({unit})", amount(summary.mrel_available)),
        ("Available MREL covers the need", "yes" if summary.mrel_sufficient else "no"),
        ("Write-down at which available MREL is no longer enough",
         "none up to the whole loan book" if summary.breakpoint_share is None
         else f"{100 * summary.breakpoint_share:.1f}% of the loan book"),
    ]
    if summary.liquidation is not None:
        wind_up = summary.liquidation
        rows += [
            ("Liquidation: value lost on the loan book (assumed)",
             f"{100 * wind_up.haircut:.0f}% ({summary.level})"),
            (f"Liquidation: value of the assets ({unit})", amount(wind_up.asset_value)),
            (f"Liquidation: repaid to secured lenders ({unit})", amount(wind_up.secured)),
        ]
        for line in wind_up.lines:
            rows.append(
                (f"Liquidation, {line.label}: loss, of claim ({unit})",
                 f"{amount(line.absorbed)} of {amount(line.amount)}")
            )
        rows += [
            (f"DGS payout of covered deposits ({unit})", amount(wind_up.dgs_payout)),
            (f"DGS recovery from the estate ({unit})", amount(wind_up.dgs_recovery)),
            ("Time to pay covered depositors",
             "n/a" if wind_up.payout_days is None else f"{wind_up.payout_days} days"),
        ]
    rows += [
        (f"Liquidity need over the following days ({unit})", amount(summary.liquidity_need)),
        (f"Of which requested, not yet settled ({unit})", amount(summary.queued)),
    ]
    if hostile_need is not None:
        rows.append(
            (f"Liquidity need with a negative market reaction (hostile assumption) ({unit})",
             amount(hostile_need))
        )
    return rows
