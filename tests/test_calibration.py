import importlib.util
from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from app.assumptions import FLAG, SOURCED, bank_table
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.model import initial_state, lcr_proxy
from engine.schema import Bank

ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config()
RAW = ROOT / "data" / "raw" / "TE2025" / "tr_oth.csv"


def load_script():
    spec = importlib.util.spec_from_file_location(
        "calibrate_te2025", ROOT / "scripts" / "calibrate_te2025.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_banks() -> dict:
    return yaml.safe_load((DEFAULT_CONFIG_DIR / "banks.yaml").read_text(encoding="utf-8"))


def test_line_source_for_unknown_line_is_rejected():
    data = raw_banks()["universal"]
    data["sources"]["gold"] = "somewhere"
    with pytest.raises(ValidationError, match="gold"):
        Bank(**data)


def test_source_of_falls_back_to_the_bank_source():
    bank = CONFIG.banks["universal"]
    assert "Transparency Exercise" in bank.source_of("cash_reserves")
    assert bank.source_of("hqla_level1") == "ASSUMPTION"
    assert CONFIG.banks["digital"].source_of("cash_reserves") == "ASSUMPTION"


def test_assumptions_page_separates_calibrated_and_assumed_lines():
    table = bank_table(CONFIG)
    universal = table[table["Bank"] == CONFIG.banks["universal"].name].set_index("Item")["Status"]
    for item in ("Cash and central bank reserves", "Deposits: corporate", "Deposits: financial",
                 "CET1 capital", "Risk-weighted assets", "Other liabilities"):
        assert universal[item] == SOURCED, item
    # Lines that rest even partly on an assumption stay flagged.
    for item in ("HQLA Level 1", "HQLA Level 2", "Other eligible collateral", "Illiquid assets",
                 "Deposits: insured retail", "Deposits: uninsured retail",
                 "Wholesale funding (total)", "Capital requirement"):
        assert universal[item] == FLAG, item
    digital = table[table["Bank"] == CONFIG.banks["digital"].name]
    assert (digital["Status"] == FLAG).all()


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_every_archetype_opens_above_the_recovery_lcr_threshold(bank):
    ratio = lcr_proxy(initial_state(CONFIG.banks[bank]), CONFIG.model)
    assert ratio > CONFIG.model.recovery_lcr_threshold.value


def test_archetype_block_balances_and_keeps_the_assumed_lines():
    script = load_script()
    median = pd.Series(
        {
            "cash": 0.10,
            "deposits_households": 0.30,
            "deposits_non_financial_corporations": 0.15,
            "deposits_general_governments": 0.02,
            "deposits_other_financial_corporations": 0.05,
            "deposits_credit_institutions": 0.04,
            "deposits_central_banks": 0.01,
            "debt_securities_issued": 0.15,
            "total_equity": 0.07,
            "cet1": 0.05,
            "total_risk_exposure_amount": 0.35,
        }
    )
    current = raw_banks()["universal"]
    block = script.archetype_block("universal", median, current)
    bank = Bank(name="x", unit="EUR bn", source="ASSUMPTION", **block)  # validates the identity
    assert bank.assets.total() == pytest.approx(sum(current["assets"].values()))
    assert bank.assets.hqla_level1 == current["assets"]["hqla_level1"]
    assert bank.assets.cash_reserves == pytest.approx(0.10 * bank.assets.total())
    deposits = bank.liabilities.deposits
    assert deposits.corporate == pytest.approx(0.17 * bank.assets.total())
    assert deposits.financial == pytest.approx(0.09 * bank.assets.total())
    old = current["liabilities"]["deposits"]
    assert deposits.insured_retail / (deposits.insured_retail + deposits.uninsured_retail) == (
        pytest.approx(old["insured_retail"] / (old["insured_retail"] + old["uninsured_retail"]), abs=1e-3)
    )
    assert bank.capital.capital_requirement_ratio == current["capital"]["capital_requirement_ratio"]


@pytest.mark.skipif(not RAW.exists(), reason="EBA raw file not downloaded (see docs/CALIBRATION.md)")
def test_config_matches_the_calibration_script_output():
    script = load_script()
    shares = script.load_shares()
    assert script.AGGREGATE_LEI not in shares.index
    large = shares["total_assets_mn"] >= script.LARGE_BANK_TOTAL_ASSETS_MN
    groups = {
        "universal": shares[large],
        "retail": shares[
            ~large & (shares["deposits_households"] >= script.RETAIL_HOUSEHOLD_DEPOSIT_SHARE)
        ],
    }
    current = raw_banks()
    for key, group in groups.items():
        median = group.drop(columns=["country", "total_assets_mn"]).median()
        block = script.archetype_block(key, median, current[key])
        for section in ("assets", "liabilities", "capital"):
            assert block[section] == current[key][section], (key, section)
