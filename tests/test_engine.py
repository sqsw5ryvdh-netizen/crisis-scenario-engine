import itertools
import math

import pytest

from engine.config import load_config
from engine.model import DEPOSIT_CATEGORIES, rollover_fraction, simulate
from engine.run import main
from engine.schema import Parameter

CONFIG = load_config()
PAIRS = list(itertools.product(CONFIG.banks, CONFIG.scenarios))
TOL = 1e-9

BALANCE_COLUMNS = [f"dep_{name}" for name in DEPOSIT_CATEGORIES] + [
    "wholesale_funding",
    "central_bank_funding",
    "other_liabilities",
    "cash",
    "hqla_level1",
    "hqla_level2",
    "eligible_collateral",
    "encumbered_collateral",
    "illiquid_assets",
    "cet1",
    "rwa",
    "queued_withdrawals",
]


def run(bank: str, scenario: str, severity: float | None = None, **scenario_updates):
    preset = CONFIG.scenarios[scenario]
    if severity is not None:
        scenario_updates["severity"] = Parameter(
            value=severity, unit="multiplier", source="ASSUMPTION"
        )
    return simulate(CONFIG.banks[bank], CONFIG.model, preset.model_copy(update=scenario_updates))


def event_day(result, kind: str):
    return next((e.day for e in result.events if e.kind == kind), None)


def central_bank_usable_from(result) -> float:
    recovery_day = event_day(result, "recovery")
    if recovery_day is None:
        return math.inf
    return recovery_day + int(CONFIG.model.central_bank_mobilisation_lag_days.value)


# ------------------------------------------------- the 4 sanity checks of CLAUDE.md


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_balance_sheet_identity_every_day(bank, scenario):
    table = run(bank, scenario).table
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < TOL


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_no_negative_balances_and_outflows_within_stock(bank, scenario):
    table = run(bank, scenario).table
    assert (table[BALANCE_COLUMNS] >= -TOL).all().all()
    assert (table["encumbered_collateral"] <= table["eligible_collateral"] + TOL).all()
    funding_columns = [
        "funded_cash", "funded_hqla", "funded_central_bank", "funded_fire_sale", "unmet_outflow",
    ]
    assert (table[funding_columns] >= -TOL).all().all()
    # Each category's stock can only fall, and never by more than the stock itself.
    for name in DEPOSIT_CATEGORIES:
        stock = table[f"dep_{name}"]
        paid = -stock.diff().dropna()
        assert (paid >= -TOL).all()
        assert (paid <= stock.shift(1).dropna() + TOL).all()
    # Deposits paid out never exceed the outflow requested that day.
    paid_total = -table["deposits_total"].diff().dropna()
    assert (paid_total <= table["outflow_deposits"].iloc[1:] + TOL).all()


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_zero_runoff_changes_nothing(bank):
    result = run(bank, "fast_run", severity=0.0, news_events=[])
    table = result.table
    assert len(table) == int(CONFIG.model.horizon_days.value) + 1
    for column in BALANCE_COLUMNS:
        assert (table[column] - table[column].iloc[0]).abs().max() < TOL, column
    assert table["outflow_total"].abs().max() < TOL
    assert (table["status"] == "normal").all()
    assert result.events == ()
    assert result.failure_day is None


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_higher_severity_never_fails_later(bank, scenario):
    severities = [i * 0.02 for i in range(0, 501)]  # 0.0 to 10.0
    days = [run(bank, scenario, severity=s).failure_day for s in severities]
    as_number = [math.inf if d is None else d for d in days]
    for lower, higher in zip(as_number, as_number[1:]):
        assert higher <= lower, list(zip(severities, days))


# ------------------------------------------------- waterfall, lag, triggers


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_funding_sources_add_up_to_the_outflow(bank, scenario):
    table = run(bank, scenario).table
    funded = (
        table["funded_cash"]
        + table["funded_hqla"]
        + table["funded_central_bank"]
        + table["funded_fire_sale"]
        + table["unmet_outflow"]
    )
    assert (funded - table["outflow_total"]).abs().max() < TOL


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_waterfall_order(bank, scenario):
    result = run(bank, scenario)
    table = result.table
    usable_from = central_bank_usable_from(result)
    haircut = CONFIG.model.central_bank_haircut.value
    for row in table.itertuples():
        if row.funded_hqla > TOL:
            assert row.cash < TOL
        if row.funded_central_bank > TOL:
            assert row.hqla_level1 < TOL and row.hqla_level2 < TOL
        if row.funded_fire_sale > TOL:
            assert row.hqla_level1 < TOL and row.hqla_level2 < TOL
            if row.day >= usable_from:
                free = row.eligible_collateral - row.encumbered_collateral
                assert free * (1 - haircut) < TOL


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_no_central_bank_funding_before_mobilisation_lag(bank, scenario):
    result = run(bank, scenario)
    early = result.table[result.table["day"] < central_bank_usable_from(result)]
    assert early["funded_central_bank"].abs().max() < TOL
    assert early["central_bank_funding"].abs().max() < TOL


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_fire_sales_stay_within_accumulated_capacity(bank, scenario):
    table = run(bank, scenario).table
    initial = table["illiquid_assets"].iloc[0]
    share = CONFIG.model.fire_sale_daily_cap_share.value
    sold = initial - table["illiquid_assets"]
    assert (sold <= share * initial * table["day"] + TOL).all()


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_triggers_fire_in_order_and_never_go_back(bank, scenario):
    result = run(bank, scenario)
    recovery = event_day(result, "recovery")
    early_intervention = event_day(result, "early_intervention")
    failure = event_day(result, "failure")
    assert failure == result.failure_day
    reached = [d for d in (recovery, early_intervention, failure) if d is not None]
    assert reached == sorted(reached)
    if failure is not None:
        assert recovery is not None and early_intervention is not None
    order = ["normal", "recovery", "early_intervention", "failed"]
    stages = [order.index(s) for s in result.table["status"]]
    assert stages == sorted(stages)


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_simulation_stops_at_failure_with_a_funding_need(bank, scenario):
    result = run(bank, scenario)
    if result.failure_day is None:
        assert result.funding_need is None
        return
    assert result.table["day"].iloc[-1] == result.failure_day
    need = result.funding_need
    assert need.days == int(CONFIG.model.resolution_funding_days.value)
    assert need.need == pytest.approx(max(0.0, need.projected_outflows - need.remaining_liquidity))
    assert need.need >= 0


