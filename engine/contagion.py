"""Simple one-way contagion from a first bank to a second bank (spec M5c).

Pure functions. Each trigger stage reached by the first bank becomes, one day later, a
news event for the second bank whose panic shock is scaled by a similarity factor.
The second bank is then simulated with the ordinary daily loop: nothing flows back to
the first bank.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.model import SimulationResult, simulate
from engine.schema import Bank, ModelParams, NewsEvent, Scenario

# Trigger stage of the first bank -> (shock parameter, headline for the second bank).
_STAGES = {
    "recovery": (
        "contagion_shock_recovery",
        "Another bank ({name}) is reported to be losing deposits fast.",
    ),
    "early_intervention": (
        "contagion_shock_early_intervention",
        "Supervisors are reported to have intervened at another bank ({name}).",
    ),
    "failure": (
        "contagion_shock_failure",
        "Another bank ({name}) is declared failing or likely to fail.",
    ),
}


@dataclass(frozen=True)
class ContagionResult:
    similarity: float
    events: tuple[NewsEvent, ...]  # what reaches the second bank, and when
    first: SimulationResult
    second_alone: SimulationResult  # second bank without contagion
    second: SimulationResult  # second bank with contagion


def similarity_factor(same_archetype: bool, same_country: bool, params: ModelParams) -> float:
    """Between 0 (unrelated banks, no contagion) and 1 (same archetype and country)."""
    factor = 0.0
    if same_archetype:
        factor += params.contagion_similarity_same_archetype.value
    if same_country:
        factor += params.contagion_similarity_same_country.value
    return min(1.0, factor)


def contagion_events(
    first: SimulationResult, first_name: str, similarity: float, params: ModelParams
) -> tuple[NewsEvent, ...]:
    """News events for the second bank, one day after each trigger of the first bank."""
    if not 0.0 <= similarity <= 1.0:
        raise ValueError("similarity must be between 0 and 1")
    horizon = int(params.horizon_days.value)
    events = []
    for event in first.events:
        if event.kind not in _STAGES:
            continue
        shock_name, headline = _STAGES[event.kind]
        shock = getattr(params, shock_name).value * similarity
        day = event.day + 1
        if shock > 0 and day <= horizon:
            events.append(
                NewsEvent(day=day, headline=headline.format(name=first_name), panic_shock=shock)
            )
    return tuple(events)


def simulate_contagion(
    first_bank: Bank,
    first_scenario: Scenario,
    second_bank: Bank,
    second_scenario: Scenario,
    params: ModelParams,
    similarity: float,
) -> ContagionResult:
    """Simulate the first bank, then the second bank with and without contagion."""
    first = simulate(first_bank, params, first_scenario)
    events = contagion_events(first, first_bank.name, similarity, params)
    hit_scenario = second_scenario.model_copy(
        update={"news_events": list(second_scenario.news_events) + list(events)}
    )
    return ContagionResult(
        similarity=similarity,
        events=events,
        first=first,
        second_alone=simulate(second_bank, params, second_scenario),
        second=simulate(second_bank, params, hit_scenario),
    )
