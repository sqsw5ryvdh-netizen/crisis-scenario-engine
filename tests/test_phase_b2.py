"""SPEC_V2 phase B2: stable core, outcome labels, milder cyber outage."""

import pytest

from engine.config import load_config
from engine.decisions import OPEN_OUTCOMES, OUTCOME_LABELS, Choice, choices_of
from engine.model import DEPOSIT_CATEGORIES, simulate
from engine.schema import Parameter

CONFIG = load_config()
DECISIONS = CONFIG.decisions
TOL = 1e-9
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
PAIRS = [(bank, scenario) for bank in CONFIG.banks for scenario in SCENARIOS]


def param(value: float) -> Parameter:
    return Parameter(value=value, unit="x", source="ASSUMPTION")


def run(bank: str, scenario: str, path: str = "act_late", overrides: dict | None = None,
        **scenario_updates):
    preset = CONFIG.scenarios[scenario]
    if overrides:
        merged = dict(preset.overrides)
        merged.update({name: param(value) for name, value in overrides.items()})
        scenario_updates["overrides"] = merged
    return simulate(
        CONFIG.banks[bank], CONFIG.model, preset.model_copy(update=scenario_updates),
        DECISIONS, choices_of(DECISIONS.paths[path]),
    )


def share(name: str) -> float:
    return getattr(CONFIG.model, f"flight_prone_share_{name}").value


NO_CORE = {f"flight_prone_share_{name}": 1.0 for name in DEPOSIT_CATEGORIES}


# ------------------------------------------------- stable core


def test_insured_retail_is_mostly_stable_and_the_least_flight_prone():
    shares = {name: share(name) for name in DEPOSIT_CATEGORIES}
    assert shares["insured_retail"] <= 0.25
    assert shares["insured_retail"] == min(shares.values())
    assert all(0 < value <= 1 for value in shares.values())


@pytest.mark.parametrize("path", ["act_late", "ela_granted", "support_package"])
@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_deposits_never_fall_below_the_stable_core(bank, scenario, path):
    table = run(bank, scenario, path).table
    for name in DEPOSIT_CATEGORIES:
        opening = table[f"dep_{name}"].iloc[0]
        core = opening * (1.0 - share(name))
        assert (table[f"dep_{name}"] >= core - TOL).all(), name
    lost = table["cum_deposit_loss_pct"].iloc[-1] / 100.0
    deposits = CONFIG.banks[bank].liabilities.deposits
    flight_prone = sum(getattr(deposits, n) * share(n) for n in DEPOSIT_CATEGORIES)
    assert lost <= flight_prone / deposits.total() + TOL


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_first_day_of_a_run_is_the_same_with_or_without_a_stable_core(bank):
    with_core = run(bank, "fast_run").table
    without = run(bank, "fast_run", overrides=NO_CORE).table
    assert with_core["outflow_deposits"].iloc[1] == pytest.approx(without["outflow_deposits"].iloc[1])
    # From the second day on the stable core slows the run.
    assert with_core["outflow_deposits"].iloc[2] < without["outflow_deposits"].iloc[2]


def test_outflows_die_out_as_the_flight_prone_part_is_used_up():
    # ELA keeps the bank paying, so the run can be followed to its end. Without weekends,
    # which would add a low on Saturday and a catch-up on Monday.
    table = run("universal", "fast_run", "ela_granted", overrides={"weekend_rules_on": 0}).table
    opening = table["deposits_total"].iloc[0]
    assert table["outflow_deposits"].iloc[2] > 0.2 * opening  # the peak of the run
    assert table["outflow_deposits"].iloc[-1] < 0.001 * opening
    late = table["outflow_deposits"].iloc[5:]
    assert (late.diff().dropna() <= TOL).all()  # after the news days it only declines


def test_a_category_with_no_flight_prone_share_does_not_move():
    table = run("retail", "fast_run", overrides={"flight_prone_share_insured_retail": 0.0}).table
    assert (table["dep_insured_retail"] == table["dep_insured_retail"].iloc[0]).all()
    assert table["dep_corporate"].iloc[-1] < table["dep_corporate"].iloc[0]


def test_queued_withdrawals_respect_the_stable_core():
    # During the outage requests are queued; they can never exceed the flight-prone part.
    table = run("universal", "cyber_outage", severity=param(10.0)).table
    deposits = CONFIG.banks["universal"].liabilities.deposits
    flight_prone = sum(getattr(deposits, n) * share(n) for n in DEPOSIT_CATEGORIES)
    assert table["queued_withdrawals"].max() <= flight_prone + 1e-6


# ------------------------------------------------- outcome labels


def test_every_outcome_has_a_label_and_survives_is_gone():
    assert set(OPEN_OUTCOMES) == {"stabilised", "emergency_funding", "under_stress"}
    assert OUTCOME_LABELS["emergency_funding"] == "Kept open on emergency funding"
    assert not any("surviv" in label.lower() for label in OUTCOME_LABELS.values())


