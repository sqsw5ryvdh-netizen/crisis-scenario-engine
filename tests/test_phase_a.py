"""SPEC_V2 phase A: panic that can fall, ELA, likely-to-fail flag, speed factor, re-set runs."""

import math

import pytest

from engine.config import load_config
from engine.decisions import Choice
from engine.model import panic_multiplier, simulate
from engine.schema import NewsEvent, Parameter

CONFIG = load_config()
TOL = 1e-9
# The four trigger scenarios; "baseline" is only a calm backdrop for a second bank.
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
PAIRS = [(bank, scenario) for bank in CONFIG.banks for scenario in SCENARIOS]


def param(value: float, unit: str = "x") -> Parameter:
    return Parameter(value=value, unit=unit, source="ASSUMPTION")


def run(bank: str, scenario: str, overrides: dict | None = None, speed: float | None = None,
        ela_day: int | None = None, **scenario_updates):
    """`ela_day`: ELA is granted without a cap and usable from that day (decided the day before)."""
    preset = CONFIG.scenarios[scenario]
    if overrides:
        merged = dict(preset.overrides)
        merged.update({name: param(value) for name, value in overrides.items()})
        scenario_updates["overrides"] = merged
    bank_config = CONFIG.banks[bank]
    if speed is not None:
        bank_config = bank_config.model_copy(update={"runoff_speed_factor": param(speed)})
    choices = () if ela_day is None else (Choice("ela", "grant_full", ela_day - 1),)
    return simulate(
        bank_config, CONFIG.model, preset.model_copy(update=scenario_updates),
        CONFIG.decisions, choices,
    )


def severity(value: float) -> Parameter:
    return param(value, "multiplier")


def event_day(result, kind: str):
    return next((e.day for e in result.events if e.kind == kind), None)


# ------------------------------------------------- panic can fall


def test_without_decay_panic_never_falls():
    table = run("universal", "slow_burn", overrides={"panic_decay": 0.0}).table
    assert (table["panic_multiplier"].diff().dropna() >= -TOL).all()


def test_with_decay_panic_falls_once_outflows_slow():
    # One early scare, then nothing: with decay the multiplier comes back down.
    scare = [NewsEvent(day=1, headline="A rumour that is not confirmed.", panic_shock=0.10)]
    table = run("retail", "slow_burn", news_events=scare).table
    multiplier = table["panic_multiplier"]
    assert multiplier.iloc[1] > multiplier.iloc[0]
    assert multiplier.iloc[-1] < multiplier.iloc[1]
    no_decay = run("retail", "slow_burn", news_events=scare, overrides={"panic_decay": 0.0}).table
    assert no_decay["panic_multiplier"].iloc[-1] >= no_decay["panic_multiplier"].iloc[1]
    assert table["cum_deposit_loss_pct"].iloc[-1] < no_decay["cum_deposit_loss_pct"].iloc[-1]


def test_reassuring_event_lowers_panic_and_outflows():
    preset = CONFIG.scenarios["fast_run"]
    calm = NewsEvent(day=3, headline="The authorities confirm the bank is sound.", panic_shock=-0.15)
    without = run("universal", "fast_run").table.set_index("day")
    with_calm = run("universal", "fast_run", news_events=list(preset.news_events) + [calm])
    table = with_calm.table.set_index("day")
    assert table.loc[2].equals(without.loc[2])  # nothing changes before the event
    assert table.loc[3, "panic_multiplier"] < without.loc[3, "panic_multiplier"]
    assert table.loc[3, "outflow_deposits"] < without.loc[3, "outflow_deposits"]
    assert any(e.kind == "news" and e.day == 3 for e in with_calm.events)


def test_panic_input_never_goes_below_zero():
    calm = [NewsEvent(day=1, headline="Good news.", panic_shock=-5.0)]
    table = run("retail", "slow_burn", news_events=calm).table
    assert table["panic_multiplier"].iloc[1] == pytest.approx(panic_multiplier(0.0, CONFIG.model))


