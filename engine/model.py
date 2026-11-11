"""Deterministic crisis model (spec sections 3.1-3.4).

Pure functions only: no file I/O, no randomness, no Streamlit.

Daily loop for day t = 1..horizon:
1. Run-off: deposit outflows and non-rolled wholesale funding.
2. Funding waterfall: cash -> HQLA sale -> central bank -> ELA -> fire sale.
3. Balance sheet and indicators are updated.
4. Triggers are checked and events are logged.

Panic (SPEC_V2 phase A): the panic input x is a fading memory. Each day it loses the share
`panic_decay`, then the day's new deposit loss share and news shocks (positive or negative)
are added. It never goes below zero. With a decay of zero it equals cumulative loss + shocks.

Stages (they never go back): normal -> recovery -> early intervention -> likely to fail ->
payments default. "Likely to fail" is a forward-looking flag and does not stop the run.
Payments default (event kind "failure", status "failed") is the first day on which outflows
cannot be paid; the run stops there, and `failure_day` is that day. The run also stops, with
the same event, when capital is exhausted: sales at a loss are capped so that CET1 never
goes below zero, and what cannot be raised is left unpaid.

Accounting conventions:
- HQLA are sold outright; the haircut is a loss that reduces CET1.
- Central bank funding is a new liability; the collateral becomes encumbered.
- Fire-sale losses reduce CET1; RWA fall in proportion to illiquid assets sold.
- Fire-sale capacity accumulates daily from day 1; unused capacity carries over.
- ELA is a new liability against what is left of the eligible collateral, then against a
  share of the loan book; pledged loans cannot be sold.
- An archetype's run-off speed factor multiplies the scenario severity.
- Payment outage: withdrawals and maturing wholesale funding are queued, nothing is
  paid, and the whole queue is due on the first day after the outage.
- Equity is represented by CET1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import pandas as pd

from engine.schema import Bank, ModelParams, Scenario

DEPOSIT_CATEGORIES = ("insured_retail", "uninsured_retail", "corporate", "financial")
EPS = 1e-9

STATUS_NORMAL = "normal"
STATUS_RECOVERY = "recovery"
STATUS_EARLY_INTERVENTION = "early_intervention"
STATUS_LIKELY_TO_FAIL = "likely_to_fail"
STATUS_FAILED = "failed"  # payments default
_STAGES = (
    STATUS_NORMAL,
    STATUS_RECOVERY,
    STATUS_EARLY_INTERVENTION,
    STATUS_LIKELY_TO_FAIL,
    STATUS_FAILED,
)
_FAILED_STAGE = len(_STAGES) - 1


@dataclass(frozen=True)
class BankState:
    cash: float
    hqla_level1: float
    hqla_level2: float
    eligible_collateral: float  # total, including the encumbered part
    encumbered_collateral: float
    illiquid_assets: float
    deposits: tuple[float, ...]  # same order as DEPOSIT_CATEGORIES
    wholesale_ladder: tuple[tuple[int, float], ...]  # (maturity day, amount)
    wholesale_beyond: float
    central_bank_funding: float
    other_liabilities: float
    cet1: float
    rwa: float
    # Payment outage: withdrawals requested but not yet executed (still in `deposits`),
    # and wholesale funding that fell due but could not be settled.
    queued_deposits: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0)
    wholesale_overdue: float = 0.0
    # Emergency liquidity assistance drawn, and the loans pledged for it.
    ela_funding: float = 0.0
    encumbered_illiquid: float = 0.0

    @property
    def total_deposits(self) -> float:
        return sum(self.deposits)

    @property
    def wholesale_funding(self) -> float:
        return (
            sum(amount for _, amount in self.wholesale_ladder)
            + self.wholesale_beyond
            + self.wholesale_overdue
        )

    @property
    def total_assets(self) -> float:
        return (
            self.cash
            + self.hqla_level1
            + self.hqla_level2
            + self.eligible_collateral
            + self.illiquid_assets
        )

    @property
    def total_liabilities(self) -> float:
        return (
            self.total_deposits
            + self.wholesale_funding
            + self.central_bank_funding
            + self.ela_funding
            + self.other_liabilities
        )


@dataclass(frozen=True)
class Funding:
    """How one day's outflow was funded (the waterfall)."""

    cash: float = 0.0
    hqla: float = 0.0
    central_bank: float = 0.0
    ela: float = 0.0
    fire_sale: float = 0.0
    unmet: float = 0.0
    loss: float = 0.0