@pytest.mark.parametrize("path", list(DECISIONS.paths))
@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_outcome_follows_its_definition(bank, scenario, path):
    result = run(bank, scenario, path)
    table, model = result.table, CONFIG.model
    assert result.outcome in OUTCOME_LABELS
    if result.failure_day is not None:
        assert result.outcome == "payments_default"
        return
    if result.outcome not in OPEN_OUTCOMES:
        assert result.end_day < int(model.horizon_days.value) or result.foltf_day is not None
        assert result.flag_cleared_day is None
        return
    assert result.end_day == int(model.horizon_days.value)
    last = table.iloc[-int(model.stabilised_days.value):]
    calm = model.stabilised_outflow_share.value * table["deposits_total"].iloc[0]
    stabilised = bool(
        (~last["flag_active"].astype(bool)).all()
        and (last["liquidity_buffer"] > TOL).all()
        and (last["outflow_total"] + last["queued_withdrawals"] <= calm).all()
    )
    ela = table["ela_funding"].iloc[-1]
    expected = "stabilised" if stabilised else "emergency_funding" if ela > TOL else "under_stress"
    assert result.outcome == expected


def test_a_bank_with_no_buffer_and_a_standing_flag_is_not_called_stabilised():
    result = run("universal", "geopolitical")
    last = result.table.iloc[-1]
    assert last["liquidity_buffer"] == pytest.approx(0.0) and last["flag_active"]
    assert result.outcome == "under_stress" and result.flag_cleared_day is None


def test_open_on_ela_without_being_stabilised_is_emergency_funding():
    result = simulate(
        CONFIG.banks["universal"], CONFIG.model, CONFIG.scenarios["fast_run"],
        DECISIONS, (Choice("ela", "grant_capped", 1),),
    )
    assert result.outcome == "emergency_funding"
    assert result.scorecard.ela_outstanding > 0 and result.failure_day is None


def test_flag_cleared_day_is_the_first_day_the_conditions_stay_away():
    result = run("universal", "fast_run", "ela_granted")
    assert result.outcome == "stabilised" and result.likely_to_fail_day is not None
    cleared = result.flag_cleared_day
    table = result.table.set_index("day")
    assert cleared is not None and cleared > result.likely_to_fail_day
    assert table.loc[cleared - 1, "flag_active"] and not table.loc[cleared:, "flag_active"].any()
    # The stage itself never goes back.
    assert table["status"].iloc[-1] == "likely_to_fail"


def test_a_calm_bank_counts_as_stabilised():
    result = run("retail", "baseline")
    assert result.outcome == "stabilised" and result.likely_to_fail_day is None


def test_a_slow_run_still_leaking_at_the_end_is_under_stress_not_stabilised():
    result = run("universal", "slow_burn")
    last = result.table.iloc[-1]
    calm = CONFIG.model.stabilised_outflow_share.value * result.table["deposits_total"].iloc[0]
    assert last["liquidity_buffer"] > 0 and not last["flag_active"]
    assert last["outflow_total"] > calm
    assert result.outcome == "under_stress"


# ------------------------------------------------- milder cyber outage


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_cyber_outage_is_clearly_milder_than_the_fast_run(bank):
    fast = run(bank, "fast_run").table["cum_deposit_loss_pct"]
    cyber_run = run(bank, "cyber_outage")
    cyber = cyber_run.table["cum_deposit_loss_pct"]
    assert cyber.diff().max() < 0.6 * fast.diff().max()  # the worst single day
    assert cyber_run.failure_day is None  # nobody defaults on a technical outage alone
    assert CONFIG.scenarios["cyber_outage"].severity.value < 0.5 * (
        CONFIG.scenarios["fast_run"].severity.value
    )


def test_release_of_the_queue_is_the_worst_day_of_the_cyber_outage():
    # Without weekends, so that the day-by-day decline can be checked.
    table = run("universal", "cyber_outage", overrides={"weekend_rules_on": 0}).table.set_index("day")
    end = int(CONFIG.scenarios["cyber_outage"].overrides["payment_outage_end_day"].value)
    daily = table["cum_deposit_loss_pct"].diff()
    assert daily.idxmax() == end + 1
    assert daily.max() < 15.0  # was 41% before phase B2
    # Afterwards the pressure eases: every day is calmer than the one before.
    after = daily.loc[end + 2:]
    assert (after.diff().dropna() < 0).all()
    assert after.loc[end + 12:].max() < 1.0


def test_universal_bank_absorbs_the_cyber_outage_with_a_large_buffer_left():
    result = run("universal", "cyber_outage")
    last = result.table.iloc[-1]
    assert result.failure_day is None
    assert last["liquidity_buffer"] > 0.1 * result.table["deposits_total"].iloc[0]
    assert not last["flag_active"]
