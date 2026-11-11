"""Deterministic crisis model (spec sections 3.1-3.4).

Pure functions only: no file I/O, no randomness, no Streamlit.

Daily loop for day t = 1..horizon:
1. Run-off: deposit outflows and non-rolled wholesale funding.
2. Funding waterfall: cash -> HQLA sale -> central bank -> fire sale.
3. Balance sheet and indicators are updated.
4. Triggers are checked and events are logged.

Accounting conventions:
- HQLA are sold outright; the haircut is a loss that reduces CET1.
- Central bank funding is a new liability; the collateral becomes encumbered.
- Fire-sale losses reduce CET1; RWA fall in proportion to illiquid assets sold.
- Fire-sale capacity accumulates daily from day 1; unused capacity carries over.
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
STATUS_FAILED = "failed"
_STAGES = (STATUS_NORMAL, STATUS_RECOVERY, STATUS_EARLY_INTERVENTION, STATUS_FAILED)


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

    @property
    def total_deposits(self) -> float:
        return sum(self.deposits)

    @property
    def wholesale_funding(self) -> float:
        return sum(amount for _, amount in self.wholesale_ladder) + self.wholesale_beyond

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
            + self.other_liabilities
        )


@dataclass(frozen=True)
class Funding:
    """How one day's outflow was funded (the waterfall)."""

    cash: float = 0.0
    hqla: float = 0.0
    central_bank: float = 0.0
    fire_sale: float = 0.0
    unmet: float = 0.0
    loss: float = 0.0


@dataclass(frozen=True)
class Event:
    day: int
    kind: str  # news | recovery | early_intervention | failure
    detail: str


@dataclass(frozen=True)
class FundingNeed:
    """Funding need in resolution, computed at the failure day."""

    days: int
    projected_outflows: float
    remaining_liquidity: float
    need: float


@dataclass(frozen=True)
class SimulationResult:
    table: pd.DataFrame
    events: tuple[Event, ...]
    failure_day: Optional[int]
    funding_need: Optional[FundingNeed]


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
    return min(1.0, max(0.0, 1.0 - p.wholesale_rollover_sensitivity.value * panic_level(x, p)))


def runoff_rates(x: float, severity: float, p: ModelParams) -> tuple[float, ...]:
    """Daily run-off rate per deposit category, capped at 100% of the stock."""
    multiplier = panic_multiplier(x, p)
    return tuple(
        min(1.0, getattr(p, f"base_runoff_{name}").value * severity * multiplier)
        for name in DEPOSIT_CATEGORIES
    )


# --------------------------------------------------------------------------- waterfall


def _sell(stock: float, need: float, haircut: float) -> tuple[float, float, float]:
    """Sell just enough of `stock` to raise `need`. Returns (sold, proceeds, loss)."""
    proceeds = min(need, stock * (1.0 - haircut))
    if proceeds <= 0:
        return 0.0, 0.0, 0.0
    sold = min(stock, proceeds / (1.0 - haircut))
    return sold, proceeds, sold - proceeds


