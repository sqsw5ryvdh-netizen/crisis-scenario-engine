"""CLI: python -m engine.run --bank digital --scenario fast_run"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import OUTCOME_LABELS, choices_of, decision_log_rows, scorecard_rows
from engine.export import daily_table_to_csv, injects_to_csv, script_to_docx
from engine.injects import build_script
from engine.model import SimulationResult, simulate
from engine.montecarlo import describe_failure_day, run_monte_carlo, summarise

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
    "queued_withdrawals": ("queued", "{:.2f}"),
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
    parser.add_argument(
        "--export-dir", type=Path, help="write the exercise script (.docx) and data (.csv) here"
    )
    parser.add_argument(
        "--path", help="decision path in config/decisions.yaml (default: authorities act late)"
    )
    parser.add_argument("--runs", type=int, help="also run a Monte Carlo with this many runs")
    parser.add_argument("--seed", type=int, help="Monte Carlo seed (default: config value)")
    args = parser.parse_args(argv)

    config = load_config(args.config_dir)
    if args.bank not in config.banks:
        parser.error(f"unknown bank '{args.bank}'. Available: {', '.join(config.banks)}")
    if args.scenario not in config.scenarios:
        parser.error(
            f"unknown scenario '{args.scenario}'. Available: {', '.join(config.scenarios)}"
        )
    path_key = args.path or config.decisions.default_path
    if path_key not in config.decisions.paths:
        parser.error(
            f"unknown path '{path_key}'. Available: {', '.join(config.decisions.paths)}"
        )
    bank = config.banks[args.bank]
    scenario = config.scenarios[args.scenario]
    path = config.decisions.paths[path_key]
    choices = choices_of(path)
    result = simulate(bank, config.model, scenario, config.decisions, choices)
    horizon = int(config.model.horizon_days.value)

    print(f"Bank: {bank.name} | Scenario: {scenario.name} | severity {scenario.severity.value:g}")
    print(f"Amounts in {bank.unit}. Deterministic run, central parameter values.")
    print(f"Decision path: {path.name}")
    print()
    print(format_table(result, horizon))
    print()
    print("Events")
    for event in result.events:
        print(f"  day {event.day:>2}  {event.kind:<18} {event.detail}")
    if not result.events:
        print("  none")
    print()
    if result.likely_to_fail_day is not None:
        print(f"Likely-to-fail flag raised on day {result.likely_to_fail_day}.")
    if result.decision_log:
        print("Decision log")
        for row in decision_log_rows(result.decision_log, bank.unit):
            print(
                f"  {row['Actor']}: {row['Decision']} -> {row['Option']} | decided after close "
                f"of day {row['Decided after close of day']}, in effect from day "
                f"{row['In effect from day']}"
            )
            print(f"      figures: {row['Figures available at decision time']}")
        print()
    print("Scorecard")
    for label, value in scorecard_rows(result.scorecard, bank.unit):
        print(f"  {label:<46} {value}")
    print()
    if result.outcome == "survives":
        print(f"Outcome: no payments default within {horizon} days.")
    elif result.failure_day is None:
        print(f"Outcome: {OUTCOME_LABELS[result.outcome]} on day {result.end_day}.")
    else:
        print(f"Outcome: payments default on day {result.failure_day}.")
    if result.funding_need is not None:
        need = result.funding_need
        print(
            f"Funding need in resolution ({need.days} days): {need.need:.2f} {bank.unit} "
            f"= projected outflows {need.projected_outflows:.2f} "
            f"- remaining liquidity {need.remaining_liquidity:.2f}"
        )

    if args.runs is not None:
        seed = int(config.model.monte_carlo_seed.value) if args.seed is None else args.seed
        summary = summarise(
            run_monte_carlo(
                bank, config.model, scenario, args.runs, seed, config.decisions, choices
            )
        )
        print()
        print(f"Monte Carlo: {args.runs} runs, seed {seed} (ranges are 5th to 95th percentiles)")
        print(f"  Fails in {summary.fail_share:.0%} of runs")
        print(f"  Failure {describe_failure_day(summary, horizon)}")
        if summary.funding_need_low is not None:
            print(
                f"  Funding need {summary.funding_need_low:.2f} to "
                f"{summary.funding_need_high:.2f} {bank.unit}, "
                f"median {summary.funding_need_median:.2f}"
            )

    if args.export_dir is not None:
        script = build_script(bank, config.model, scenario, result, config.injects)
        stem = f"{args.bank}_{args.scenario}"
        files = {
            f"exercise_script_{stem}.docx": script_to_docx(script),
            f"injects_{stem}.csv": injects_to_csv(script),
            f"daily_table_{stem}.csv": daily_table_to_csv(result),
        }
        args.export_dir.mkdir(parents=True, exist_ok=True)
        print()
        for name, content in files.items():
            (args.export_dir / name).write_bytes(content)
            print(f"Exported {args.export_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
