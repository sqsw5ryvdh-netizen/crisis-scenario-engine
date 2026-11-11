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
                    "Unit": "ratio of RWA" if key == "capital_requirement_ratio" else bank.unit,
                    "Source": source,
                }
            )
    return pd.DataFrame(rows)


def assumption_counts(*tables: pd.DataFrame) -> tuple[int, int]:
    """(number of rows flagged ASSUMPTION, total rows) across the tables."""
    flagged = sum(int((table["Status"] == FLAG).sum()) for table in tables)
    return flagged, sum(len(table) for table in tables)