def fund_outflow(
    state: BankState,
    need: float,
    central_bank_usable: bool,
    fire_sale_cap: float,
    p: ModelParams,
) -> tuple[BankState, Funding]:
    """Fund `need` in the waterfall order. Liabilities are not touched here."""
    remaining = need

    from_cash = min(remaining, state.cash)
    remaining -= from_cash

    sold1, from_l1, loss1 = _sell(state.hqla_level1, remaining, p.hqla_level1_haircut.value)
    remaining -= from_l1
    sold2, from_l2, loss2 = _sell(state.hqla_level2, remaining, p.hqla_level2_haircut.value)
    remaining -= from_l2

    cb_haircut = p.central_bank_haircut.value
    from_cb = 0.0
    if central_bank_usable:
        capacity = (state.eligible_collateral - state.encumbered_collateral) * (1.0 - cb_haircut)
        from_cb = min(remaining, max(0.0, capacity))
        remaining -= from_cb

    sellable = min(state.illiquid_assets, fire_sale_cap)
    sold_fs, from_fs, loss_fs = _sell(sellable, remaining, p.fire_sale_discount.value)
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
            state.eligible_collateral, state.encumbered_collateral + from_cb / (1.0 - cb_haircut)
        ),
        illiquid_assets=illiquid_after,
        central_bank_funding=state.central_bank_funding + from_cb,
        cet1=state.cet1 - loss,
        rwa=rwa,
    )
    funding = Funding(
        cash=from_cash,
        hqla=from_l1 + from_l2,
        central_bank=from_cb,
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


def liquidity_buffer(state: BankState, central_bank_usable: bool, p: ModelParams) -> float:
    """Cash + HQLA after haircuts + central bank capacity already mobilised."""
    buffer = hqla_buffer(state, p)
    if central_bank_usable:
        buffer += central_bank_capacity(state, p)
    return buffer


def lcr_proxy(state: BankState, p: ModelParams) -> float:
    """(Cash + HQLA after haircuts) / 30-day outflows at the LCR-style rates."""
    outflows = sum(
        stock * getattr(p, f"lcr_outflow_rate_{name}").value
        for name, stock in zip(DEPOSIT_CATEGORIES, state.deposits)
    )
    outflows += p.lcr_outflow_rate_wholesale.value * sum(a for _, a in state.wholesale_ladder)
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
    initial_deposits: float,
    p: ModelParams,
) -> dict:
    outflow_total = outflow_deposits + outflow_wholesale
    buffer = liquidity_buffer(state, central_bank_usable, p)
    row = {"day": day, "status": status}
    for name, stock in zip(DEPOSIT_CATEGORIES, state.deposits):
        row[f"dep_{name}"] = stock
    row.update(
        deposits_total=state.total_deposits,
        wholesale_funding=state.wholesale_funding,
        central_bank_funding=state.central_bank_funding,
        other_liabilities=state.other_liabilities,
        cash=state.cash,
        hqla_level1=state.hqla_level1,
        hqla_level2=state.hqla_level2,
        eligible_collateral=state.eligible_collateral,
        encumbered_collateral=state.encumbered_collateral,
        illiquid_assets=state.illiquid_assets,
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
        funded_fire_sale=funding.fire_sale,
        unmet_outflow=funding.unmet,
        loss=funding.loss,
        liquidity_buffer=buffer,
        lcr_proxy=lcr_proxy(state, p),
        survival_days=buffer / outflow_total if outflow_total > EPS else math.inf,
        cum_deposit_loss_pct=100.0 * (1.0 - state.total_deposits / initial_deposits)
        if initial_deposits > 0
        else 0.0,
        cet1_ratio=state.cet1 / state.rwa if state.rwa > EPS else math.inf,
        unencumbered_collateral=state.hqla_level1
        + state.hqla_level2
        + state.eligible_collateral
        - state.encumbered_collateral,
    )
    return row


