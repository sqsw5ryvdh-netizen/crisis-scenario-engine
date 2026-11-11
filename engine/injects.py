"""Exercise script ("injects") generator (spec section 4.3).

Pure functions: a simulation result plus the templates of config/injects.yaml give, for
each key day, what each actor sees and which decision is due.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from engine.model import SimulationResult, uninsured_share
from engine.schema import ACTORS, INJECT_EVENTS, Bank, InjectsConfig, ModelParams, Scenario

_TRIGGER_KINDS = ("recovery", "early_intervention", "likely_to_fail", "failure")
_FIRST_USE = {
    "first_hqla_sale": "funded_hqla",
    "first_central_bank": "funded_central_bank",
    "first_fire_sale": "funded_fire_sale",
}
_TINY = 1e-9


@dataclass(frozen=True)
class Inject:
    actor: str
    actor_name: str
    sees: str
    decision: str


@dataclass(frozen=True)
class KeyEvent:
    kind: str
    title: str
    situation: str
    injects: tuple[Inject, ...]


@dataclass(frozen=True)
class KeyDay:
    day: int
    figures: tuple[tuple[str, str], ...]  # (label, formatted value)
    events: tuple[KeyEvent, ...]


@dataclass(frozen=True)
class ExerciseScript:
    bank_name: str
    scenario_name: str
    unit: str
    settings: tuple[tuple[str, str], ...]  # (label, value)
    summary: str
    key_days: tuple[KeyDay, ...]
    glossary: tuple[tuple[str, str], ...]
    disclaimer: str


def _amount(value: float) -> str:
    return f"{value:,.2f}"


def _percent(ratio: float, digits: int = 1) -> str:
    return "n/a" if math.isinf(ratio) else f"{ratio * 100:.{digits}f}%"


def _survival(value: float, horizon: int) -> str:
    if math.isinf(value) or value > horizon:
        return f"more than {horizon}"
    return f"{value:.1f}"


def _number(value: float) -> str:
    return f"{value:g}"


def _context(
    row, bank: Bank, scenario: Scenario, result: SimulationResult, horizon: int
) -> dict[str, str]:
    need = result.funding_need
    return {
        "bank_name": bank.name,
        "scenario_name": scenario.name,
        "unit": bank.unit,
        "day": str(int(row.day)),
        "headline": "",
        "trigger_reason": "",
        "deposits_total": _amount(row.deposits_total),
        "insured_deposits": _amount(row.dep_insured_retail),
        "deposit_loss_pct": f"{row.cum_deposit_loss_pct:.1f}%",
        "outflow_today": _amount(row.outflow_total),
        "queued_withdrawals": _amount(row.queued_withdrawals),
        "liquidity_buffer": _amount(row.liquidity_buffer),
        "lcr_pct": _percent(row.lcr_proxy, 0),
        "survival_days": _survival(row.survival_days, horizon),
        "cet1_ratio_pct": _percent(row.cet1_ratio),
        "cet1_requirement_pct": _percent(bank.capital.capital_requirement_ratio),
        "unencumbered_collateral": _amount(row.unencumbered_collateral),
        "central_bank_funding": _amount(row.central_bank_funding),
        "central_bank_drawn_today": _amount(row.funded_central_bank),
        "hqla_sold_today": _amount(row.funded_hqla),
        "fire_sale_today": _amount(row.funded_fire_sale),
        "loss_today": _amount(row.loss),
        "unmet_outflow": _amount(row.unmet_outflow),
        "funding_need": _amount(need.need) if need else "n/a",
        "funding_need_days": str(need.days) if need else "n/a",
        "failure_day": str(result.failure_day) if result.failure_day is not None else "none",
        "horizon_days": str(horizon),
    }


def _figures(context: dict[str, str]) -> tuple[tuple[str, str], ...]:
    unit = context["unit"]
    return (
        (f"Deposits ({unit})", context["deposits_total"]),
        ("Deposits lost since the start", context["deposit_loss_pct"]),
        (f"Outflow today ({unit})", context["outflow_today"]),
        (f"Liquidity buffer ({unit})", context["liquidity_buffer"]),
        ("LCR proxy", context["lcr_pct"]),
        ("Survival horizon (days)", context["survival_days"]),
        ("CET1 ratio", context["cet1_ratio_pct"]),
    )


def _key_events(
    result: SimulationResult, params: ModelParams, last_day: int
) -> dict[int, list[tuple[str, dict[str, str]]]]:
    """Map each key day to its events: (kind, extra placeholder values)."""
    found: dict[int, list[tuple[str, dict[str, str]]]] = {}

    def add(day: int, kind: str, **extra: str) -> None:
        if 0 <= day <= last_day:
            found.setdefault(day, []).append((kind, extra))

    add(0, "opening")
    outage_start = int(params.payment_outage_start_day.value)
    if outage_start >= 1:
        add(outage_start, "outage_start")
        add(int(params.payment_outage_end_day.value) + 1, "outage_end")
    for event in result.events:
        if event.kind == "news":
            add(event.day, "news", headline=event.detail)
        elif event.kind in _TRIGGER_KINDS:
            add(event.day, event.kind, trigger_reason=event.detail)
    for kind, column in _FIRST_USE.items():
        used = result.table[result.table[column] > _TINY]
        if not used.empty:
            add(int(used["day"].iloc[0]), kind)
    if result.failure_day is None:
        add(last_day, "end_no_failure")

    for events in found.values():
        events.sort(key=lambda item: INJECT_EVENTS.index(item[0]))
    return dict(sorted(found.items()))


def _settings(
    bank: Bank, scenario: Scenario, params: ModelParams, resolved: ModelParams
) -> tuple[tuple[str, str], ...]:
    severity = scenario.severity
    rows = [
        ("Bank archetype", bank.name),
        ("Trigger scenario", scenario.name),
        ("Scenario severity", f"{_number(severity.value)} ({severity.unit})"),
        ("Uninsured deposits", f"{uninsured_share(bank) * 100:.0f}% of deposits"),
    ]
    for name in type(params).model_fields:
        default, used = getattr(params, name), getattr(resolved, name)
        if abs(used.value - default.value) > 1e-12:
            label = name.replace("_", " ").capitalize()
            rows.append(
                (label, f"{_number(used.value)} {used.unit} (default {_number(default.value)})")
            )
    return tuple(rows)


def build_script(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    result: SimulationResult,
    templates: InjectsConfig,
    horizon: Optional[int] = None,
) -> ExerciseScript:
    """Build the exercise script for one simulation run.

    `bank`, `params` and `scenario` must be the ones passed to `simulate` for `result`.
    """
    resolved = params.with_overrides(scenario.overrides)
    horizon = int(resolved.horizon_days.value) if horizon is None else horizon
    rows = {int(row.day): row for row in result.table.itertuples()}
    last_day = max(rows)

    key_days = []
    for day, events in _key_events(result, resolved, last_day).items():
        base = _context(rows[day], bank, scenario, result, horizon)
        key_events = []
        for kind, extra in events:
            context = {**base, **extra}
            template = templates.events[kind]
            injects = tuple(
                Inject(
                    actor=actor,
                    actor_name=templates.actors[actor],
                    sees=template.injects[actor].sees.format(**context),
                    decision=template.injects[actor].decision.format(**context),
                )
                for actor in ACTORS
                if actor in template.injects
            )
            key_events.append(
                KeyEvent(
                    kind=kind,
                    title=template.title.format(**context),
                    situation=template.situation.format(**context),
                    injects=injects,
                )
            )
        key_days.append(KeyDay(day=day, figures=_figures(base), events=tuple(key_events)))

    final = _context(rows[last_day], bank, scenario, result, horizon)
    summary = templates.summary.failure if result.failure_day is not None else (
        templates.summary.no_failure
    )
    return ExerciseScript(
        bank_name=bank.name,
        scenario_name=scenario.name,
        unit=bank.unit,
        settings=_settings(bank, scenario, params, resolved),
        summary=summary.format(**final),
        key_days=tuple(key_days),
        glossary=tuple((entry.term, entry.definition) for entry in templates.glossary),
        disclaimer=templates.disclaimer,
    )
