import time
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from app.assumptions import FLAG, assumption_counts, bank_table, model_table, scenario_table
from app.charts import build_failure_distribution, build_timeline
from engine.config import load_config
from engine.model import simulate
from engine.montecarlo import (
    BAND_SERIES,
    INTEGER_PARAMETERS,
    SAMPLED_PARAMETERS,
    bands,
    describe_failure_day,
    failure_day_counts,
    recentre,
    run_monte_carlo,
    summarise,
)
from engine.run import main
from engine.schema import Parameter

CONFIG = load_config()
HORIZON = int(CONFIG.model.horizon_days.value)
RUNS = int(CONFIG.model.monte_carlo_runs.value)
SEED = int(CONFIG.model.monte_carlo_seed.value)
ROOT = Path(__file__).resolve().parent.parent


def mc(bank="digital", scenario="fast_run", runs=200, seed=SEED):
    return run_monte_carlo(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], runs, seed)


def same(a, b) -> bool:
    return (
        np.array_equal(a.failure_days, b.failure_days, equal_nan=True)
        and np.array_equal(a.funding_needs, b.funding_needs, equal_nan=True)
        and all(np.array_equal(a.series[name], b.series[name]) for name in BAND_SERIES)
        and a.draws.equals(b.draws)
    )


# ------------------------------------------------- acceptance


def test_default_runs_take_under_five_seconds():
    assert RUNS == 500
    start = time.perf_counter()
    result = mc(runs=RUNS)
    elapsed = time.perf_counter() - start
    assert result.n_runs == RUNS
    assert elapsed < 5.0, f"{RUNS} runs took {elapsed:.2f}s"


def test_same_seed_gives_identical_results():
    assert same(mc(seed=7), mc(seed=7))


def test_different_seeds_give_different_results():
    assert not same(mc(seed=7), mc(seed=8))


# ------------------------------------------------- sampling


def test_every_draw_stays_inside_its_range():
    result = mc("universal", "geopolitical")
    resolved = CONFIG.model.with_overrides(CONFIG.scenarios["geopolitical"].overrides)
    ranges = {name: getattr(resolved, name) for name in SAMPLED_PARAMETERS}
    ranges["severity"] = CONFIG.scenarios["geopolitical"].severity
    assert set(result.draws.columns) == set(ranges)
    for name, param in ranges.items():
        assert param.low is not None, name
        assert result.draws[name].min() >= param.low - 1e-12, name
        assert result.draws[name].max() <= param.high + 1e-12, name
        assert result.draws[name].nunique() > 1, name
    for name in INTEGER_PARAMETERS:
        assert (result.draws[name] == result.draws[name].round()).all()


def test_scenario_override_range_is_used():
    result = mc("universal", "geopolitical")
    override = CONFIG.scenarios["geopolitical"].overrides["fire_sale_discount"]
    assert result.draws["fire_sale_discount"].min() >= override.low
    assert result.draws["fire_sale_discount"].max() <= override.high


def test_parameter_without_range_is_held_fixed():
    scenario = CONFIG.scenarios["fast_run"].model_copy(
        update={"severity": Parameter(value=3.0, unit="multiplier", source="ASSUMPTION")}
    )
    result = run_monte_carlo(CONFIG.banks["digital"], CONFIG.model, scenario, 50, SEED)
    assert (result.draws["severity"] == 3.0).all()


def test_recentre_moves_the_range_with_the_value():
    base = CONFIG.model.fire_sale_discount  # 0.20 in [0.10, 0.40]
    moved = recentre("fire_sale_discount", base, 0.30)
    assert (moved.value, moved.low, moved.high) == pytest.approx((0.30, 0.15, 0.60))
    lag = recentre(
        "central_bank_mobilisation_lag_days", CONFIG.model.central_bank_mobilisation_lag_days, 0
    )
    assert (lag.value, lag.low, lag.high) == (0, 0, 3)
    capped = recentre("central_bank_haircut", CONFIG.model.central_bank_haircut, 0.9)
    assert capped.high == 0.99 and capped.low <= 0.9
    fixed = recentre("panic_max_multiplier", Parameter(value=6, unit="x", source="s"), 8)
    assert fixed.value == 8 and fixed.low is None


# ------------------------------------------------- outputs