def _check_triggers(
    day: int, stage: int, row: dict, requirement: float, p: ModelParams
) -> tuple[int, list[Event]]:
    """Return the new stage (it never goes back) and the events for stages newly reached."""
    lcr = row["lcr_proxy"]
    loss_share = row["cum_deposit_loss_pct"] / 100.0
    cet1_ratio = row["cet1_ratio"]

    recovery_reasons = []
    if lcr < p.recovery_lcr_threshold.value:
        recovery_reasons.append(f"LCR proxy {lcr:.0%} below {p.recovery_lcr_threshold.value:.0%}")
    if loss_share > p.recovery_deposit_loss_threshold.value:
        recovery_reasons.append(
            f"cumulative deposit loss {loss_share:.1%} above "
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

    fail_reasons = []
    if row["unmet_outflow"] > EPS:
        fail_reasons.append(
            f"liquidity exhausted: {row['unmet_outflow']:.2f} of outflows could not be paid"
        )
    if cet1_ratio < requirement:
        fail_reasons.append(f"CET1 ratio {cet1_ratio:.1%} below requirement {requirement:.1%}")

    reached = 3 if fail_reasons else 2 if ei_reasons else 1 if recovery_reasons else 0
    new_stage = max(stage, reached)
    carried = "conditions of a later stage met"
    details = {
        1: ("recovery", "; ".join(recovery_reasons) or carried),
        2: ("early_intervention", "; ".join(ei_reasons) or carried),
        3: ("failure", "; ".join(fail_reasons)),
    }
    events = [Event(day, *details[s]) for s in range(stage + 1, new_stage + 1)]
    return new_stage, events


def simulate(bank: Bank, params: ModelParams, scenario: Scenario) -> SimulationResult:
    """Run the deterministic daily loop for one bank and one scenario.

    The simulation stops at the failure day. Row 0 is the opening state.
    """
    p = params.with_overrides(scenario.overrides)
    severity = scenario.severity.value
    horizon = int(p.horizon_days.value)
    lag = int(p.central_bank_mobilisation_lag_days.value)
    requirement = bank.capital.capital_requirement_ratio

    state = initial_state(bank)
    initial_deposits = state.total_deposits
    initial_illiquid = state.illiquid_assets
    # Fire-sale capacity builds up by this amount every day; unused capacity carries over.
    fire_sale_daily_capacity = p.fire_sale_daily_cap_share.value * initial_illiquid

    events: list[Event] = []
    stage = 0
    central_bank_from: Optional[int] = None  # first day central bank funding is usable
    news_shock = 0.0

    def cb_usable(day: int) -> bool:
        return central_bank_from is not None and day >= central_bank_from

    row = _row(
        0, STATUS_NORMAL, state, panic_multiplier(0.0, p), rollover_fraction(0.0, p),
        0.0, 0.0, Funding(), False, initial_deposits, p,
    )
    stage, new_events = _check_triggers(0, stage, row, requirement, p)
    events.extend(new_events)
    if stage >= 1:
        central_bank_from = lag
    row["status"] = _STAGES[stage]
    rows = [row]

    failure_day: Optional[int] = 0 if stage == 3 else None
    funding_need: Optional[FundingNeed] = None
    x = 0.0
    day = 0

    while failure_day is None and day < horizon:
        day += 1

        # 1. Run-off.
        for event in scenario.news_events:
            if event.day == day:
                news_shock += event.panic_shock
                events.append(Event(day, "news", event.headline))
        loss_share = 1.0 - state.total_deposits / initial_deposits if initial_deposits > 0 else 0.0
        x = loss_share + news_shock
        multiplier = panic_multiplier(x, p)
        rollover = rollover_fraction(x, p)
        rates = runoff_rates(x, severity, p)
        deposit_outflows = tuple(stock * rate for stock, rate in zip(state.deposits, rates))
        maturing = sum(a for d, a in state.wholesale_ladder if d == day)
        wholesale_outflow = maturing * (1.0 - rollover)
        outflow_deposits = sum(deposit_outflows)
        need = outflow_deposits + wholesale_outflow

        # 2. Funding waterfall.
        usable = cb_usable(day)
        already_sold = initial_illiquid - state.illiquid_assets
        fire_sale_cap = max(0.0, fire_sale_daily_capacity * day - already_sold)
        state, funding = fund_outflow(state, need, usable, fire_sale_cap, p)

        # 3. Balance sheet: only the part that was actually paid leaves.
        paid_share = (need - funding.unmet) / need if need > EPS else 1.0
        state = replace(
            state,
            deposits=tuple(
                max(0.0, stock - out * paid_share)
                for stock, out in zip(state.deposits, deposit_outflows)
            ),
            wholesale_ladder=tuple((d, a) for d, a in state.wholesale_ladder if d != day),
            # Rolled-over funding, and any part due but unpaid, moves beyond the horizon.
            wholesale_beyond=state.wholesale_beyond + maturing - wholesale_outflow * paid_share,
        )
        row = _row(
            day, "", state, multiplier, rollover, outflow_deposits, wholesale_outflow,
            funding, usable, initial_deposits, p,
        )

        # 4. Triggers.
        previous_stage = stage
        stage, new_events = _check_triggers(day, stage, row, requirement, p)
        events.extend(new_events)
        if previous_stage == 0 and stage >= 1:
            central_bank_from = day + lag
        row["status"] = _STAGES[stage]
        rows.append(row)

        if stage == 3:
            failure_day = day

    if failure_day is not None:
        days = int(p.resolution_funding_days.value)
        end_loss = 1.0 - state.total_deposits / initial_deposits if initial_deposits > 0 else 0.0
        projected = projected_outflows(
            state, failure_day, end_loss + news_shock, severity, days, p
        )
        remaining = liquidity_buffer(state, cb_usable(failure_day), p)
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
    )
