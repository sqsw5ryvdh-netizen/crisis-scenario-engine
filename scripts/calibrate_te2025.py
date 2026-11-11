"""Calibrate archetype proportions on the EBA 2025 EU-wide Transparency Exercise.

Usage (from the repo root):
    .venv/bin/python scripts/calibrate_te2025.py

Reads data/raw/TE2025/tr_oth.csv and TR_Metadata.xlsx (see docs/CALIBRATION.md for where
to get them and their checksums) and prints, for each calibrated archetype: the peer group,
the median share of total assets of each item, and the resulting block for config/banks.yaml.

Lines the data does not cover keep the ASSUMPTION proportions already in config/banks.yaml:
the HQLA and collateral shares of total assets, the insured share of retail deposits, the
shape of the wholesale maturity ladder, the capital requirement and the fictitious size.
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "TE2025"
PERIOD = "202506"
LARGE_BANK_TOTAL_ASSETS_MN = 250_000  # EUR mn: universal peer group at or above this
RETAIL_HOUSEHOLD_DEPOSIT_SHARE = 0.40  # of total assets: retail peer group at or above this
AGGREGATE_LEI = "XXXXXXXXXXXXXXXXXXXX"  # the "All Other banks" aggregate row, not a bank

# name -> (Item, Financial_instruments, Exposure); "0" = total / no breakdown.
ITEMS = {
    "total_assets": ("2521010", "0", "0"),
    "cash": ("2521001", "0", "0"),
    "deposits_households": ("2521215", "30", "401"),
    "deposits_non_financial_corporations": ("2521215", "30", "301"),
    "deposits_general_governments": ("2521215", "30", "102"),
    "deposits_other_financial_corporations": ("2521215", "30", "202"),
    "deposits_credit_institutions": ("2521215", "30", "201"),
    "deposits_central_banks": ("2521215", "30", "101"),
    "debt_securities_issued": ("2521215", "40", "0"),
    "total_equity": ("2521216", "0", "0"),
    "cet1": ("2520102", "0", "0"),
    "total_risk_exposure_amount": ("2520138", "0", "0"),
}
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def bank_names(path: Path) -> dict[str, str]:
    """LEI -> name from the 'List of Institutions' sheet (standard library only)."""
    archive = zipfile.ZipFile(path)
    strings = [
        "".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"]))
        for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", _NS)
    ]
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
    sheet = next(s for s in workbook.find("m:sheets", _NS) if s.get("name") == "List of Institutions")
    target = rels[sheet.get("{%s}id" % _NS["r"])]
    target = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
    names = {}
    for row in ET.fromstring(archive.read(target)).iter("{%s}row" % _NS["m"]):
        cells = {}
        for cell in row.findall("m:c", _NS):
            value = cell.find("m:v", _NS)
            if value is not None and cell.get("t") == "s":
                cells[re.match(r"[A-Z]+", cell.get("r")).group(0)] = strings[int(value.text)]
        if len(cells.get("C", "")) == 20:  # an LEI
            names[cells["C"]] = cells.get("D", "")
    return names


def load_shares() -> pd.DataFrame:
    """One row per bank: total assets (EUR mn) and each item as a share of total assets."""
    data = pd.read_csv(RAW / "tr_oth.csv", dtype=str)
    data = data[
        (data["Period"] == PERIOD)
        & (data["ASSETS_FV"] == "0")
        & (data["ASSETS_Stages"] == "0")
        & (data["LEI_Code"] != AGGREGATE_LEI)
    ]
    data = data.assign(Amount=pd.to_numeric(data["Amount"], errors="coerce"))
    columns = {}
    for name, (item, instrument, exposure) in ITEMS.items():
        rows = data[
            (data["Item"] == item)
            & (data["Financial_instruments"] == instrument)
            & (data["Exposure"] == exposure)
        ]
        columns[name] = rows.groupby("LEI_Code")["Amount"].sum(min_count=1)
    amounts = pd.DataFrame(columns).dropna()
    shares = amounts.div(amounts["total_assets"], axis=0).drop(columns="total_assets")
    shares.insert(0, "total_assets_mn", amounts["total_assets"])
    country = data.drop_duplicates("LEI_Code").set_index("LEI_Code")["NSA"]
    shares.insert(0, "country", country.reindex(shares.index))
    return shares


def archetype_block(key: str, median: pd.Series, current: dict) -> dict:
    """Balance sheet for config/banks.yaml: data-driven lines plus the kept assumptions."""
    assets, liabilities = current["assets"], current["liabilities"]
    old_deposits = liabilities["deposits"]
    total = sum(assets.values())  # fictitious size is kept
    digits = 1 if total >= 500 else 2

    def amount(share: float) -> float:
        return round(float(share) * total, digits)

    cash = amount(median["cash"])
    kept_assets = {k: assets[k] for k in ("hqla_level1", "hqla_level2", "other_eligible_collateral")}
    illiquid = round(total - cash - sum(kept_assets.values()), digits)

    retail = amount(median["deposits_households"])
    insured_share = old_deposits["insured_retail"] / (
        old_deposits["insured_retail"] + old_deposits["uninsured_retail"]
    )
    insured = round(float(retail * insured_share), digits)
    corporate = amount(
        median["deposits_non_financial_corporations"] + median["deposits_general_governments"]
    )
    financial = amount(
        median["deposits_other_financial_corporations"] + median["deposits_credit_institutions"]
    )

    old_wholesale = liabilities["wholesale_funding"]
    old_total = sum(old_wholesale["maturing"].values()) + old_wholesale["beyond_horizon"]
    wholesale = amount(median["debt_securities_issued"])
    maturing = {
        day: round(float(value / old_total * wholesale), digits)
        for day, value in old_wholesale["maturing"].items()
    }
    beyond = round(wholesale - sum(maturing.values()), digits)

    cet1 = amount(median["cet1"])
    deposits_total = insured + round(retail - insured, digits) + corporate + financial
    other = round(total - deposits_total - wholesale - cet1, digits)
    if min(illiquid, other, beyond) < 0:
        raise ValueError(f"{key}: a remainder line is negative")
    return {
        "assets": {"cash_reserves": cash, **kept_assets, "illiquid_assets": illiquid},
        "liabilities": {
            "deposits": {
                "insured_retail": insured,
                "uninsured_retail": round(retail - insured, digits),
                "corporate": corporate,
                "financial": financial,
            },
            "wholesale_funding": {"maturing": maturing, "beyond_horizon": beyond},
            "other_liabilities": other,
        },
        "capital": {
            "cet1": cet1,
            "rwa": amount(median["total_risk_exposure_amount"]),
            "capital_requirement_ratio": current["capital"]["capital_requirement_ratio"],
        },
    }


def main() -> int:
    for name in ("tr_oth.csv", "TR_Metadata.xlsx"):
        path = RAW / name
        if not path.exists():
            print(f"Missing {path}. See docs/CALIBRATION.md for the download location.")
            return 1
        print(f"{name}: sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")

    shares = load_shares()
    names = bank_names(RAW / "TR_Metadata.xlsx")
    current = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
    large = shares["total_assets_mn"] >= LARGE_BANK_TOTAL_ASSETS_MN
    groups = {
        "universal": shares[large],
        "retail": shares[~large & (shares["deposits_households"] >= RETAIL_HOUSEHOLD_DEPOSIT_SHARE)],
    }
    print(f"\nPeriod {PERIOD}: {len(shares)} banks report every item used.")
    pd.set_option("display.width", 200)
    for key, group in groups.items():
        median = group.drop(columns=["country", "total_assets_mn"]).median()
        print(f"\n===== {key}: {len(group)} banks, {group['country'].nunique()} countries")
        for lei, row in group.sort_values("total_assets_mn", ascending=False).iterrows():
            print(f"  {row['country']}  {lei}  {names.get(lei, '?')}")
        print("\nMedian share of total assets:")
        print(median.round(4).to_string())
        print("\nBlock for config/banks.yaml:")
        print(yaml.safe_dump(archetype_block(key, median, current[key]), sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
