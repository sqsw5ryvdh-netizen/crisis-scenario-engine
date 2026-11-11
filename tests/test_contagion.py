import itertools
import math

import pytest

from app.charts import build_contagion_chart
from engine.config import load_config
from engine.contagion import contagion_events, similarity_factor, simulate_contagion
from engine.model import simulate

CONFIG = load_config()
HORIZON = int(CONFIG.model.horizon_days.value)
BASELINE = CONFIG.scenarios["baseline"]
TOL = 1e-9


def run(first="digital", second="retail", similarity=1.0, scenario="fast_run"):
    return simulate_contagion(
        CONFIG.banks[first], CONFIG.scenarios[scenario], CONFIG.banks[second], BASELINE,
        CONFIG.model, similarity,
    )


def test_similarity_factor_adds_components_between_zero_and_one():
    p = CONFIG.model
    assert similarity_factor(False, False, p) == 0.0
    assert similarity_factor(True, False, p) == p.contagion_similarity_same_archetype.value
    assert similarity_factor(False, True, p) == p.contagion_similarity_same_country.value
    assert similarity_factor(True, True, p) == 1.0


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_baseline_bank_stays_normal_on_its_own(bank):
    result = simulate(CONFIG.banks[bank], CONFIG.model, BASELINE)
    assert result.failure_day is None
    assert (result.table["status"] == "normal").all()


@pytest.mark.parametrize("second", list(CONFIG.banks))
def test_zero_similarity_is_exactly_the_standalone_run(second):
    result = run(second=second, similarity=0.0)
    assert result.events == ()
    assert result.second.table.equals(result.second_alone.table)
    assert result.second.events == result.second_alone.events


def test_shocks_arrive_one_day_after_each_trigger_scaled_by_similarity():
    result = run(similarity=0.5)
    triggers = [e for e in result.first.events if e.kind in ("recovery", "early_intervention", "failure")]
    assert [event.day for event in result.events] == [t.day + 1 for t in triggers]
    p = CONFIG.model
    expected = [
        p.contagion_shock_recovery.value,
        p.contagion_shock_early_intervention.value,
        p.contagion_shock_failure.value,
    ]
    assert [event.panic_shock for event in result.events] == pytest.approx([0.5 * v for v in expected])
    full = run(similarity=1.0)
    assert [e.panic_shock for e in full.events] == pytest.approx(expected)


def test_no_contagion_when_the_first_bank_reaches_no_trigger():
    result = run(scenario="baseline")
    assert result.events == ()
    assert result.second.table.equals(result.second_alone.table)


def test_shock_beyond_the_horizon_is_dropped():
    first = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    short = CONFIG.model.with_overrides(
        {"horizon_days": CONFIG.model.horizon_days.model_copy(update={"value": first.failure_day})}
    )
    events = contagion_events(first, "Bank", 1.0, short)
    assert all(event.day <= first.failure_day for event in events)
    assert len(events) == 2


def test_similarity_outside_zero_one_is_rejected():
    first = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    with pytest.raises(ValueError):
        contagion_events(first, "Bank", 1.5, CONFIG.model)


@pytest.mark.parametrize("first,second", list(itertools.product(CONFIG.banks, CONFIG.banks)))
def test_contagion_makes_the_second_bank_worse_and_more_similarity_never_helps(first, second):
    losses, days = [], []
    for step in range(0, 11):
        result = run(first, second, similarity=step / 10)
        losses.append(result.second.table["cum_deposit_loss_pct"].iloc[-1])
        days.append(math.inf if result.second.failure_day is None else result.second.failure_day)
    assert all(later >= earlier - TOL for earlier, later in zip(losses, losses[1:]))
    assert all(later <= earlier for earlier, later in zip(days, days[1:]))
    assert losses[-1] > losses[0]


@pytest.mark.parametrize("first,second", list(itertools.product(CONFIG.banks, CONFIG.banks)))
def test_second_bank_keeps_the_sanity_checks(first, second):
    table = run(first, second).second.table
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < TOL
    for column in ("cash", "deposits_total", "liquidity_buffer", "cet1", "illiquid_assets"):
        assert (table[column] >= -TOL).all()


def test_first_bank_is_not_affected_by_the_second():
    alone = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    assert run().first.table.equals(alone.table)


def test_contagion_chart_has_both_runs_and_a_marker_per_shock():
    result = run()
    fig = build_contagion_chart(result, HORIZON)
    names = [trace.name for trace in fig.data]
    assert names.count("With contagion") == 2 and names.count("Without contagion") == 2
    assert sum(1 for trace in fig.data if trace.showlegend) == 2
    labels = [a.text for a in fig.layout.annotations if a.text.startswith("Day ")]
    assert len(labels) == len(result.events)
