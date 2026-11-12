"""Tables for the assumptions page. No Streamlit imports, so they can be tested alone."""

from __future__ import annotations

import pandas as pd

from engine.montecarlo import SAMPLED_PARAMETERS
from engine.schema import ASSUMPTION, Config, Parameter

FLAG = "ASSUMPTION"
SOURCED = "Sourced"
COLUMNS = ["Status", "Parameter", "Value", "Low", "High", "Unit", "Sampled", "Source"]


def _row(name: str, param: Parameter, sampled: bool) -> dict:
    return {
        "Status": FLAG if param.is_assumption else SOURCED,
        "Parameter": name,
        "Value": param.value,
        "Low": param.low,
        "High": param.high,
        "Unit": param.unit,
        "Sampled": "yes" if sampled and param.low is not None else "no",
        "Source": param.source,
    }


def model_table(config: Config) -> pd.DataFrame:
    """Every parameter of config/model.yaml."""
    rows = [
        _row(name, getattr(config.model, name), name in SAMPLED_PARAMETERS)
        for name in type(config.model).model_fields
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def scenario_table(config: Config) -> pd.DataFrame:
    """Severity, parameter overrides and news shocks of every scenario."""
    rows = []
    for scenario in config.scenarios.values():
        entries = [("severity", scenario.severity, True)]
        entries += [
            (f"override: {name}", param, name in SAMPLED_PARAMETERS)
            for name, param in scenario.overrides.items()
        ]
        if scenario.fade is not None:
            entries += [
                (f"fade: {name}", getattr(scenario.fade, name), False)
                for name in type(scenario.fade).model_fields
            ]
        for name, param, sampled in entries:
            rows.append({"Scenario": scenario.name, **_row(name, param, sampled)})
        for event in scenario.news_events:
            # News shocks are part of the scenario design and have no source of their own.
            rows.append(
                {
                    "Scenario": scenario.name,
                    "Status": FLAG,
                    "Parameter": f"news shock, day {event.day}",
                    "Value": event.panic_shock,
                    "Low": None,
                    "High": None,
                    "Unit": "added to the panic input",
                    "Sampled": "no",
                    "Source": ASSUMPTION,
                }
            )
    return pd.DataFrame(rows, columns=["Scenario"] + COLUMNS)


def bank_table(config: Config) -> pd.DataFrame:
    """Opening balance sheet of every archetype."""
    rows = []
    for bank in config.banks.values():
        deposits = bank.liabilities.deposits
        wholesale = bank.liabilities.wholesale_funding
        # (label, line key for the source, value, unit)
        items = [
            ("Cash and central bank reserves", "cash_reserves", bank.assets.cash_reserves),
            ("HQLA Level 1", "hqla_level1", bank.assets.hqla_level1),
            ("HQLA Level 2", "hqla_level2", bank.assets.hqla_level2),
            ("Other eligible collateral", "other_eligible_collateral",
             bank.assets.other_eligible_collateral),
            ("Illiquid assets", "illiquid_assets", bank.assets.illiquid_assets),
            ("Deposits: insured retail", "insured_retail", deposits.insured_retail),
            ("Deposits: uninsured retail", "uninsured_retail", deposits.uninsured_retail),
            ("Deposits: corporate", "corporate", deposits.corporate),
            ("Deposits: financial", "financial", deposits.financial),
            ("Wholesale funding (total)", "wholesale_funding", wholesale.total()),
            ("Other liabilities", "other_liabilities", bank.liabilities.other_liabilities),
            ("CET1 capital", "cet1", bank.capital.cet1),
            ("Risk-weighted assets", "rwa", bank.capital.rwa),
            ("Capital requirement", "capital_requirement_ratio",
             bank.capital.capital_requirement_ratio),
            ("AT1 and Tier 2 (part of other liabilities)", "at1_t2", bank.resolution.at1_t2),
            ("Senior non-preferred debt (part of wholesale funding)", "senior_non_preferred",
             bank.resolution.senior_non_preferred),
            ("MREL requirement", "mrel_requirement_ratio",
             bank.resolution.mrel_requirement_ratio),
        ]
        for label, key, value in items:
            source = bank.source_of(key)
            rows.append(
                {
                    "Bank": bank.name,
                    # A line is flagged as soon as any part of it rests on an assumption.
                    "Status": FLAG if ASSUMPTION in source.upper() else SOURCED,
                    "Item": label,
                    "Value": value,
                    "Unit": "ratio of RWA" if key.endswith("_ratio") else bank.unit,
                    "Source": source,
                }
            )
        factor = bank.runoff_speed_factor
        rows.append(
            {
                "Bank": bank.name,
                "Status": FLAG if factor.is_assumption else SOURCED,
                "Item": "Run-off speed factor",
                "Value": factor.value,
                "Unit": factor.unit,
                "Source": factor.source,
            }
        )
    return pd.DataFrame(rows)


def decision_table(config: Config) -> pd.DataFrame:
    """Every number of config/decisions.yaml: timing rule and the effect of each option."""
    decisions = config.decisions
    rows = [
        {"Decision": "(all decisions)", **_row("lead_days", decisions.lead_days, False)},
        {
            "Decision": "Payout of covered deposits",
            **_row("payout_days_unprepared", decisions.payout_days_unprepared, False),
        },
    ]
    for decision in decisions.decisions.values():
        for option in decision.options.values():
            for name, param in option.params.items():
                rows.append(
                    {"Decision": decision.name, **_row(f"{option.name}: {name}", param, False)}
                )
    return pd.DataFrame(rows, columns=["Decision"] + COLUMNS)


def information_table(config: Config) -> pd.DataFrame:
    """What each actor knows and how late (config/decisions.yaml). All assumptions."""
    rows = []
    for actor, info in config.decisions.information.items():
        faster = ""
        if info.faster is not None:
            option = config.decisions.decisions[info.faster.decision].options[info.faster.option]
            faster = f"{info.faster.lag_days} after: {option.name}"
        rows.append(
            {
                "Status": FLAG,
                "Actor": config.script.actors[actor],
                "Figures from": info.source,
                "Delay (days)": info.lag_days,
                "Shorter delay": faster,
                "Informed from": info.informed_from or "the start",
                "Figures": ", ".join(info.figures),
            }
        )
    return pd.DataFrame(rows)


def assumption_counts(*tables: pd.DataFrame) -> tuple[int, int]:
    """(number of rows flagged ASSUMPTION, total rows) across the tables."""
    flagged = sum(int((table["Status"] == FLAG).sum()) for table in tables)
    return flagged, sum(len(table) for table in tables)
