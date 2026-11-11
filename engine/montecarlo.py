"""Monte Carlo over the uncertain parameters (spec section 3.6).

Pure functions. Each run draws every sampled parameter from a triangular distribution
(low, central value, high), independently, and runs the deterministic simulation.
The only randomness is in the parameters; the seed is always passed explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from engine.model import simulate
from engine.schema import Bank, ModelParams, Parameter, Scenario

# Model parameters that are sampled when they have a low/high range.
SAMPLED_PARAMETERS = (
    "base_runoff_insured_retail",
    "base_runoff_uninsured_retail",
    "base_runoff_corporate",
    "base_runoff_financial",
    "panic_max_multiplier",
    "panic_steepness",
    "panic_midpoint",
    "wholesale_rollover_sensitivity",
    "hqla_level1_haircut",
    "hqla_level2_haircut",
    "central_bank_haircut",
    "central_bank_mobilisation_lag_days",
    "fire_sale_discount",
    "fire_sale_daily_cap_share",
    "panic_decay",
)
# The archetype's run-off speed factor is sampled too (it belongs to the bank, not the model).
SPEED_FACTOR = "runoff_speed_factor"
# Whole-number parameters: draws are rounded, and their range shifts additively.
INTEGER_PARAMETERS = ("central_bank_mobilisation_lag_days",)
# Daily series kept for the uncertainty bands.
BAND_SERIES = ("liquidity_buffer", "deposits_total", "cet1_ratio")


@dataclass(frozen=True)
class MonteCarloResult:
    n_runs: int
    seed: int
    horizon: int
    failure_days: np.ndarray  # payments default day per run; NaN = none within the horizon
    funding_needs: np.ndarray  # one per run; NaN = no failure
    series: dict[str, np.ndarray]  # name -> array (n_runs, horizon + 1)
    draws: pd.DataFrame  # sampled value of each parameter, one row per run


@dataclass(frozen=True)
class MonteCarloSummary:
    n_runs: int
    fail_share: float
    # Percentiles of the failure day over all runs; None = beyond the horizon.
    failure_day_low: Optional[int]
    failure_day_median: Optional[int]
    failure_day_high: Optional[int]
    # Percentiles of the funding need over the runs that fail; None if no run fails.
    funding_need_low: Optional[float]
    funding_need_median: Optional[float]
    funding_need_high: Optional[float]


def _upper_bound(name: str) -> float:
    if name.endswith(("_haircut", "_discount")):
        return 0.99
    if name.endswith("_share") or name.startswith("base_runoff_"):
        return 1.0
    return math.inf


def recentre(name: str, param: Parameter, value: float) -> Parameter:
    """Copy of `param` with a new central value and its range moved along with it.

    The range keeps its width relative to the central value (additively for whole-number
    parameters or a zero central value), clipped to what the parameter allows.
    """
    if param.low is None:
        return param.model_copy(update={"value": value})
    if name in INTEGER_PARAMETERS or param.value == 0:
        low = value + (param.low - param.value)
        high = value + (param.high - param.value)
    else:
        low = value * param.low / param.value
        high = value * param.high / param.value
    bound = _upper_bound(name)
    low = min(max(0.0, low), value)
    high = max(min(bound, high), value)
    return param.model_copy(update={"value": value, "low": low, "high": high})


def _draw(rng: np.random.Generator, param: Parameter, n_runs: int, integer: bool) -> np.ndarray:
    if param.low is None or param.high <= param.low:
        return np.full(n_runs, float(param.value))
    values = rng.triangular(param.low, param.value, param.high, size=n_runs)
    return np.rint(values) if integer else values


def run_monte_carlo(
    bank: Bank, params: ModelParams, scenario: Scenario, n_runs: int, seed: int
) -> MonteCarloResult:
    """Run `n_runs` simulations with sampled parameters. Same inputs and seed, same output."""
    rng = np.random.default_rng(seed)
    resolved = params.with_overrides(scenario.overrides)
    horizon = int(resolved.horizon_days.value)

    # Draw in a fixed order so that results are reproducible.
    draws = {"severity": _draw(rng, scenario.severity, n_runs, integer=False)}
    for name in SAMPLED_PARAMETERS:
        draws[name] = _draw(rng, getattr(resolved, name), n_runs, name in INTEGER_PARAMETERS)
    draws[SPEED_FACTOR] = _draw(rng, bank.runoff_speed_factor, n_runs, integer=False)

    failure_days = np.full(n_runs, np.nan)
    funding_needs = np.full(n_runs, np.nan)
    series = {name: np.empty((n_runs, horizon + 1)) for name in BAND_SERIES}

    for i in range(n_runs):
        overrides = dict(scenario.overrides)
        for name in SAMPLED_PARAMETERS:
            overrides[name] = getattr(resolved, name).model_copy(
                update={"value": float(draws[name][i])}
            )
        run_scenario = scenario.model_copy(
            update={
                "severity": scenario.severity.model_copy(
                    update={"value": float(draws["severity"][i])}
                ),
                "overrides": overrides,
            }
        )
        run_bank = bank.model_copy(
            update={
                SPEED_FACTOR: bank.runoff_speed_factor.model_copy(
                    update={"value": float(draws[SPEED_FACTOR][i])}
                )
            }
        )
        result = simulate(run_bank, params, run_scenario)
        if result.failure_day is not None:
            failure_days[i] = result.failure_day
            funding_needs[i] = result.funding_need.need
        length = len(result.table)
        for name in BAND_SERIES:
            values = result.table[name].to_numpy()
            series[name][i, :length] = values
            # A failed bank is frozen: its last values are carried forward.
            series[name][i, length:] = values[-1]

    return MonteCarloResult(
        n_runs=n_runs,
        seed=seed,
        horizon=horizon,
        failure_days=failure_days,
        funding_needs=funding_needs,
        series=series,
        draws=pd.DataFrame(draws),
    )


def summarise(mc: MonteCarloResult, lower: float = 5, upper: float = 95) -> MonteCarloSummary:
    beyond = mc.horizon + 1
    days = np.where(np.isnan(mc.failure_days), beyond, mc.failure_days)

    def day_at(q: float) -> Optional[int]:
        value = int(np.percentile(days, q, method="nearest"))
        return None if value >= beyond else value

    needs = mc.funding_needs[~np.isnan(mc.funding_needs)]

    def need_at(q: float) -> Optional[float]:
        return float(np.percentile(needs, q)) if len(needs) else None

    return MonteCarloSummary(
        n_runs=mc.n_runs,
        fail_share=float(np.mean(~np.isnan(mc.failure_days))),
        failure_day_low=day_at(lower),
        failure_day_median=day_at(50),
        failure_day_high=day_at(upper),
        funding_need_low=need_at(lower),
        funding_need_median=need_at(50),
        funding_need_high=need_at(upper),
    )


def bands(mc: MonteCarloResult, lower: float = 5, upper: float = 95) -> dict[str, pd.DataFrame]:
    """Per series: day, low, median and high across runs (percentiles per day)."""
    out = {}
    for name, values in mc.series.items():
        low, median, high = np.percentile(values, [lower, 50, upper], axis=0)
        out[name] = pd.DataFrame(
            {"day": np.arange(mc.horizon + 1), "low": low, "median": median, "high": high}
        )
    return out


def failure_day_counts(mc: MonteCarloResult) -> pd.DataFrame:
    """Number of runs failing on each day 0..horizon, plus the runs that do not fail."""
    days = mc.failure_days[~np.isnan(mc.failure_days)].astype(int)
    counts = np.bincount(days, minlength=mc.horizon + 1)
    frame = pd.DataFrame({"day": np.arange(mc.horizon + 1), "runs": counts})
    frame.attrs["no_failure"] = int(np.isnan(mc.failure_days).sum())
    return frame


def describe_failure_day(summary: MonteCarloSummary, horizon: int) -> str:
    """E.g. 'day 7 to 14, median 9' ('>30' marks a percentile beyond the horizon)."""

    def text(day: Optional[int]) -> str:
        return f">{horizon}" if day is None else str(day)

    return (
        f"day {text(summary.failure_day_low)} to {text(summary.failure_day_high)}, "
        f"median {text(summary.failure_day_median)}"
    )
