"""CLI: python -m engine.run --bank digital --scenario fast_run"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import OPEN_OUTCOMES, OUTCOME_LABELS, choices_of, decision_log_rows, scorecard_rows
from engine.export import pack_files, pack_to_zip
from engine.model import SimulationResult, simulate
from engine.montecarlo import describe_failure_day, run_monte_carlo, summarise
from engine.resolution import (
    LEVELS,
    breakpoint_text,
    hostile_liquidity_need,
    resolution_summary,
    summary_rows,
)
from engine.script import MODES, active_challenges, build_pack, scenario_with_challenges

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
    parser.add_argument("--bank", help="bank key in config/banks.yaml")
    parser.add_argument("--scenario", help="scenario key in config/scenarios.yaml")
    parser.add_argument(
        "--showcase", action="store_true",
        help="use the showcase run of config/script.yaml: bank, scenario and both paths",
    )
    parser.add_argument(
        "--no-ambient", action="store_true", help="leave the ambient injects out of the pack"
    )
    parser.add_argument(
        "--mode", choices=MODES, default="dynamic",
        help="type of exercise: dynamic (decisions change the course, two branches) or "
        "linear (one fixed path, decisions are only recorded)",
    )
    parser.add_argument(
        "--writedown", choices=LEVELS, default="central",
        help="valuation write-down at FoLtF used in the resolution summary (an assumption)",
    )
    parser.add_argument(
        "--hostile", action="store_true",
        help="hostile assumptions: add the hostile challenge injects and show the liquidity "
        "need at reopening with a negative market reaction",
    )
    parser.add_argument(
        "--challenge", action="append", metavar="KEY",
        help="challenge inject of config/script.yaml to play; repeat for several. Replaces "
        "the defaults. 'rating_downgrade' adds a news shock to the run",
    )
    parser.add_argument(
        "--sessions", type=int, help="session plan: number of real sessions (default: config)"
    )
    parser.add_argument(
        "--session-hours", type=float, help="session plan: hours per session (default: config)"
    )
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument(
        "--export-dir", type=Path,
        help="write the exercise pack here: dossier and rules of play for the players, "
        "observer sheets, and for the control team the charter draft, session plan, main "
        "events list, inject sheets, role cards, control team pack and data",
    )
    parser.add_argument(
        "--compare-path",
        help="second branch of the exercise pack (default: authorities act late)",
    )
    parser.add_argument(
        "--path", help="decision path in config/decisions.yaml (default: authorities act late)"
    )
    parser.add_argument("--runs", type=int, help="also run a Monte Carlo with this many runs")
    parser.add_argument("--seed", type=int, help="Monte Carlo seed (default: config value)")
    args = parser.parse_args(argv)

    config = load_config(args.config_dir)
    if args.showcase:
        showcase = config.script.showcase
        args.bank = args.bank or showcase.bank
        args.scenario = args.scenario or showcase.scenario
        args.path = args.path or showcase.path
        args.compare_path = args.compare_path or showcase.compare_path
    if args.bank is None or args.scenario is None:
        parser.error("give --bank and --scenario, or --showcase")
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
    unknown = set(args.challenge or ()) - set(config.script.challenges)
    if unknown:
        parser.error(
            f"unknown challenge inject {sorted(unknown)}. "
            f"Available: {', '.join(config.script.challenges)}"
        )
    challenges = active_challenges(config.script, args.challenge, args.hostile)
    # Only a challenge inject that is a public news event changes the run.
    scenario = scenario_with_challenges(
        bank, config.model, scenario, config.decisions, config.script, challenges
    )
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
    if result.outcome in OPEN_OUTCOMES:
        print(f"Outcome after {horizon} days: {OUTCOME_LABELS[result.outcome]}.")
    elif result.failure_day is None:
        print(f"Outcome: {OUTCOME_LABELS[result.outcome]} on day {result.end_day}.")
    else:
        print(f"Outcome: payments default on day {result.failure_day}.")
    summary = resolution_summary(bank, config.model, result, args.writedown)
    if summary is not None:
        print("Resolution summary")
        resolved = config.model.with_overrides(scenario.overrides)
        hostile_need = hostile_liquidity_need(summary, resolved) if args.hostile else None
        for label, value in summary_rows(summary, bank.unit, hostile_need):
            print(f"  {label:<88} {value}")
        print(f"  {breakpoint_text(summary)}")
        print()
    if result.funding_need is not None:
        need = result.funding_need
        print(
            f"Funding need in resolution ({need.days} days): {need.need:.2f} {bank.unit} "
            f"= projected outflows {need.projected_outflows:.2f} "
            f"+ requested, not yet settled {need.queued:.2f} "
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
        other_key = args.compare_path or config.decisions.default_path
        if other_key not in config.decisions.paths:
            parser.error(
                f"unknown path '{other_key}'. Available: {', '.join(config.decisions.paths)}"
            )
        branches = [(path.name, choices)]
        if other_key != path_key:
            other = config.decisions.paths[other_key]
            branches.insert(0, (other.name, choices_of(other)))
        pack = build_pack(
            bank, config.model, scenario, config.decisions, config.script, branches,
            ambient=not args.no_ambient,
            writedown_level=args.writedown,
            mode=args.mode,
            challenges=challenges,
            hostile=args.hostile,
            bank_alias=config.script.setup.bank_alias,
            background_key=args.scenario,
        )
        if (args.sessions is not None and args.sessions < 1) or (
                args.session_hours is not None and args.session_hours <= 0):
            parser.error("--sessions must be at least 1 and --session-hours positive")
        inputs = (args.sessions, args.session_hours)
        stem = f"exercise_pack_{args.bank}_{args.scenario}"
        args.export_dir.mkdir(parents=True, exist_ok=True)
        archive = args.export_dir / f"{stem}.zip"
        archive.write_bytes(pack_to_zip(pack, config.script, config.decisions, *inputs))
        print()
        print(f"Exported {archive}")
        for name, content in pack_files(pack, config.script, config.decisions, *inputs).items():
            target = args.export_dir / stem / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            print(f"Exported {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