@dataclass(frozen=True)
class Event:
    day: int
    kind: str  # news | recovery | early_intervention | likely_to_fail | failure
    detail: str


@dataclass(frozen=True)
class FundingNeed:
    """Funding need in resolution, computed at the payments default day."""

    days: int
    projected_outflows: float
    remaining_liquidity: float
    need: float


@dataclass(frozen=True)
class SimulationResult:
    table: pd.DataFrame
    events: tuple[Event, ...]
    failure_day: Optional[int]  # day of the payments default
    funding_need: Optional[FundingNeed]
    likely_to_fail_day: Optional[int] = None  # day the forward-looking flag is raised


# --------------------------------------------------------------------------- state


def initial_state(bank: Bank) -> BankState:
    deposits = bank.liabilities.deposits
    wholesale = bank.liabilities.wholesale_funding
    return BankState(
        cash=bank.assets.cash_reserves,
        hqla_level1=bank.assets.hqla_level1,
        hqla_level2=bank.assets.hqla_level2,
        eligible_collateral=bank.assets.other_eligible_collateral,
        encumbered_collateral=0.0,
        illiquid_assets=bank.assets.illiquid_assets,
        deposits=tuple(getattr(deposits, name) for name in DEPOSIT_CATEGORIES),
        wholesale_ladder=tuple(sorted(wholesale.maturing.items())),
        wholesale_beyond=wholesale.beyond_horizon,
        central_bank_funding=0.0,
        other_liabilities=bank.liabilities.other_liabilities,
        cet1=bank.capital.cet1,
        rwa=bank.capital.rwa,
    )


def uninsured_share(bank: Bank) -> float:
    """Share of deposits other than insured retail."""
    deposits = bank.liabilities.deposits
    total = deposits.total()
    return 1.0 - deposits.insured_retail / total if total > 0 else 0.0