# ------------------------------------------------- likely to fail and payments default


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_flag_comes_before_or_with_the_payments_default(bank, scenario):
    result = run(bank, scenario)
    assert result.likely_to_fail_day == event_day(result, "likely_to_fail")
    assert result.failure_day == event_day(result, "failure")
    if result.failure_day is not None:
        assert result.likely_to_fail_day is not None
        assert result.likely_to_fail_day <= result.failure_day
        last = result.table.iloc[-1]
        assert (last["unmet_outflow"] > TOL or last["cet1"] <= TOL) and last["status"] == "failed"
    # Only the last day can have unpaid outflows: the run stops there.
    assert (result.table["unmet_outflow"].iloc[:-1] <= TOL).all()


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_flag_is_raised_on_the_first_day_a_condition_holds(bank, scenario):
    result = run(bank, scenario)
    table = result.table
    requirement = CONFIG.banks[bank].capital.capital_requirement_ratio
    days = int(CONFIG.model.likely_to_fail_survival_days.value)
    condition = (
        (table["survival_days"] < days)
        | (table["cet1_ratio_projected"] < requirement)
        | (table["unmet_outflow"] > TOL)
        | (table["cet1"] <= TOL)
    )
    first = int(table.loc[condition, "day"].iloc[0]) if condition.any() else None
    assert result.likely_to_fail_day == first


def test_capital_below_requirement_raises_the_flag_but_does_not_stop_the_run():
    result = run("universal", "geopolitical")
    table = result.table
    requirement = CONFIG.banks["universal"].capital.capital_requirement_ratio
    below = table[table["cet1_ratio"] < requirement]
    assert not below.empty
    assert result.likely_to_fail_day <= int(below["day"].iloc[0])
    # It kept paying after the breach: here until the end of the horizon.
    assert result.failure_day is None or result.failure_day > int(below["day"].iloc[0])
    assert result.table["day"].iloc[-1] > int(below["day"].iloc[0])


def test_projected_capital_is_never_above_the_current_ratio():
    for bank, scenario in PAIRS:
        table = run(bank, scenario).table
        assert (table["cet1_ratio_projected"] <= table["cet1_ratio"] + TOL).all()


# Not tested, on purpose: the flag is not monotone in severity. A more severe run can breach
# the recovery indicator a day earlier, which mobilises central bank funding a day earlier
# and lengthens the survival horizon for a while. Only the payments default day is required
# never to come later with higher severity (CLAUDE.md), and tests/test_engine.py checks it.


# ------------------------------------------------- emergency liquidity assistance


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_ela_is_off_unless_granted(bank, scenario):
    table = run(bank, scenario).table
    assert table["funded_ela"].abs().max() < TOL
    assert table["ela_funding"].abs().max() < TOL
    assert table["encumbered_illiquid"].abs().max() < TOL


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_ela_keeps_the_balance_sheet_identity_and_no_negative_balances(bank, scenario):
    table = run(bank, scenario, ela_day=2).table
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < TOL
    columns = ["ela_funding", "funded_ela", "encumbered_illiquid", "cash", "illiquid_assets", "cet1"]
    assert (table[columns] >= -TOL).all().all()
    assert (table["encumbered_illiquid"] <= table["illiquid_assets"] + TOL).all()
    assert (table["encumbered_collateral"] <= table["eligible_collateral"] + TOL).all()
    funded = table[
        ["funded_cash", "funded_hqla", "funded_central_bank", "funded_ela", "funded_fire_sale",
         "unmet_outflow"]
    ].sum(axis=1)
    assert (funded - table["outflow_total"]).abs().max() < 1e-6


def test_ela_delays_the_payments_default():
    without = run("universal", "fast_run")
    granted = run("universal", "fast_run", ela_day=2)
    assert granted.table["funded_ela"].sum() > 0
    assert without.failure_day is not None
    assert granted.failure_day is None or granted.failure_day > without.failure_day


def test_ela_is_not_used_before_the_granted_day():
    table = run("universal", "fast_run", ela_day=4).table
    assert table.loc[table["day"] < 4, "funded_ela"].abs().max() < TOL


def test_ela_comes_after_ordinary_central_bank_funding_and_before_fire_sales():
    table = run("universal", "fast_run", ela_day=2).table
    haircut = CONFIG.model.central_bank_haircut.value
    for _, row in table[table["funded_ela"] > TOL].iterrows():
        assert row["cash"] < 1e-6 and row["hqla_level1"] < 1e-6 and row["hqla_level2"] < 1e-6
    recovery = int(table.loc[table["status"] != "normal", "day"].iloc[0])
    lag = int(CONFIG.model.central_bank_mobilisation_lag_days.value)
    after_lag = table[(table["funded_ela"] > TOL) & (table["day"] >= recovery + lag)]
    free = after_lag["eligible_collateral"] - after_lag["encumbered_collateral"]
    assert (free * (1 - haircut) < 1e-6).all()  # ordinary capacity is used up first
    # A fire sale happens only on days when ELA could not cover the rest.
    for _, row in table[table["funded_fire_sale"] > TOL].iterrows():
        share = CONFIG.model.ela_eligible_loan_share.value
        assert row["encumbered_illiquid"] >= share * row["illiquid_assets"] - 1e-6 or (
            row["cet1_ratio"] < CONFIG.banks["universal"].capital.capital_requirement_ratio
        )


