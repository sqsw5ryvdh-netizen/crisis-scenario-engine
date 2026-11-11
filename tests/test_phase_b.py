"""SPEC_V2 phase B: decision points, decision log, outcome scorecard, path comparison."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from app import demo
from app.charts import build_path_comparison
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import Choice, choices_of, decision_log_rows, scorecard_rows
from engine.model import simulate
from engine.montecarlo import run_monte_carlo
from engine.run import main

CONFIG = load_config()
DECISIONS = CONFIG.decisions
TOL = 1e-9
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
PAIRS = [(bank, scenario) for bank in CONFIG.banks for scenario in SCENARIOS]
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


def run(bank: str, scenario: str, *choices: Choice):
    return simulate(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], DECISIONS, choices)


def run_path(bank: str, scenario: str, path: str):
    return run(bank, scenario, *choices_of(DECISIONS.paths[path]))


def day_row(result, day: int):
    return result.table.set_index("day").loc[day]


# ------------------------------------------------- config


def test_default_path_decides_nothing_and_default_options_do_nothing():
    assert DECISIONS.paths[DECISIONS.default_path].choices == []
    for decision in DECISIONS.decisions.values():
        assert decision.options[decision.default_option].effect == "none"


def test_minimum_set_of_the_spec_is_present():
    by_actor = {}
    for key, decision in DECISIONS.decisions.items():
        by_actor.setdefault(decision.actor, set()).add(key)
    assert by_actor == {
        "bank_management": {"public_statement", "recovery_options"},
        "competent_authority": {"early_intervention_measures", "declare_foltf"},
        "central_bank": {"ela"},
        "resolution_authority": {"moratorium", "resolution_tool"},
        "deposit_guarantee_scheme": {"prepare_payout", "dgs_payout"},
    }
    tools = {o.tool for o in DECISIONS.decisions["resolution_tool"].options.values() if o.tool}
    assert tools == {"bail_in", "sale_of_business", "liquidation"}


def _broken_config(tmp_path, change):
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    data = yaml.safe_load((target / "decisions.yaml").read_text())
    change(data)
    (target / "decisions.yaml").write_text(yaml.safe_dump(data))
    return target


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["decisions"]["ela"]["options"]["grant_full"].update(effect="print_money"),
        lambda d: d["decisions"]["moratorium"]["options"]["one_day"].pop("params"),
        lambda d: d["decisions"]["ela"].update(actor="finance_ministry"),
        lambda d: d["decisions"]["ela"].update(default_option="grant_full"),
        lambda d: d["paths"]["ela_granted"]["choices"][0].update(option="grant_everything"),
        lambda d: d["decisions"]["moratorium"]["condition"].update(after="nothing"),
        lambda d: d["lead_days"].update(value=0),
        lambda d: d["actor_figures"]["central_bank"].append("share_price"),
    ],
)
def test_invalid_decisions_config_is_rejected(tmp_path, change):
    with pytest.raises(ValidationError):
        load_config(_broken_config(tmp_path, change))


def test_invalid_decision_set_is_rejected():
    with pytest.raises(ValueError):
        run("universal", "fast_run", Choice("ela", "grant_everything", 1))
    with pytest.raises(ValueError):
        run("universal", "fast_run", Choice("ela", "refuse", 1), Choice("ela", "grant_full", 2))
    with pytest.raises(ValueError):
        simulate(
            CONFIG.banks["universal"], CONFIG.model, CONFIG.scenarios["fast_run"],
            None, (Choice("ela", "grant_full", 1),),
        )


# ------------------------------------------------- default path and timing


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_default_path_reproduces_the_run_without_decisions(bank, scenario):
    plain = simulate(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario])
    late = run_path(bank, scenario, DECISIONS.default_path)
    assert late.table.equals(plain.table)
    assert late.events == plain.events
    assert late.failure_day == plain.failure_day
    assert late.decision_log == () and late.foltf_day is None
    assert late.outcome == ("payments_default" if plain.failure_day is not None else "survives")


@pytest.mark.parametrize("path", list(DECISIONS.paths))
def test_decisions_take_effect_one_day_after_they_are_taken(path):
    result = run_path("universal", "fast_run", path)
    lead = int(DECISIONS.lead_days.value)
    scheduled = {c.decision: c.day for c in DECISIONS.paths[path].choices}
    for record in result.decision_log:
        assert record.effect_day == record.decided_day + lead
        assert record.decided_day >= scheduled[record.decision]


def test_a_decision_waits_until_its_condition_holds():
    # ELA needs the recovery stage, which the slow burn reaches only on day 7.
    result = run("universal", "slow_burn", Choice("ela", "grant_full", 0))
    recovery = next(e.day for e in result.events if e.kind == "recovery")
    (record,) = result.decision_log
    assert (record.scheduled_day, record.decided_day, record.effect_day) == (0, recovery, recovery + 1)


def test_nothing_changes_before_a_decision_is_in_effect():
    late = run_path("universal", "fast_run", "act_late").table
    for path in DECISIONS.paths:
        result = run_path("universal", "fast_run", path)
        first = min((r.effect_day for r in result.decision_log), default=None)
        if first is None:
            continue
        before = result.table[result.table["day"] < first]
        assert before.equals(late[late["day"] < first]), path


# ------------------------------------------------- bank management


def test_public_statement_moves_panic_in_the_chosen_direction():
    # The slow burn is used because panic is not saturated there.
    none = run("universal", "slow_burn")
    calm = run("universal", "slow_burn", Choice("public_statement", "reassuring", 0))
    worse = run("universal", "slow_burn", Choice("public_statement", "backfiring", 0))
    day = calm.decision_log[0].effect_day
    assert day_row(calm, day)["panic_multiplier"] < day_row(none, day)["panic_multiplier"]
    assert day_row(worse, day)["panic_multiplier"] > day_row(none, day)["panic_multiplier"]
    loss = lambda r: r.table["cum_deposit_loss_pct"].iloc[-1]  # noqa: E731
    assert loss(calm) < loss(none) < loss(worse)


def test_portfolio_sale_raises_cash_after_settlement_and_books_the_loss():
    option = DECISIONS.decisions["recovery_options"].options["sell_portfolio"]
    share, discount = option.params["share"].value, option.params["discount"].value
    settlement = int(option.params["settlement_days"].value)
    none = run("universal", "slow_burn")
    sale = run("universal", "slow_burn", Choice("recovery_options", "sell_portfolio", 0))
    settles = sale.decision_log[0].effect_day + settlement
    sold = share * CONFIG.banks["universal"].assets.illiquid_assets
    table = sale.table.set_index("day")
    assert table.loc[settles, "portfolio_sale"] == pytest.approx(sold * (1 - discount))
    assert (table["portfolio_sale"].drop(settles).abs() < TOL).all()
    base = none.table.set_index("day")
    assert table.loc[settles - 1].equals(base.loc[settles - 1])
    assert base.loc[settles, "illiquid_assets"] - table.loc[settles, "illiquid_assets"] == (
        pytest.approx(sold)
    )
    assert base.loc[settles, "cet1"] - table.loc[settles, "cet1"] == pytest.approx(sold * discount)
    assert table.loc[settles, "liquidity_buffer"] > base.loc[settles, "liquidity_buffer"]
    assert table.loc[settles, "rwa"] < base.loc[settles, "rwa"]


def test_portfolio_sale_does_not_use_fire_sale_capacity():
    result = run_path("universal", "geopolitical", "support_package")
    table = result.table
    cap = CONFIG.model.fire_sale_daily_cap_share.value
    initial = CONFIG.banks["universal"].assets.illiquid_assets
    discount = CONFIG.model.with_overrides(
        CONFIG.scenarios["geopolitical"].overrides
    ).fire_sale_discount.value
    fire_sold = (table["funded_fire_sale"] / (1 - discount)).cumsum()
    assert table["funded_fire_sale"].sum() > 0 and table["portfolio_sale"].sum() > 0
    assert (fire_sold <= cap * initial * table["day"] + 1e-6).all()


# ------------------------------------------------- competent authority


def test_early_intervention_measure_forces_the_portfolio_sale():
    # Retail bank, geopolitical shock: recovery on day 2, early intervention on day 5.
    own = run("retail", "geopolitical", Choice("recovery_options", "sell_portfolio", 0))
    forced = run(
        "retail", "geopolitical",
        Choice("early_intervention_measures", "require_recovery_options", 0),
    )
    assert forced.table["portfolio_sale"].sum() > 0
    assert own.table["portfolio_sale"].sum() == pytest.approx(forced.table["portfolio_sale"].sum())
    # The authority can act only from early intervention, so the sale comes later.
    first = lambda r: int(r.table.loc[r.table["portfolio_sale"] > 0, "day"].iloc[0])  # noqa: E731
    assert first(forced) > first(own)


def test_forcing_recovery_options_does_not_sell_twice():
    both = run(
        "retail", "geopolitical",
        Choice("recovery_options", "sell_portfolio", 0),
        Choice("early_intervention_measures", "require_recovery_options", 0),
    )
    own = run("retail", "geopolitical", Choice("recovery_options", "sell_portfolio", 0))
    assert own.table["portfolio_sale"].sum() > 0
    assert both.table["portfolio_sale"].sum() == pytest.approx(own.table["portfolio_sale"].sum())
    assert len(both.decision_log) == 2  # both decisions are logged, the sale happens once


def test_script_only_measure_changes_no_figure_but_is_logged():
    plain = run("universal", "fast_run")
    reporting = run(
        "universal", "fast_run", Choice("early_intervention_measures", "enhanced_reporting", 0)
    )
    assert reporting.table.equals(plain.table)
    assert [r.option for r in reporting.decision_log] == ["enhanced_reporting"]


def test_foltf_can_only_be_declared_once_the_bank_is_likely_to_fail():
    result = run("retail", "geopolitical", Choice("declare_foltf", "declare", 0))
    (record,) = result.decision_log
    assert record.decided_day == result.likely_to_fail_day
    assert result.foltf_day == record.effect_day
    assert any(e.kind == "foltf" and e.day == result.foltf_day for e in result.events)
    never = run("retail", "slow_burn", Choice("declare_foltf", "declare", 0))
    assert never.decision_log == () and never.foltf_day is None  # the flag is never raised


def test_foltf_alone_does_not_stop_the_outflows():
    declared = run("universal", "fast_run", Choice("declare_foltf", "declare", 1))
    late = run("universal", "fast_run")
    assert declared.table.equals(late.table)
    assert declared.outcome == "payments_default"


# ------------------------------------------------- central bank


def test_refusing_ela_changes_nothing_but_is_logged():
    refused = run_path("universal", "fast_run", "ela_refused")
    late = run_path("universal", "fast_run", "act_late")
    assert refused.table.equals(late.table)
    (record,) = refused.decision_log
    assert (record.actor, record.option) == ("central_bank", "refuse")


def test_ela_cap_limits_what_is_drawn():
    capped = run("universal", "fast_run", Choice("ela", "grant_capped", 1))
    full = run("universal", "fast_run", Choice("ela", "grant_full", 1))
    late = run("universal", "fast_run")
    option = DECISIONS.decisions["ela"].options["grant_capped"]
    cap = option.params["cap_share"].value * CONFIG.banks["universal"].assets.total()
    assert capped.table["ela_funding"].max() == pytest.approx(cap)
    assert full.table["ela_funding"].max() > cap
    assert late.failure_day < capped.failure_day < full.failure_day


def test_capped_ela_counts_in_the_buffer_only_up_to_the_cap():
    capped = run("universal", "fast_run", Choice("ela", "grant_capped", 1))
    late = run("universal", "fast_run")
    option = DECISIONS.decisions["ela"].options["grant_capped"]
    cap = option.params["cap_share"].value * CONFIG.banks["universal"].assets.total()
    day = capped.decision_log[0].effect_day
    extra = day_row(capped, day)["liquidity_buffer"] - day_row(late, day)["liquidity_buffer"]
    assert 0 < extra <= cap + 1e-6


# ------------------------------------------------- resolution authority


def test_moratorium_needs_foltf():
    result = run("universal", "fast_run", Choice("moratorium", "two_days", 0))
    assert result.decision_log == ()
    assert result.table.equals(run("universal", "fast_run").table)


@pytest.mark.parametrize("option,days", [("one_day", 1), ("two_days", 2)])
def test_moratorium_suspends_outflows_and_queues_withdrawals(option, days):
    result = run(
        "universal", "fast_run",
        Choice("declare_foltf", "declare", 1), Choice("moratorium", option, 1),
    )
    start = next(r.effect_day for r in result.decision_log if r.decision == "moratorium")
    table = result.table.set_index("day")
    blocked = table.loc[start : start + days - 1]
    assert len(blocked) == days
    assert (blocked["outflow_total"].abs() < TOL).all()
    assert (blocked["deposits_total"] == table.loc[start - 1, "deposits_total"]).all()
    assert (blocked["queued_withdrawals"].diff().dropna() > 0).all()
    assert blocked["queued_withdrawals"].iloc[0] > 0
    # The day after, the queue is released on top of that day's requests.
    after = table.loc[start + days]
    assert after["queued_withdrawals"] == pytest.approx(0.0)
    assert after["outflow_deposits"] > blocked["queued_withdrawals"].iloc[-1]


@pytest.mark.parametrize("tool", ["bail_in", "sale_of_business", "liquidation"])
def test_resolution_tool_ends_the_run_on_its_effect_day(tool):
    result = run(
        "universal", "fast_run",
        Choice("declare_foltf", "declare", 1), Choice("resolution_tool", tool, 1),
    )
    end = next(r.effect_day for r in result.decision_log if r.decision == "resolution_tool")
    assert result.outcome == tool and result.end_day == end
    assert result.failure_day is None
    assert result.table["day"].iloc[-1] == end
    last = result.table.iloc[-1]
    assert last["outflow_total"] == pytest.approx(0.0)  # no payments on the resolution day
    assert last["status"] == ("liquidation" if tool == "liquidation" else "resolution")
    assert result.events[-1].kind == "resolution"
    assert result.funding_need is not None


def test_resolution_tool_needs_foltf():
    result = run("universal", "fast_run", Choice("resolution_tool", "bail_in", 0))
    assert result.decision_log == () and result.outcome == "payments_default"


# ------------------------------------------------- deposit guarantee scheme


def test_payout_in_liquidation_equals_covered_deposits_and_preparation_shortens_it():
    prepared = run_path("universal", "fast_run", "early_foltf_liquidation")
    unprepared = run(
        "universal", "fast_run",
        *[c for c in choices_of(DECISIONS.paths["early_foltf_liquidation"])
          if c.decision != "prepare_payout"],
    )
    covered = prepared.table["dep_insured_retail"].iloc[-1]
    assert prepared.scorecard.dgs_payout == pytest.approx(covered) and covered > 0
    prepare = DECISIONS.decisions["prepare_payout"].options["prepare"]
    assert prepared.scorecard.payout_days == int(prepare.params["payout_days"].value)
    assert unprepared.scorecard.payout_days == int(DECISIONS.payout_days_unprepared.value)
    assert unprepared.scorecard.dgs_payout == pytest.approx(covered)
    # Preparation changes no figure of the run.
    assert prepared.table.equals(unprepared.table)


def test_no_payout_without_liquidation_or_without_the_decision():
    bail_in = run_path("universal", "fast_run", "early_foltf")
    assert bail_in.scorecard.dgs_payout == 0.0 and bail_in.scorecard.payout_days is None
    undecided = run(
        "universal", "fast_run",
        Choice("declare_foltf", "declare", 1), Choice("resolution_tool", "liquidation", 1),
    )
    assert undecided.outcome == "liquidation" and undecided.scorecard.dgs_payout == 0.0
    # The scheme cannot decide a payout before liquidation is chosen.
    alone = run("universal", "fast_run", Choice("dgs_payout", "pay_out", 0))
    assert alone.decision_log == ()


# ------------------------------------------------- scorecard and decision log


@pytest.mark.parametrize("path", list(DECISIONS.paths))
@pytest.mark.parametrize("bank,scenario", [("universal", "fast_run"), ("retail", "geopolitical")])
def test_scorecard_matches_the_last_day_of_the_run(bank, scenario, path):
    result = run_path(bank, scenario, path)
    card, last = result.scorecard, result.table.iloc[-1]
    opening = result.table.iloc[0]
    assert card.outcome == result.outcome and card.end_day == last["day"]
    assert card.deposits_lost == pytest.approx(opening["deposits_total"] - last["deposits_total"])
    assert card.deposits_lost_pct == pytest.approx(last["cum_deposit_loss_pct"])
    assert card.ela_outstanding == pytest.approx(last["ela_funding"])
    assert card.losses_booked == pytest.approx(opening["cet1"] - last["cet1"])
    assert card.losses_booked == pytest.approx(result.table["loss"].sum())
    assert card.covered_deposits == pytest.approx(last["dep_insured_retail"])
    assert card.uncovered_deposits + card.covered_deposits == pytest.approx(last["deposits_total"])
    assert card.queued_withdrawals == pytest.approx(last["queued_withdrawals"])
    labels = [label for label, _ in scorecard_rows(card, "EUR bn")]
    for wanted in ("Deposits lost", "ELA outstanding", "Losses booked", "Uncovered deposits",
                   "DGS payout"):
        assert any(label.startswith(wanted) for label in labels), wanted


@pytest.mark.parametrize("path", list(DECISIONS.paths))
def test_decision_log_holds_the_actor_figures_of_the_decision_day(path):
    result = run_path("universal", "geopolitical", path)
    assert len(result.decision_log) <= len(DECISIONS.paths[path].choices)
    for record in result.decision_log:
        definition = DECISIONS.decisions[record.decision]
        assert record.actor == definition.actor
        row = day_row(result, record.decided_day)
        assert [name for name, _ in record.figures] == DECISIONS.actor_figures[record.actor]
        for name, value in record.figures:
            assert value == pytest.approx(row[name]), (record.decision, name)
    rows = decision_log_rows(result.decision_log, "EUR bn")
    assert all(row["In effect from day"] == row["Decided after close of day"] + 1 for row in rows)
    events = [e for e in result.events if e.kind == "decision"]
    assert len(events) == len(result.decision_log)


# ------------------------------------------------- acceptance


def test_three_fast_run_paths_of_the_universal_bank_end_differently():
    refused = run_path("universal", "fast_run", "ela_refused")
    granted = run_path("universal", "fast_run", "ela_granted")
    early = run_path("universal", "fast_run", "early_foltf")
    assert (refused.outcome, refused.failure_day) == ("payments_default", 4)
    assert (granted.outcome, granted.failure_day) == ("payments_default", 9)
    assert granted.failure_day > 5  # it gets through the first five days
    assert (early.outcome, early.end_day, early.failure_day) == ("bail_in", 4, None)
    assert early.foltf_day == 2
    losses = [r.scorecard.deposits_lost_pct for r in (early, refused, granted)]
    assert losses == sorted(losses) and losses[0] < 20 < 50 < losses[1]
    assert granted.scorecard.ela_outstanding > 0 == refused.scorecard.ela_outstanding


@pytest.mark.parametrize("bank,scenario", [("universal", "cyber_outage"), ("retail", "geopolitical")])
def test_a_combination_of_decisions_lets_the_bank_survive_where_it_otherwise_defaults(
    bank, scenario
):
    horizon = int(CONFIG.model.horizon_days.value)
    requirement = CONFIG.banks[bank].capital.capital_requirement_ratio
    late = run_path(bank, scenario, "act_late")
    ela_only = run_path(bank, scenario, "ela_granted")
    package = run_path(bank, scenario, "support_package")
    assert late.outcome == "payments_default"
    # ELA alone avoids the default, but capital ends below the requirement.
    assert ela_only.outcome == "survives"
    assert ela_only.table["cet1_ratio"].min() < requirement
    # The full package keeps the bank paying for 30 days with capital above the requirement.
    assert package.outcome == "survives" and package.end_day == horizon
    assert package.table["cet1_ratio"].min() >= requirement
    assert len(package.decision_log) == 3
    assert package.scorecard.losses_booked < ela_only.scorecard.losses_booked


# ------------------------------------------------- scenario pressure that fades


def test_fade_moves_severity_towards_the_floor_by_half_lives():
    fade = CONFIG.scenarios["cyber_outage"].fade
    start, half_life, floor = (
        int(fade.from_day.value), fade.half_life_days.value, fade.floor.value
    )
    severity = 2.0
    assert fade.factor_on(start - 1, severity) == severity
    assert fade.factor_on(start, severity) < severity
    after_one = fade.factor_on(start - 1 + int(half_life), severity)
    assert after_one == pytest.approx(floor + (severity - floor) / 2)
    values = [fade.factor_on(day, severity) for day in range(0, 31)]
    assert values == sorted(values, reverse=True) and values[-1] > floor
    assert fade.factor_on(20, floor / 2) == floor / 2  # already below the floor: unchanged


def test_only_the_cyber_and_geopolitical_scenarios_fade():
    fading = {key for key, scenario in CONFIG.scenarios.items() if scenario.fade is not None}
    assert fading == {"cyber_outage", "geopolitical"}


@pytest.mark.parametrize("scenario", ["cyber_outage", "geopolitical"])
@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_fade_does_not_change_the_run_without_decisions(bank, scenario):
    preset = CONFIG.scenarios[scenario]
    constant = preset.model_copy(update={"fade": None})
    with_fade = simulate(CONFIG.banks[bank], CONFIG.model, preset)
    without = simulate(CONFIG.banks[bank], CONFIG.model, constant)
    assert with_fade.failure_day == without.failure_day is not None
    stages = lambda r: [(e.kind, e.day) for e in r.events if e.kind != "news"]  # noqa: E731
    assert stages(with_fade) == stages(without)
    assert with_fade.table["cum_deposit_loss_pct"].iloc[-1] == pytest.approx(
        without.table["cum_deposit_loss_pct"].iloc[-1], abs=0.5
    )


def test_fade_lowers_outflows_only_from_its_first_day():
    preset = CONFIG.scenarios["cyber_outage"]
    choices = choices_of(DECISIONS.paths["support_package"])
    bank = CONFIG.banks["universal"]
    fading = simulate(bank, CONFIG.model, preset, DECISIONS, choices).table.set_index("day")
    constant = simulate(
        bank, CONFIG.model, preset.model_copy(update={"fade": None}), DECISIONS, choices
    ).table.set_index("day")
    start = int(preset.fade.from_day.value)
    assert fading.loc[: start - 1].equals(constant.loc[: start - 1])
    assert fading.loc[start, "outflow_deposits"] < constant.loc[start, "outflow_deposits"]


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f["from_day"].update(value=0),
        lambda f: f["half_life_days"].update(value=0),
        lambda f: f["floor"].update(value=-0.1),
        lambda f: f.pop("floor"),
    ],
)
def test_invalid_fade_is_rejected(tmp_path, change):
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    data = yaml.safe_load((target / "scenarios.yaml").read_text())
    change(data["cyber_outage"]["fade"])
    (target / "scenarios.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_config(target)


# ------------------------------------------------- domain checks on every path


@pytest.mark.parametrize("path", list(DECISIONS.paths))
@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_identity_and_no_negative_balances_on_every_path(bank, scenario, path):
    table = run_path(bank, scenario, path).table
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < 1e-8
    columns = [c for c in table.columns if c.startswith(("dep_", "funded_"))] + [
        "cash", "hqla_level1", "hqla_level2", "illiquid_assets", "encumbered_illiquid",
        "central_bank_funding", "ela_funding", "cet1", "rwa", "queued_withdrawals",
        "portfolio_sale", "unmet_outflow",
    ]
    assert (table[columns] >= -TOL).all().all()
    assert (table["encumbered_illiquid"] <= table["illiquid_assets"] + TOL).all()
    assert (table["deposits_total"].diff().dropna() <= TOL).all()  # deposits never come back


# ------------------------------------------------- Monte Carlo, CLI, chart, app


def test_monte_carlo_follows_the_decision_set_and_is_reproducible():
    bank, scenario = CONFIG.banks["universal"], CONFIG.scenarios["fast_run"]
    choices = choices_of(DECISIONS.paths["ela_granted"])
    late = run_monte_carlo(bank, CONFIG.model, scenario, 60, 7)
    first = run_monte_carlo(bank, CONFIG.model, scenario, 60, 7, DECISIONS, choices)
    again = run_monte_carlo(bank, CONFIG.model, scenario, 60, 7, DECISIONS, choices)
    assert (first.failure_days == again.failure_days).all() or (
        (first.failure_days != first.failure_days) == (again.failure_days != again.failure_days)
    ).all()
    assert first.draws.equals(late.draws)  # same parameter draws, different decisions
    later = (first.failure_days > late.failure_days) | (first.failure_days != first.failure_days)
    assert later.mean() > 0.9


def test_cli_prints_the_decision_log_and_the_scorecard(capsys):
    args = ["--bank", "universal", "--scenario", "fast_run", "--path", "early_foltf_liquidation"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Decision path: Early FOLTF, liquidation" in out
    assert "Decision log" in out and "Scorecard" in out
    assert "decided after close of day 1, in effect from day 2" in out
    assert "Outcome: Liquidation on day 4." in out
    with pytest.raises(SystemExit):
        main(["--bank", "universal", "--scenario", "fast_run", "--path", "no_such_path"])


def test_comparison_chart_shows_both_paths_to_their_last_day():
    late = run_path("universal", "fast_run", "act_late")
    early = run_path("universal", "fast_run", "early_foltf")
    fig = build_path_comparison(late, early, "Authorities act late", "Selected path", 30, "EUR bn")
    lines = [t for t in fig.data if t.mode == "lines"]
    assert len(lines) == 6 and {t.name for t in lines} == {"Authorities act late", "Selected path"}
    ends = [t for t in fig.data if t.mode == "markers"]
    assert sorted({t.x[0] for t in ends}) == sorted({late.end_day, early.end_day})


def _open_decisions_tab():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_DECISIONS
    at.run(timeout=30)
    at.selectbox(key="bank").set_value("universal").run(timeout=30)
    return at


def test_app_decisions_tab_compares_the_selected_path_with_a_reference():
    at = _open_decisions_tab()
    assert not at.exception
    assert at.metric[0].value == "Day 4"
    assert len(at.info) == 1  # no decision on the default path
    at.selectbox(key="decision_path").set_value("early_foltf_liquidation").run(timeout=30)
    assert not at.exception
    assert at.metric[0].value == "Avoided"
    assert at.metric[0].delta == "Liquidation on day 4"
    assert at.selectbox(key="dec:resolution_tool:option").value == "liquidation"
    assert at.number_input(key="dec:resolution_tool:day").value == 3
    scorecard, log = at.dataframe[0].value, at.dataframe[1].value
    assert list(scorecard.columns) == ["Figure", "Authorities act late", "Selected path"]
    payout = scorecard[scorecard["Figure"].str.startswith("DGS payout")].iloc[0]
    assert payout["Authorities act late"] == "0.00" and payout["Selected path"] == "214.09"
    assert len(log) == 5 and "Figures available at decision time" in log.columns


def test_app_single_decision_can_be_changed_after_loading_a_path():
    at = _open_decisions_tab()
    at.selectbox(key="decision_path").set_value("ela_granted").run(timeout=30)
    assert at.metric[0].value == "Day 9"
    at.selectbox(key="dec:ela:option").set_value("grant_capped").run(timeout=30)
    assert not at.exception
    assert at.metric[0].value == "Day 6"  # the cap is reached before the collateral runs out
    at.selectbox(key="reference_path").set_value("ela_granted").run(timeout=30)
    assert list(at.dataframe[0].value.columns) == ["Figure", "ELA granted", "Selected path"]