def with_uninsured_share(bank: Bank, share: float) -> Bank:
    """Copy of `bank` with a different uninsured deposit share.

    Money moves between insured retail and the three other categories (pro rata).
    Total deposits, and so the balance sheet identity, are unchanged.
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError("share must be between 0 and 1")
    deposits = bank.liabilities.deposits
    total = deposits.total()
    others = ("uninsured_retail", "corporate", "financial")
    current = sum(getattr(deposits, name) for name in others)
    target = share * total
    if current > 0:
        new = {name: getattr(deposits, name) * target / current for name in others}
    else:
        new = {"uninsured_retail": target, "corporate": 0.0, "financial": 0.0}
    new["insured_retail"] = total - target
    data = bank.model_dump()
    data["liabilities"]["deposits"] = new
    return Bank(**data)


# --------------------------------------------------------------------------- run-off


def _logistic(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def panic_multiplier(x: float, p: ModelParams) -> float:
    """1 + (max - 1) * logistic(steepness * (x - midpoint))."""
    z = p.panic_steepness.value * (x - p.panic_midpoint.value)
    return 1.0 + (p.panic_max_multiplier.value - 1.0) * _logistic(z)


def panic_level(x: float, p: ModelParams) -> float:
    """Panic normalised to 0 at x = 0 (the floor) and 1 at the maximum."""
    floor = panic_multiplier(0.0, p)
    span = p.panic_max_multiplier.value - floor
    if span <= 0:
        return 0.0
    return min(1.0, max(0.0, (panic_multiplier(x, p) - floor) / span))


def rollover_fraction(x: float, p: ModelParams) -> float:
    """Share of maturing wholesale funding that is rolled over."""
    fraction = 1.0 - p.wholesale_rollover_sensitivity.value * panic_level(x, p)
    return min(p.wholesale_rollover_cap.value, max(0.0, fraction))


def runoff_rates(x: float, severity: float, p: ModelParams) -> tuple[float, ...]:
    """Daily run-off rate per deposit category, capped at 100% of the stock."""
    multiplier = panic_multiplier(x, p)
    return tuple(
        min(1.0, getattr(p, f"base_runoff_{name}").value * severity * multiplier)
        for name in DEPOSIT_CATEGORIES
    )


# --------------------------------------------------------------------------- waterfall


def _sell(
    stock: float, need: float, haircut: float, loss_budget: float
) -> tuple[float, float, float]:
    """Sell just enough of `stock` to raise `need`, without losing more than `loss_budget`.

    Returns (sold, proceeds, loss).
    """
    if haircut > 0:
        stock = min(stock, max(0.0, loss_budget) / haircut)
    proceeds = min(need, stock * (1.0 - haircut))
    if proceeds <= 0:
        return 0.0, 0.0, 0.0
    sold = min(stock, proceeds / (1.0 - haircut))
    return sold, proceeds, sold - proceeds


def _pledgeable_loans(state: BankState, p: ModelParams) -> float:
    """Loans still acceptable as ELA collateral."""
    eligible = p.ela_eligible_loan_share.value * state.illiquid_assets
    return max(0.0, eligible - state.encumbered_illiquid)


def fund_outflow(
    state: BankState,
    need: float,
    central_bank_usable: bool,
    fire_sale_cap: float,
    p: ModelParams,
    ela_usable: bool = False,
) -> tuple[BankState, Funding]:
    """Fund `need` in the waterfall order. Deposits and wholesale funding are not touched."""
    remaining = need

    from_cash = min(remaining, state.cash)
    remaining -= from_cash

    # Sales at a loss stop when the loss would exceed the capital left (CET1 never below zero).
    capital = max(0.0, state.cet1)
    sold1, from_l1, loss1 = _sell(
        state.hqla_level1, remaining, p.hqla_level1_haircut.value, capital
    )
    remaining -= from_l1
    sold2, from_l2, loss2 = _sell(
        state.hqla_level2, remaining, p.hqla_level2_haircut.value, capital - loss1
    )
    remaining -= from_l2

    cb_haircut = p.central_bank_haircut.value
    from_cb = 0.0
    if central_bank_usable:
        capacity = (state.eligible_collateral - state.encumbered_collateral) * (1.0 - cb_haircut)
        from_cb = min(remaining, max(0.0, capacity))
        remaining -= from_cb

    # ELA: first what is left of the eligible collateral, then pledgeable loans.
    ela_haircut = p.ela_haircut.value
    ela_from_collateral = ela_from_loans = 0.0
    if ela_usable:
        encumbered_after_cb = state.encumbered_collateral + from_cb / (1.0 - cb_haircut)
        free_collateral = max(0.0, state.eligible_collateral - encumbered_after_cb)
        ela_from_collateral = min(remaining, free_collateral * (1.0 - ela_haircut))
        remaining -= ela_from_collateral
        ela_from_loans = min(remaining, _pledgeable_loans(state, p) * (1.0 - ela_haircut))
        remaining -= ela_from_loans
    from_ela = ela_from_collateral + ela_from_loans
    encumbered_illiquid = state.encumbered_illiquid + ela_from_loans / (1.0 - ela_haircut)

    sellable = min(max(0.0, state.illiquid_assets - encumbered_illiquid), fire_sale_cap)
    sold_fs, from_fs, loss_fs = _sell(
        sellable, remaining, p.fire_sale_discount.value, capital - loss1 - loss2
    )
    remaining -= from_fs

    unmet = remaining if remaining > EPS else 0.0
    loss = loss1 + loss2 + loss_fs
    illiquid_after = max(0.0, state.illiquid_assets - sold_fs)
    rwa = state.rwa
    if sold_fs > 0 and state.illiquid_assets > 0:
        rwa = state.rwa * illiquid_after / state.illiquid_assets

    new_state = replace(
        state,
        cash=max(0.0, state.cash - from_cash),
        hqla_level1=max(0.0, state.hqla_level1 - sold1),
        hqla_level2=max(0.0, state.hqla_level2 - sold2),
        encumbered_collateral=min(
            state.eligible_collateral,
            state.encumbered_collateral
            + from_cb / (1.0 - cb_haircut)
            + ela_from_collateral / (1.0 - ela_haircut),
        ),
        illiquid_assets=illiquid_after,
        encumbered_illiquid=min(state.illiquid_assets, encumbered_illiquid),
        central_bank_funding=state.central_bank_funding + from_cb,
        ela_funding=state.ela_funding + from_ela,
        cet1=max(0.0, state.cet1 - loss) if state.cet1 >= 0 else state.cet1,
        rwa=rwa,
    )
    funding = Funding(
        cash=from_cash,
        hqla=from_l1 + from_l2,
        central_bank=from_cb,
        ela=from_ela,
        fire_sale=from_fs,
        unmet=unmet,
        loss=loss,
    )
    return new_state, funding


# --------------------------------------------------------------------------- indicators


def hqla_buffer(state: BankState, p: ModelParams) -> float:
    """Cash plus HQLA after haircuts."""
    return (
        state.cash
        + state.hqla_level1 * (1.0 - p.hqla_level1_haircut.value)
        + state.hqla_level2 * (1.0 - p.hqla_level2_haircut.value)
    )


def central_bank_capacity(state: BankState, p: ModelParams) -> float:
    free = state.eligible_collateral - state.encumbered_collateral
    return max(0.0, free * (1.0 - p.central_bank_haircut.value))


def ela_capacity(state: BankState, central_bank_usable: bool, p: ModelParams) -> float:
    """What ELA could still lend. Free eligible collateral counts only if ordinary
    central bank operations are not usable, because they would take it first."""
    collateral = 0.0 if central_bank_usable else state.eligible_collateral - state.encumbered_collateral
    return max(0.0, collateral + _pledgeable_loans(state, p)) * (1.0 - p.ela_haircut.value)


def liquidity_buffer(
    state: BankState, central_bank_usable: bool, p: ModelParams, ela_usable: bool = False
) -> float:
    """Cash + HQLA after haircuts + central bank capacity already mobilised + usable ELA."""
    buffer = hqla_buffer(state, p)
    if central_bank_usable:
        buffer += central_bank_capacity(state, p)
    if ela_usable:
        buffer += ela_capacity(state, central_bank_usable, p)
    return buffer


def is_solvent(state: BankState, requirement: float) -> bool:
    """Solvency test for ELA: CET1 ratio at or above the capital requirement."""
    return state.rwa <= EPS or state.cet1 / state.rwa >= requirement


def lcr_proxy(state: BankState, p: ModelParams) -> float:
    """(Cash + HQLA after haircuts) / 30-day outflows at the LCR-style rates."""
    outflows = sum(
        stock * getattr(p, f"lcr_outflow_rate_{name}").value
        for name, stock in zip(DEPOSIT_CATEGORIES, state.deposits)
    )
    outflows += p.lcr_outflow_rate_wholesale.value * (
        sum(a for _, a in state.wholesale_ladder) + state.wholesale_overdue
    )
    if outflows <= EPS:
        return math.inf
    return hqla_buffer(state, p) / outflows


def projected_outflows(
    state: BankState, day: int, x: float, severity: float, days: int, p: ModelParams
) -> float:
    """Outflows over the next `days` days with panic frozen at its current level."""
    rates = runoff_rates(x, severity, p)
    total = 0.0
    for stock, rate in zip(state.deposits, rates):
        total += stock * (1.0 - (1.0 - rate) ** days)
    not_rolled = 1.0 - rollover_fraction(x, p)
    total += not_rolled * sum(a for d, a in state.wholesale_ladder if day < d <= day + days)
    return total


# --------------------------------------------------------------------------- simulation


def _pay(
    state: BankState,
    deposit_outflows: tuple[float, ...],
    wholesale_due: float,
    rollover: float,
    remaining_ladder: tuple[tuple[int, float], ...],
    central_bank_usable: bool,
    ela_usable: bool,
    fire_sale_cap: float,
    p: ModelParams,
) -> tuple[BankState, Funding, float, float]:
    """One day of payments: fund the outflow, then let what was paid leave the balance sheet.

    Returns (new state, funding, deposit outflow requested, wholesale outflow requested).
    """
    wholesale_outflow = wholesale_due * (1.0 - rollover)
    outflow_deposits = sum(deposit_outflows)
    need = outflow_deposits + wholesale_outflow
    state, funding = fund_outflow(state, need, central_bank_usable, fire_sale_cap, p, ela_usable)
    # Only the part that was actually paid leaves.
    paid_share = (need - funding.unmet) / need if need > EPS else 1.0
    state = replace(
        state,
        deposits=tuple(
            max(0.0, stock - out * paid_share)
            for stock, out in zip(state.deposits, deposit_outflows)
        ),
        queued_deposits=(0.0,) * len(DEPOSIT_CATEGORIES),
        wholesale_ladder=remaining_ladder,
        wholesale_overdue=0.0,
        # Rolled-over funding, and any part due but unpaid, moves beyond the horizon.
        wholesale_beyond=state.wholesale_beyond + wholesale_due - wholesale_outflow * paid_share,
    )
    return state, funding, outflow_deposits, wholesale_outflow


def projected_cet1_ratio(
    state: BankState,
    day: int,
    x: float,
    severity: float,
    days: int,
    central_bank_from: Optional[int],
    ela_from: Optional[int],
    requirement: float,
    fire_sale_daily_capacity: float,
    initial_illiquid: float,
    p: ModelParams,
) -> float:
    """Lowest CET1 ratio over the next `days` days with panic frozen at its current level.

    The projected outflows go through the same funding waterfall, so the losses on asset
    sales are counted. A payment outage is ignored: queued withdrawals are assumed paid.
    """
    lowest = state.cet1 / state.rwa if state.rwa > EPS else math.inf
    # Shortcut: if cash alone covers the projected outflows, nothing is sold and no loss
    # is made, so the ratio does not move.
    nothing_pending = sum(state.queued_deposits) <= EPS and state.wholesale_overdue <= EPS
    if nothing_pending and projected_outflows(state, day, x, severity, days, p) <= state.cash:
        return lowest
    rates = runoff_rates(x, severity, p)
    rollover = rollover_fraction(x, p)
    for ahead in range(day + 1, day + days + 1):
        deposit_outflows = tuple(
            queued + (stock - queued) * rate
            for stock, queued, rate in zip(state.deposits, state.queued_deposits, rates)
        )
        due = sum(a for d, a in state.wholesale_ladder if d == ahead) + state.wholesale_overdue
        remaining_ladder = tuple((d, a) for d, a in state.wholesale_ladder if d != ahead)
        already_sold = initial_illiquid - state.illiquid_assets
        fire_sale_cap = max(0.0, fire_sale_daily_capacity * ahead - already_sold)
        state, _, _, _ = _pay(
            state, deposit_outflows, due, rollover, remaining_ladder,
            central_bank_from is not None and ahead >= central_bank_from,
            ela_from is not None and ahead >= ela_from and is_solvent(state, requirement),
            fire_sale_cap, p,
        )
        if state.rwa > EPS:
            lowest = min(lowest, state.cet1 / state.rwa)
    return lowest


def _row(
    day: int,
    status: str,
    state: BankState,
    multiplier: float,
    rollover: float,
    outflow_deposits: float,
    outflow_wholesale: float,
    funding: Funding,
    central_bank_usable: bool,
    ela_usable: bool,
    projected_cet1: float,
    initial_deposits: float,
    p: ModelParams,
) -> dict:
    outflow_total = outflow_deposits + outflow_wholesale
    buffer = liquidity_buffer(state, central_bank_usable, p, ela_usable)
    row = {"day": day, "status": status}
    for name, stock in zip(DEPOSIT_CATEGORIES, state.deposits):
        row[f"dep_{name}"] = stock
    row.update(
        deposits_total=state.total_deposits,
        wholesale_funding=state.wholesale_funding,
        central_bank_funding=state.central_bank_funding,
        ela_funding=state.ela_funding,
        other_liabilities=state.other_liabilities,
        cash=state.cash,
        hqla_level1=state.hqla_level1,
        hqla_level2=state.hqla_level2,
        eligible_collateral=state.eligible_collateral,
        encumbered_collateral=state.encumbered_collateral,
        illiquid_assets=state.illiquid_assets,
        encumbered_illiquid=state.encumbered_illiquid,
        total_assets=state.total_assets,
        total_liabilities=state.total_liabilities,
        cet1=state.cet1,
        rwa=state.rwa,
        panic_multiplier=multiplier,
        rollover_fraction=rollover,
        outflow_deposits=outflow_deposits,
        outflow_wholesale=outflow_wholesale,
        outflow_total=outflow_total,
        funded_cash=funding.cash,
        funded_hqla=funding.hqla,
        funded_central_bank=funding.central_bank,
        funded_ela=funding.ela,
        funded_fire_sale=funding.fire_sale,
        unmet_outflow=funding.unmet,
        queued_withdrawals=sum(state.queued_deposits),
        loss=funding.loss,
        liquidity_buffer=buffer,
        lcr_proxy=lcr_proxy(state, p),
        survival_days=buffer / outflow_total if outflow_total > EPS else math.inf,
        cum_deposit_loss_pct=100.0 * (1.0 - state.total_deposits / initial_deposits)
        if initial_deposits > 0
        else 0.0,
        cet1_ratio=state.cet1 / state.rwa if state.rwa > EPS else math.inf,
        cet1_ratio_projected=projected_cet1,
        unencumbered_collateral=state.hqla_level1
        + state.hqla_level2
        + state.eligible_collateral
        - state.encumbered_collateral,
    )
    return row


def _check_triggers(
    day: int, stage: int, row: dict, requirement: float, initial_deposits: float, p: ModelParams
) -> tuple[int, list[Event]]:
    """Return the new stage (it never goes back) and the events for stages newly reached.

    Withdrawals queued during a payment outage count as deposit loss for the recovery trigger.
    """
    lcr = row["lcr_proxy"]
    queued = row["queued_withdrawals"]
    loss_share = row["cum_deposit_loss_pct"] / 100.0
    if initial_deposits > 0:
        loss_share += queued / initial_deposits
    cet1_ratio = row["cet1_ratio"]

    recovery_reasons = []
    if lcr < p.recovery_lcr_threshold.value:
        recovery_reasons.append(f"LCR proxy {lcr:.0%} below {p.recovery_lcr_threshold.value:.0%}")
    if loss_share > p.recovery_deposit_loss_threshold.value:
        recovery_reasons.append(
            f"cumulative deposit loss{' incl. queued withdrawals' if queued > EPS else ''} "
            f"{loss_share:.1%} above "
            f"{p.recovery_deposit_loss_threshold.value:.0%}"
        )

    ei_reasons = []
    if lcr < p.early_intervention_lcr_threshold.value:
        ei_reasons.append(
            f"LCR proxy {lcr:.0%} below {p.early_intervention_lcr_threshold.value:.0%}"
        )
    ei_floor = requirement + p.early_intervention_cet1_margin.value
    if cet1_ratio < ei_floor:
        ei_reasons.append(f"CET1 ratio {cet1_ratio:.1%} below {ei_floor:.1%}")

    flag_reasons = []
    flag_days = int(p.likely_to_fail_survival_days.value)
    if row["survival_days"] < flag_days:
        flag_reasons.append(
            f"liquidity covers {row['survival_days']:.1f} days of the current outflow, "
            f"fewer than {flag_days}"
        )
    if cet1_ratio < requirement:
        flag_reasons.append(f"CET1 ratio {cet1_ratio:.1%} below requirement {requirement:.1%}")
    elif row["cet1_ratio_projected"] < requirement:
        flag_reasons.append(
            f"CET1 ratio projected at {row['cet1_ratio_projected']:.1%} within {flag_days} "
            f"days, below requirement {requirement:.1%}"
        )

    fail_reasons = []
    if row["unmet_outflow"] > EPS:
        fail_reasons.append(
            f"liquidity exhausted, {row['unmet_outflow']:.2f} of outflows could not be paid"
        )
    if row["cet1"] <= EPS:
        fail_reasons.append("capital exhausted, no further asset sale at a loss is possible")

    reached = (
        4 if fail_reasons else 3 if flag_reasons else 2 if ei_reasons else 1 if recovery_reasons else 0
    )
    new_stage = max(stage, reached)
    carried = "conditions of a later stage met"
    details = {
        1: ("recovery", "; ".join(recovery_reasons) or carried),
        2: ("early_intervention", "; ".join(ei_reasons) or carried),
        3: ("likely_to_fail", "; ".join(flag_reasons) or carried),
        4: ("failure", "; ".join(fail_reasons)),
    }
    events = [Event(day, *details[s]) for s in range(stage + 1, new_stage + 1)]
    return new_stage, events


def simulate(bank: Bank, params: ModelParams, scenario: Scenario) -> SimulationResult:
    """Run the deterministic daily loop for one bank and one scenario.

    The simulation stops at the payments default day. Row 0 is the opening state.
    """
    p = params.with_overrides(scenario.overrides)
    # The archetype's speed factor scales every run-off rate, like the scenario severity.
    severity = scenario.severity.value * bank.runoff_speed_factor.value
    horizon = int(p.horizon_days.value)
    lag = int(p.central_bank_mobilisation_lag_days.value)
    outage_start = int(p.payment_outage_start_day.value)
    outage_end = int(p.payment_outage_end_day.value)
    decay = p.panic_decay.value
    flag_days = int(p.likely_to_fail_survival_days.value)
    ela_granted = int(p.ela_granted_from_day.value)
    ela_from: Optional[int] = ela_granted if ela_granted >= 1 else None
    requirement = bank.capital.capital_requirement_ratio

    state = initial_state(bank)
    initial_deposits = state.total_deposits
    initial_illiquid = state.illiquid_assets
    # Fire-sale capacity builds up by this amount every day; unused capacity carries over.
    fire_sale_daily_capacity = p.fire_sale_daily_cap_share.value * initial_illiquid

    events: list[Event] = []
    stage = 0
    central_bank_from: Optional[int] = None  # first day central bank funding is usable
    x = 0.0  # panic input (fading memory)
    loss_seen = 0.0  # deposit loss share (incl. queued) already counted in x

    def cb_usable(day: int) -> bool:
        return central_bank_from is not None and day >= central_bank_from

    def ela_usable(day: int, current: BankState) -> bool:
        return ela_from is not None and day >= ela_from and is_solvent(current, requirement)

    def loss_share(current: BankState) -> float:
        # Deposits already queued for withdrawal count as lost for the panic input.
        if initial_deposits <= 0:
            return 0.0
        left = initial_deposits - current.total_deposits + sum(current.queued_deposits)
        return left / initial_deposits

    def next_x(current: BankState, shock: float = 0.0) -> float:
        return max(0.0, x * (1.0 - decay) + loss_share(current) - loss_seen + shock)

    def projection(day: int, current: BankState) -> float:
        return projected_cet1_ratio(
            current, day, next_x(current), severity, flag_days, central_bank_from, ela_from,
            requirement, fire_sale_daily_capacity, initial_illiquid, p,
        )

    row = _row(
        0, STATUS_NORMAL, state, panic_multiplier(0.0, p), rollover_fraction(0.0, p),
        0.0, 0.0, Funding(), False, ela_usable(0, state), projection(0, state),
        initial_deposits, p,
    )
    stage, new_events = _check_triggers(0, stage, row, requirement, initial_deposits, p)
    events.extend(new_events)
    if stage >= 1:
        central_bank_from = lag
    row["status"] = _STAGES[stage]
    rows = [row]

    failure_day: Optional[int] = 0 if stage == _FAILED_STAGE else None
    funding_need: Optional[FundingNeed] = None
    day = 0

    while failure_day is None and day < horizon:
        day += 1

        # 1. Run-off.
        shock = 0.0
        for event in scenario.news_events:
            if event.day == day:
                shock += event.panic_shock
                events.append(Event(day, "news", event.headline))
        x = next_x(state, shock)
        loss_seen = loss_share(state)
        multiplier = panic_multiplier(x, p)
        rollover = rollover_fraction(x, p)
        rates = runoff_rates(x, severity, p)
        requests = tuple(
            (stock - queued) * rate
            for stock, queued, rate in zip(state.deposits, state.queued_deposits, rates)
        )
        maturing = sum(a for d, a in state.wholesale_ladder if d == day)
        remaining_ladder = tuple((d, a) for d, a in state.wholesale_ladder if d != day)
        usable = cb_usable(day)

        if outage_start >= 1 and outage_start <= day <= outage_end:
            # Payments are blocked: requests are queued, nothing is paid or funded.
            outflow_deposits = wholesale_outflow = 0.0
            funding = Funding()
            state = replace(
                state,
                queued_deposits=tuple(q + r for q, r in zip(state.queued_deposits, requests)),
                wholesale_ladder=remaining_ladder,
                wholesale_overdue=state.wholesale_overdue + maturing,
            )
        else:
            # Today's requests plus anything queued during an outage are due.
            deposit_outflows = tuple(q + r for q, r in zip(state.queued_deposits, requests))
            due = maturing + state.wholesale_overdue

            # 2. Funding waterfall and 3. balance sheet.
            already_sold = initial_illiquid - state.illiquid_assets
            fire_sale_cap = max(0.0, fire_sale_daily_capacity * day - already_sold)
            state, funding, outflow_deposits, wholesale_outflow = _pay(
                state, deposit_outflows, due, rollover, remaining_ladder,
                usable, ela_usable(day, state), fire_sale_cap, p,
            )
        row = _row(
            day, "", state, multiplier, rollover, outflow_deposits, wholesale_outflow,
            funding, usable, ela_usable(day, state), projection(day, state),
            initial_deposits, p,
        )

        # 4. Triggers.
        previous_stage = stage
        stage, new_events = _check_triggers(day, stage, row, requirement, initial_deposits, p)
        events.extend(new_events)
        if previous_stage == 0 and stage >= 1:
            central_bank_from = day + lag
        row["status"] = _STAGES[stage]
        rows.append(row)

        if stage == _FAILED_STAGE:
            failure_day = day

    if failure_day is not None:
        days = int(p.resolution_funding_days.value)
        projected = projected_outflows(state, failure_day, next_x(state), severity, days, p)
        remaining = liquidity_buffer(
            state, cb_usable(failure_day), p, ela_usable(failure_day, state)
        )
        funding_need = FundingNeed(
            days=days,
            projected_outflows=projected,
            remaining_liquidity=remaining,
            need=max(0.0, projected - remaining),
        )

    return SimulationResult(
        table=pd.DataFrame(rows),
        events=tuple(events),
        failure_day=failure_day,
        funding_need=funding_need,
        likely_to_fail_day=next((e.day for e in events if e.kind == "likely_to_fail"), None),
    )