def test_series_cover_the_horizon_and_carry_failed_runs_forward():
    result = mc()
    failed = np.flatnonzero(~np.isnan(result.failure_days))
    assert len(failed) > 0
    for name in BAND_SERIES:
        values = result.series[name]
        assert values.shape == (result.n_runs, HORIZON + 1)
        assert not np.isnan(values).any()
        i = failed[0]
        day = int(result.failure_days[i])
        assert (values[i, day:] == values[i, day]).all()


def test_bands_are_ordered_and_contain_the_median():
    for frame in bands(mc()).values():
        assert len(frame) == HORIZON + 1
        assert (frame["low"] <= frame["median"] + 1e-12).all()
        assert (frame["median"] <= frame["high"] + 1e-12).all()


def test_summary_is_consistent_with_the_runs():
    result = mc()
    summary = summarise(result)
    assert summary.fail_share == pytest.approx(np.mean(~np.isnan(result.failure_days)))
    days = [summary.failure_day_low, summary.failure_day_median, summary.failure_day_high]
    numbers = [HORIZON + 1 if d is None else d for d in days]
    assert numbers == sorted(numbers)
    assert summary.funding_need_low <= summary.funding_need_median <= summary.funding_need_high
    text = describe_failure_day(summary, HORIZON)
    assert text.startswith("day ") and "median" in text


def test_summary_marks_percentiles_beyond_the_horizon():
    summary = summarise(mc("digital", "slow_burn"))
    assert 0 < summary.fail_share < 1
    assert summary.failure_day_high is None
    assert f">{HORIZON}" in describe_failure_day(summary, HORIZON)


def test_failure_day_counts_add_up_to_the_number_of_runs():
    result = mc("digital", "slow_burn")
    counts = failure_day_counts(result)
    assert counts["runs"].sum() + counts.attrs["no_failure"] == result.n_runs
    assert counts.attrs["no_failure"] == int(np.isnan(result.failure_days).sum())


# ------------------------------------------------- charts


def test_timeline_with_bands_adds_two_band_traces_per_panel():
    result = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    fig = build_timeline(result, HORIZON, "EUR bn", bands(mc()))
    band_traces = [trace for trace in fig.data if "band" in trace.name]
    assert len(band_traces) == 6
    assert sum(1 for trace in band_traces if trace.fill == "tonexty") == 3
    lines = [t for t in fig.data if t.mode == "lines" and "band" not in t.name]
    assert len(lines) == 3


def test_failure_distribution_chart_shows_all_runs():
    result = mc("digital", "slow_burn")
    fig = build_failure_distribution(failure_day_counts(result), HORIZON)
    assert sum(sum(trace.y) for trace in fig.data) == result.n_runs
    assert "No failure" in fig.layout.xaxis.ticktext


# ------------------------------------------------- assumptions page


def test_assumptions_tables_list_every_parameter():
    model = model_table(CONFIG)
    assert list(model["Parameter"]) == list(type(CONFIG.model).model_fields)
    sampled = set(model.loc[model["Sampled"] == "yes", "Parameter"])
    assert sampled == set(SAMPLED_PARAMETERS)
    scenarios = scenario_table(CONFIG)
    for scenario in CONFIG.scenarios.values():
        rows = scenarios[scenarios["Scenario"] == scenario.name]
        assert len(rows) == 1 + len(scenario.overrides) + len(scenario.news_events)
    banks = bank_table(CONFIG)
    assert set(banks["Bank"]) == {bank.name for bank in CONFIG.banks.values()}


def test_assumptions_are_flagged_and_counted():
    model = model_table(CONFIG)
    for name in type(CONFIG.model).model_fields:
        flagged = model.loc[model["Parameter"] == name, "Status"].iloc[0] == FLAG
        assert flagged == getattr(CONFIG.model, name).is_assumption
    flagged, total = assumption_counts(model, scenario_table(CONFIG), bank_table(CONFIG))
    assert 0 < flagged <= total


def test_assumptions_page_renders():
    at = AppTest.from_file(str(ROOT / "app" / "views" / "assumptions.py")).run(timeout=30)
    assert not at.exception
    assert len(at.dataframe) == 3
    assert "ASSUMPTION" in at.warning[0].value


# ------------------------------------------------- CLI


def test_cli_monte_carlo_is_reproducible(capsys):
    args = ["--bank", "digital", "--scenario", "fast_run", "--runs", "100", "--seed", "11"]
    assert main(args) == 0
    first = capsys.readouterr().out
    assert main(args) == 0
    second = capsys.readouterr().out
    assert "Monte Carlo: 100 runs, seed 11" in first
    assert first == second
