"""CLI: python -m engine.run --bank digital --scenario fast_run"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.model import SimulationResult, simulate

# column in the result table -> (header, format)
_COLUMNS = {
    "day": ("day", "{:d}"),
    "dep_insured_retail": ("dep_ins", "{:.2f}"),
    "dep_uninsured_retail": ("dep_unins", "{:.2f}"),
    "dep_corporate": ("dep_corp", "{:.2f}"),
    "dep_financial": ("dep_fin", "{:.2f}"),
    "outflow_total": ("outflow", "{:.2f}"),
    "funded_cash": ("f_cash", "{:.2f}"),
    "funded_hqla": ("f_hqla", "{:.2f}"),
    "funded_central_bank": ("f_cb", "{:.2f}"),
    "funded_fire_sale": ("f_fire", "{:.2f}"),
    "unmet_outflow": ("unmet", "{:.2f}"),
    "liquidity_buffer": ("buffer", "{:.2f}"),
    "lcr_proxy": ("lcr", "{:.0%}"),
    "survival_days": ("surv_d", "{:.1f}"),
    "cum_deposit_loss_pct": ("dep_loss%", "{:.1f}"),
    "cet1_ratio": ("cet1", "{:.1%}"),
    "unencumbered_collateral": ("unenc_coll", "{:.2f}"),
    "status": ("status", "{}"),
}


def format_table(result: SimulationResult, horizon: int) -> str:
    table = result.table[list(_COLUMNS)].copy()
    for column, (_, fmt) in _COLUMNS.items():
        if column == "survival_days":
            table[column] = [
                f">{horizon}" if math.isinf(v) or v > horizon else fmt.format(v)
                for v in table[column]
            ]
        elif column == "lcr_proxy":
            table[column] = ["n/a" if math.isinf(v) else fmt.format(v) for v in table[column]]
        else:
            table[column] = [fmt.format(v) for v in table[column]]
    table.columns = [header for header, _ in _COLUMNS.values()]
    return table.to_string(index=False)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one deterministic crisis simulation.")
    parser.add_argument("--bank", required=True, help="bank key in config/banks.yaml")
    parser.add_argument("--scenario", required=True, help="scenario key in config/scenarios.yaml")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    args = parser.parse_args(argv)

    config = load_config(args.config_dir)
    if args.bank not in config.banks:
        parser.error(f"unknown bank '{args.bank}'. Available: {', '.join(config.banks)}")
    if args.scenario not in config.scenarios:
        parser.error(
            f"unknown scenario '{args.scenario}'. Available: {', '.join(config.scenarios)}"
        )
    bank = config.banks[args.bank]
    scenario = config.scenarios[args.scenario]
    result = simulate(bank, config.model, scenario)
    horizon = int(config.model.horizon_days.value)

    print(f"Bank: {bank.name} | Scenario: {scenario.name} | severity {scenario.severity.value:g}")
    print(f"Amounts in {bank.unit}. Deterministic run, central parameter values.")
    print()
    print(format_table(result, horizon))
    print()
    print("Events")
    for event in result.events:
        print(f"  day {event.day:>2}  {event.kind:<18} {event.detail}")
    if not result.events:
        print("  none")
    print()
    if result.failure_day is None:
        print(f"Outcome: no failure within {horizon} days.")
    else:
        need = result.funding_need
        print(f"Outcome: failing or likely to fail on day {result.failure_day}.")
        print(
            f"Funding need in resolution ({need.days} days): {need.need:.2f} {bank.unit} "
            f"= projected outflows {need.projected_outflows:.2f} "
            f"- remaining liquidity {need.remaining_liquidity:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
