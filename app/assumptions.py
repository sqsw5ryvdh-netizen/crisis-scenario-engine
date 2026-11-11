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
        items = [
            ("Cash and central bank reserves", bank.assets.cash_reserves, bank.unit),
            ("HQLA Level 1", bank.assets.hqla_level1, bank.unit),
            ("HQLA Level 2", bank.assets.hqla_level2, bank.unit),
            ("Other eligible collateral", bank.assets.other_eligible_collateral, bank.unit),
            ("Illiquid assets", bank.assets.illiquid_assets, bank.unit),
            ("Deposits: insured retail", deposits.insured_retail, bank.unit),
            ("Deposits: uninsured retail", deposits.uninsured_retail, bank.unit),
            ("Deposits: corporate", deposits.corporate, bank.unit),
            ("Deposits: financial", deposits.financial, bank.unit),
            ("Wholesale funding maturing within the horizon", sum(wholesale.maturing.values()),
             bank.unit),
            ("Wholesale funding beyond the horizon", wholesale.beyond_horizon, bank.unit),
            ("Other liabilities", bank.liabilities.other_liabilities, bank.unit),
            ("CET1 capital", bank.capital.cet1, bank.unit),
            ("Risk-weighted assets", bank.capital.rwa, bank.unit),
            ("Capital requirement", bank.capital.capital_requirement_ratio, "ratio of RWA"),
        ]
        status = FLAG if bank.source.strip().upper() == ASSUMPTION else SOURCED
        for item, value, unit in items:
            rows.append(
                {
                    "Bank": bank.name,
                    "Status": status,
                    "Item": item,
                    "Value": value,
                    "Unit": unit,
                    "Source": bank.source,
                }
            )
    return pd.DataFrame(rows)


def assumption_counts(*tables: pd.DataFrame) -> tuple[int, int]:
    """(number of rows flagged ASSUMPTION, total rows) across the tables."""
    flagged = sum(int((table["Status"] == FLAG).sum()) for table in tables)
    return flagged, sum(len(table) for table in tables)