def test_ela_stops_when_the_bank_is_no_longer_solvent():
    # A requirement above the opening ratio makes the bank "not solvent" from the start.
    bank = CONFIG.banks["universal"]
    capital = bank.capital.model_copy(update={"capital_requirement_ratio": 0.5})
    weak = bank.model_copy(update={"capital": capital})
    choices = (Choice("ela", "grant_full", 0),)
    result = simulate(weak, CONFIG.model, CONFIG.scenarios["fast_run"], CONFIG.decisions, choices)
    assert [record.effect_day for record in result.decision_log] == [1]  # granted, but unusable
    assert result.table["funded_ela"].abs().max() < TOL


def test_usable_ela_counts_in_the_liquidity_buffer():
    without = run("universal", "fast_run").table
    granted = run("universal", "fast_run", ela_day=2).table
    assert granted["liquidity_buffer"].iloc[2] > without["liquidity_buffer"].iloc[2]
    assert granted["liquidity_buffer"].iloc[1] == pytest.approx(without["liquidity_buffer"].iloc[1])


# ------------------------------------------------- run-off speed factor


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_speed_factor_acts_like_severity(scenario):
    # Without a fade: a fading severity moves towards a floor, which the factor then scales.
    base = CONFIG.scenarios[scenario].severity.value
    by_factor = run("retail", scenario, speed=2.0, fade=None).table
    by_severity = run("retail", scenario, severity=severity(2.0 * base), fade=None).table
    assert by_factor.drop(columns="status").equals(by_severity.drop(columns="status"))


def test_only_the_digital_archetype_is_faster_than_the_global_rates():
    factors = {key: bank.runoff_speed_factor.value for key, bank in CONFIG.banks.items()}
    assert factors["digital"] > 1.0
    assert all(value == 1.0 for key, value in factors.items() if key != "digital")


def test_digital_bank_has_the_thinnest_liquid_assets():
    def liquid_share(bank):
        assets = bank.assets
        liquid = (
            assets.cash_reserves + assets.hqla_level1 + assets.hqla_level2
            + assets.other_eligible_collateral
        )
        return liquid / bank.liabilities.deposits.total()

    shares = {key: liquid_share(bank) for key, bank in CONFIG.banks.items()}
    assert min(shares, key=shares.get) == "digital"


# ------------------------------------------------- acceptance targets of phase A


def test_at_least_four_pairs_survive_without_default_or_flag():
    survivors = [
        (bank, scenario)
        for bank, scenario in PAIRS
        if run(bank, scenario).failure_day is None
        and run(bank, scenario).likely_to_fail_day is None
    ]
    assert len(survivors) >= 4, survivors


def test_digital_bank_defaults_first_in_the_fast_run():
    days = {bank: run(bank, "fast_run").failure_day for bank in CONFIG.banks}
    assert all(day is not None for day in days.values())
    assert all(days["digital"] < day for bank, day in days.items() if bank != "digital")


def test_slow_burn_loss_of_the_universal_bank_is_of_the_documented_order():
    # About 25% of deposits in one month in the documented case (docs/CALIBRATION.md).
    loss = run("universal", "slow_burn").table["cum_deposit_loss_pct"].iloc[-1]
    assert 20.0 <= loss <= 30.0


# ------------------------------------------------- capital exhausted


def test_run_stops_when_capital_is_exhausted_and_cet1_never_goes_negative():
    # With ELA, pressure that never eases and no stable core, the universal bank keeps
    # finding liquidity until its capital is gone.
    no_core = {f"flight_prone_share_{name}": 1.0 for name in
               ("insured_retail", "uninsured_retail", "corporate", "financial")}
    no_core["weekend_rules_on"] = 0  # and no weekends to slow the run
    result = run("universal", "geopolitical", overrides=no_core, ela_day=2, fade=None)
    table = result.table
    assert (table["cet1"] >= -TOL).all()
    assert result.failure_day is not None
    assert table["cet1"].iloc[-1] <= TOL
    assert (table["cet1"].iloc[:-1] > TOL).all()
    reason = next(e.detail for e in result.events if e.kind == "failure")
    assert "capital exhausted" in reason


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_cet1_never_negative_over_a_range_of_severities(bank, scenario):
    for i in range(0, 101):
        table = run(bank, scenario, severity=severity(i * 0.1)).table
        assert (table["cet1"] >= -TOL).all(), i * 0.1