def test_rollover_is_full_at_zero_stress_and_falls_with_panic():
    p = CONFIG.model
    assert rollover_fraction(0.0, p) == 1.0
    values = [rollover_fraction(x / 20, p) for x in range(0, 21)]
    assert values == sorted(values, reverse=True)
    assert values[-1] < 1.0


def test_simulation_is_deterministic():
    first = run("digital", "fast_run")
    second = run("digital", "fast_run")
    assert first.table.equals(second.table)
    assert first.events == second.events


# ------------------------------------------------- cyber outage and geopolitical shock


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_outage_queues_withdrawals_and_pays_nothing(bank):
    table = run(bank, "cyber_outage").table
    overrides = CONFIG.scenarios["cyber_outage"].overrides
    start = int(overrides["payment_outage_start_day"].value)
    end = int(overrides["payment_outage_end_day"].value)
    outage = table[(table["day"] >= start) & (table["day"] <= end)]
    assert len(outage) == end - start + 1
    assert outage["outflow_total"].abs().max() < TOL
    assert (outage["deposits_total"] - table["deposits_total"].iloc[0]).abs().max() < TOL
    assert (outage["cash"] - table["cash"].iloc[0]).abs().max() < TOL
    assert outage["queued_withdrawals"].is_monotonic_increasing
    assert outage["queued_withdrawals"].iloc[0] > 0
    assert (outage["status"] != "failed").all()
    # Queued withdrawals never exceed the deposits they are queued against.
    assert (table["queued_withdrawals"] <= table["deposits_total"] + TOL).all()


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_queue_is_released_on_the_first_day_after_the_outage(bank):
    table = run(bank, "cyber_outage").table.set_index("day")
    end = int(CONFIG.scenarios["cyber_outage"].overrides["payment_outage_end_day"].value)
    queued = table.loc[end, "queued_withdrawals"]
    assert table.loc[end + 1, "outflow_deposits"] > queued
    assert table.loc[end + 1, "queued_withdrawals"] == 0
    assert (table.loc[end + 1 :, "queued_withdrawals"] == 0).all()


@pytest.mark.parametrize("scenario", ["fast_run", "slow_burn", "geopolitical"])
def test_no_queue_without_an_outage(scenario):
    for bank in CONFIG.banks:
        assert run(bank, scenario).table["queued_withdrawals"].abs().max() == 0


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_closed_wholesale_market_rolls_nothing_over(bank):
    table = run(bank, "geopolitical").table.set_index("day")
    assert (table["rollover_fraction"] == 0).all()
    for day, amount in CONFIG.banks[bank].liabilities.wholesale_funding.maturing.items():
        if day in table.index and table.loc[day, "unmet_outflow"] < TOL:
            assert table.loc[day, "outflow_wholesale"] == pytest.approx(amount)


def test_wider_haircuts_cost_more_capital_per_unit_sold():
    base = run("universal", "geopolitical", overrides={
        "wholesale_rollover_cap": CONFIG.scenarios["geopolitical"].overrides["wholesale_rollover_cap"]
    }).table
    shocked = run("universal", "geopolitical").table
    def loss_rate(table):
        sold = table[table["funded_hqla"] > TOL]
        return (sold["loss"] / sold["funded_hqla"]).iloc[0]
    assert loss_rate(shocked) > loss_rate(base)


# ------------------------------------------------- CLI


def test_cli_prints_the_daily_table(capsys):
    assert main(["--bank", "digital", "--scenario", "fast_run"]) == 0
    out = capsys.readouterr().out
    assert "Digital deposit bank" in out
    assert "dep_loss%" in out
    assert "Outcome:" in out


def test_cli_rejects_unknown_bank():
    with pytest.raises(SystemExit):
        main(["--bank", "nope", "--scenario", "fast_run"])
