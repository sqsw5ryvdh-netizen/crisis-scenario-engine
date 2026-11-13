"""SPEC_V2 phase C1: calendar and weekend rules, intraday view, showcase, script additions."""

import io
import math
import shutil

import pytest
import yaml
from docx import Document
from pydantic import ValidationError

from app.charts import build_failure_distribution, build_path_comparison, build_timeline
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import FIGURE_LABELS, Choice, choices_of, intraday_allocation
from engine.export import pack_files
from engine.model import simulate
from engine.montecarlo import failure_day_counts, run_monte_carlo
from engine.run import main
from engine.schema import ACTORS, DECISION_STAGES, INTRADAY_BLOCKS, Parameter
from engine.script import build_pack

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
TOL = 1e-9
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
PAIRS = [(bank, scenario) for bank in CONFIG.banks for scenario in SCENARIOS]
HORIZON = int(CONFIG.model.horizon_days.value)


def param(value: float) -> Parameter:
    return Parameter(value=value, unit="x", source="ASSUMPTION")


def run(bank: str, scenario: str, *choices: Choice, overrides: dict | None = None, **updates):
    preset = CONFIG.scenarios[scenario]
    if overrides:
        merged = dict(preset.overrides)
        merged.update({name: param(value) for name, value in overrides.items()})
        updates["overrides"] = merged
    return simulate(
        CONFIG.banks[bank], CONFIG.model, preset.model_copy(update=updates), DECISIONS, choices
    )


def run_path(bank: str, scenario: str, path: str):
    return run(bank, scenario, *choices_of(DECISIONS.paths[path]))


def showcase_pack(ambient: bool = True):
    paths = [
        (DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key]))
        for key in (SHOWCASE.compare_path, SHOWCASE.path)
    ]
    return build_pack(
        CONFIG.banks[SHOWCASE.bank], CONFIG.model, CONFIG.scenarios[SHOWCASE.scenario],
        DECISIONS, SCRIPT, paths, ambient=ambient,
    )


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


# ------------------------------------------------- calendar


def test_weekdays_follow_the_scenario_start_weekday():
    monday = run("retail", "slow_burn").table.set_index("day")
    assert monday.loc[0, "weekday"] == "" and not monday.loc[0, "weekend"]
    assert list(monday.loc[1:8, "weekday"]) == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Monday",
    ]
    assert list(monday.index[monday["weekend"]]) == [6, 7, 13, 14, 20, 21, 27, 28]
    wednesday = run("retail", "geopolitical").table.set_index("day")
    assert CONFIG.scenarios["geopolitical"].start_weekday == "Wednesday"
    assert wednesday.loc[1, "weekday"] == "Wednesday" and wednesday.loc[10, "weekday"] == "Friday"
    assert list(wednesday.index[wednesday["weekend"]])[:4] == [4, 5, 11, 12]


@pytest.mark.parametrize("path", ["act_late", "support_package"])
@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_weekend_days_have_no_sales_no_central_bank_drawing_and_no_default(bank, scenario, path):
    result = run_path(bank, scenario, path)
    table = result.table.set_index("day")
    weekend = table[table["weekend"]]
    for column in ("funded_hqla", "funded_central_bank", "funded_ela", "funded_fire_sale",
                   "outflow_wholesale", "unmet_outflow", "portfolio_sale"):
        assert weekend[column].abs().max() < TOL or weekend.empty, column
    # What leaves on a weekend is paid from cash.
    assert (weekend["funded_cash"] - weekend["outflow_total"]).abs().max() < TOL or weekend.empty
    for day in weekend.index:
        assert weekend.loc[day, "outflow_total"] <= table.loc[day - 1, "cash"] + TOL
    if result.failure_day is not None:
        assert not table.loc[result.failure_day, "weekend"]


def test_weekend_requests_beyond_cash_wait_for_monday():
    # Retail bank, geopolitical shock: cash runs out on the first Saturday (day 4).
    table = run("retail", "geopolitical").table.set_index("day")
    saturday, sunday, monday = table.loc[4], table.loc[5], table.loc[6]
    assert saturday["cash"] == pytest.approx(0.0) and saturday["queued_withdrawals"] > 0
    assert sunday["outflow_total"] == pytest.approx(0.0)
    assert sunday["queued_withdrawals"] > saturday["queued_withdrawals"]
    # Queued requests are still deposits of the bank until they are paid.
    assert sunday["deposits_total"] == pytest.approx(saturday["deposits_total"])
    # On Monday the queue is paid on top of the day's requests.
    assert monday["queued_withdrawals"] == pytest.approx(0.0)
    assert monday["outflow_deposits"] > sunday["queued_withdrawals"]
    assert monday["outflow_total"] > table.loc[3, "outflow_total"]


def test_weekend_withdrawal_share_scales_what_is_requested():
    none = run("universal", "geopolitical", overrides={"weekend_withdrawal_share": 0.0})
    base = run("universal", "geopolitical")
    full = run("universal", "geopolitical", overrides={"weekend_withdrawal_share": 1.0})
    asked = lambda r: (  # noqa: E731
        r.table.set_index("day").loc[4, ["outflow_total", "queued_withdrawals"]].sum()
    )
    assert asked(none) == pytest.approx(0.0)
    share = CONFIG.model.weekend_withdrawal_share.value
    assert asked(base) == pytest.approx(share * asked(full))
    assert 0 < share < 1


def test_calendar_can_be_switched_off():
    off = run("retail", "geopolitical", overrides={"weekend_rules_on": 0}).table
    on = run("retail", "geopolitical").table
    assert not off["weekend"].any() and off["queued_withdrawals"].abs().max() == 0
    assert list(off["weekday"]) == list(on["weekday"])  # labels stay, the rules go
    # Up to the first weekend the two runs are the same.
    first = int(on.loc[on["weekend"], "day"].iloc[0])
    columns = [c for c in on.columns if c != "weekend"]
    assert off.loc[off["day"] < first, columns].equals(on.loc[on["day"] < first, columns])


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_higher_severity_never_defaults_later_with_the_calendar_on(bank, scenario):
    assert CONFIG.model.weekend_rules_on.value == 1
    days = [
        run(bank, scenario, severity=param(i * 0.05)).failure_day for i in range(0, 201)
    ]
    as_number = [math.inf if d is None else d for d in days]
    for lower, higher in zip(as_number, as_number[1:]):
        assert higher <= lower, days


def test_moratorium_and_settlement_count_business_days():
    # Geopolitical shock: day 10 is a Friday, so FoLtF and the moratorium start on Saturday.
    held = run(
        "retail", "geopolitical",
        Choice("declare_foltf", "declare", 10), Choice("moratorium", "two_days", 10),
    ).table.set_index("day")
    assert list(held.loc[11:14, "weekday"]) == ["Saturday", "Sunday", "Monday", "Tuesday"]
    assert held.loc[11:14, "outflow_total"].abs().max() < TOL  # two business days: Mon, Tue
    assert held.loc[15, "outflow_total"] > 0 and held.loc[15, "weekday"] == "Wednesday"
    # A portfolio sale decided on Thursday is in effect on Friday and settles two business
    # days later, on Tuesday.
    sale = run("retail", "geopolitical", Choice("recovery_options", "sell_portfolio", 2))
    record = sale.decision_log[0]
    table = sale.table.set_index("day")
    assert (record.decided_day, record.effect_day) == (2, 3)
    assert list(table.index[table["portfolio_sale"] > 0]) == [7]
    assert table.loc[7, "weekday"] == "Tuesday"


def test_resolution_weekend_flag_marks_foltf_after_a_friday_close():
    assert run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path).resolution_weekend
    midweek = run_path("universal", "fast_run", "early_foltf")
    assert midweek.foltf_day == 2 and not midweek.resolution_weekend
    assert not run("retail", "geopolitical").resolution_weekend  # no FoLtF at all


# ------------------------------------------------- the queue is visible (review change 1)


def test_requested_not_yet_settled_is_in_the_figures_and_in_the_liquidity_need():
    info = DECISIONS.information
    assert "queued_withdrawals" in info["bank_management"].figures
    assert "queued_withdrawals" in info["resolution_authority"].figures
    assert FIGURE_LABELS["queued_withdrawals"][0] == "Requested, not yet settled"
    result = run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)
    need = result.funding_need
    assert need.queued > 0 and need.queued == pytest.approx(result.scorecard.queued_withdrawals)
    assert need.need == pytest.approx(
        max(0.0, need.projected_outflows + need.queued - need.remaining_liquidity)
    )
    pack = showcase_pack()
    files = pack_files(pack, SCRIPT, DECISIONS)
    authority = docx_text(files["players/resolution_authority.docx"])
    assert "Requested, not yet settled" in authority
    last = [i for i in pack.branches[1].injects["resolution_authority"] if i.figures][-1]
    queued = next(f for f in last.figures if f.name == "queued_withdrawals")
    assert queued.value > 0  # the weekend's requests, seen a day late
    control_text = docx_text(files["control_team/control_team_pack.docx"])
    assert "requested but not yet settled" in control_text and "Requested, not settled" in control_text


# ------------------------------------------------- stage events carry their figures


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_stage_events_record_the_figures_that_crossed_a_threshold(bank, scenario):
    result = run(bank, scenario)
    table = result.table.set_index("day")
    for event in result.events:
        if event.kind not in DECISION_STAGES:
            assert event.figures == ()
            continue
        assert event.figures, event
        for name, value, threshold in event.figures:
            assert name in FIGURE_LABELS
            if name == "cum_deposit_loss_pct":
                assert value > threshold  # more lost than the limit (queued requests count)
            elif name == "unmet_outflow":
                assert value > 0
            else:
                assert value < threshold or (name == "cet1_ratio" and value <= threshold)
                assert value == pytest.approx(table.loc[event.day, name])


# ------------------------------------------------- intraday view


def test_intraday_allocation_splits_the_day_and_adds_up():
    rows = intraday_allocation(100.0, CONFIG.model)
    assert [label for label, _, _ in rows] == list(INTRADAY_BLOCKS.values())
    assert sum(amount for _, amount, _ in rows) == pytest.approx(100.0)
    assert rows[-1][2] == pytest.approx(100.0)
    assert [c for _, _, c in rows] == sorted(c for _, _, c in rows)


def test_intraday_shares_must_add_up_to_one(tmp_path):
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    data = yaml.safe_load((target / "model.yaml").read_text())
    data["intraday_share_morning"]["value"] = 0.5
    (target / "model.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_config(target)


def test_only_bank_management_gets_the_outflow_by_time_block():
    pack = showcase_pack()
    branch = pack.branches[1]
    for actor in ACTORS:
        blocks = [f for i in branch.injects[actor] for f in i.figures if f.block]
        assert bool(blocks) == (actor == "bank_management")
    report = next(i for i in branch.injects["bank_management"] if i.decision_due)
    blocks = [f for f in report.figures if f.block]
    outflow = next(f for f in report.figures if f.name == "outflow_total" and not f.block)
    assert [f.block for f in blocks] == list(INTRADAY_BLOCKS.values())
    assert sum(f.value for f in blocks) == pytest.approx(outflow.value)
    text = docx_text(pack_files(pack, SCRIPT, DECISIONS)["players/bank_management.docx"])
    assert "allocation of the daily figure" in text and "Outflow 09:00-12:00" in text


# ------------------------------------------------- showcase


def test_showcase_is_about_ten_days_and_crosses_a_weekend():
    assert (SHOWCASE.bank, SHOWCASE.scenario) == ("retail", "geopolitical")
    late = run(SHOWCASE.bank, SHOWCASE.scenario)
    result = run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)
    table = result.table.set_index("day")
    log = {record.decision: record for record in result.decision_log}
    # The bank is likely to fail on the Monday after the first weekend ...
    assert late.likely_to_fail_day == result.likely_to_fail_day == 6
    assert table.loc[6, "weekday"] == "Monday" and table.loc[4:5, "weekend"].all()
    # ... FoLtF is declared after the close of the second Friday ...
    assert log["declare_foltf"].decided_day == 10 and table.loc[10, "weekday"] == "Friday"
    assert result.foltf_day == 11 and result.resolution_weekend
    # ... the tool is decided on Sunday and applies on Monday.
    assert log["resolution_tool"].decided_day == 12 and table.loc[12, "weekday"] == "Sunday"
    assert result.end_day == 13 and table.loc[13, "weekday"] == "Monday"
    assert result.outcome == "bail_in" and result.failure_day is None
    # On the Friday the bank has about a day of liquidity left.
    assert table.loc[10, "survival_days"] < 2
    other = run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.compare_path)
    assert other.outcome == "liquidation" and other.end_day == 13
    assert other.scorecard.dgs_payout > 0 and other.resolution_weekend


def test_cli_exports_the_showcase(tmp_path, capsys):
    assert main(["--showcase", "--export-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    stem = f"exercise_pack_{SHOWCASE.bank}_{SHOWCASE.scenario}"
    assert "Decision path: FoLtF on the second Friday, bail-in" in out
    assert (tmp_path / f"{stem}.zip").exists()
    data = tmp_path / stem / "control_team" / "data"
    assert (data / "SIMULATION_daily_table_branch_A.csv").exists() and (data / "SIMULATION_daily_table_branch_B.csv").exists()
    injects = (data / "SIMULATION_injects.csv").read_text()
    assert "ambient" in injects
    assert main(["--showcase", "--no-ambient", "--export-dir", str(tmp_path / "quiet")]) == 0
    quiet = (tmp_path / "quiet" / stem / "control_team" / "data" / "SIMULATION_injects.csv").read_text()
    assert ",ambient," not in quiet
    with pytest.raises(SystemExit):
        main(["--export-dir", str(tmp_path)])  # neither a bank and scenario nor --showcase


# ------------------------------------------------- ambient injects


def test_ambient_library_has_no_item_that_would_move_the_figures():
    assert len(SCRIPT.ambient) >= 4
    titles = " ".join(item.title.lower() for item in SCRIPT.ambient.values())
    assert "rating" not in titles and "downgrade" not in titles
    assert "parliamentary question" in titles and "tv footage" in titles
    for item in SCRIPT.ambient.values():
        assert not any(character.isdigit() for character in item.text)
        assert set(item.recipients) <= set(ACTORS)


def test_ambient_injects_can_be_switched_off_and_change_nothing_else():
    loud, quiet = showcase_pack(ambient=True), showcase_pack(ambient=False)
    assert loud.ambient_on and not quiet.ambient_on
    for with_noise, without in zip(loud.branches, quiet.branches):
        assert with_noise.result.table.equals(without.result.table)  # no model effect
        assert with_noise.decision_points == without.decision_points
        for actor in ACTORS:
            noise = [i for i in with_noise.injects[actor] if i.kind == "ambient"]
            rest = tuple(i for i in with_noise.injects[actor] if i.kind != "ambient")
            assert rest == without.injects[actor]
            assert all(i.figures == i.headline == i.notified == () for i in noise)
            assert not [i for i in without.injects[actor] if i.kind == "ambient"]
    assert sum(1 for b in loud.branches for a in ACTORS for i in b.injects[a] if i.kind == "ambient") > 5


def test_ambient_injects_are_placed_after_their_anchor_for_informed_recipients():
    pack = showcase_pack()
    branch = pack.branches[1]
    result = branch.result
    anchors = {e.kind: e.day for e in result.events if e.kind in DECISION_STAGES}
    anchors["foltf"] = result.foltf_day
    seen = set()
    for key, item in SCRIPT.ambient.items():
        for actor in ACTORS:
            placed = [i for i in branch.injects[actor] if i.kind == "ambient" and i.title == item.title]
            needed = DECISIONS.information[actor].informed_from
            day = anchors[item.anchor] + item.offset_days
            expected = (
                actor in item.recipients
                and day <= result.end_day
                and (needed is None or anchors[needed] <= day)
            )
            assert len(placed) == (1 if expected else 0), (key, actor)
            if placed:
                assert (placed[0].day, placed[0].time, placed[0].sender) == (day, item.time, item.sender)
                seen.add(key)
    assert {"press_enquiry", "tv_footage", "ministry_call", "depositor_association"} <= seen


def test_control_team_pack_lists_ambient_injects_and_known_limits():
    text = docx_text(pack_files(showcase_pack(), SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "Ambient injects" in text and "no effect on any figure" in text
    assert "Press enquiry" in text and "Parliamentary question" in text
    assert "Known limits" in text
    assert "Legal loss-absorption thresholds are not modelled" in text
    assert "resolution weekend" in text  # FoLtF after a Friday close
    off = docx_text(pack_files(showcase_pack(False), SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "Ambient injects are switched off in this pack." in off


# ------------------------------------------------- charts


def test_charts_show_weekdays_and_shade_weekends():
    scenario = CONFIG.scenarios[SHOWCASE.scenario]
    result = run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)
    other = run(SHOWCASE.bank, SHOWCASE.scenario)
    weekend_days = sum(1 for day in range(1, HORIZON + 1) if scenario.weekday(day) in ("Saturday", "Sunday"))
    for fig in (
        build_timeline(result, HORIZON, "EUR bn", start_weekday=scenario.start_weekday),
        build_path_comparison(other, result, "A", "B", HORIZON, "EUR bn", scenario.start_weekday),
    ):
        labels = list(fig.layout.xaxis.ticktext)
        assert labels[0] == "Wed 1" and "Mon 6" in labels and "Mon 13" in labels
        shaded = [s for s in fig.layout.shapes if s.type == "rect"]
        assert len(shaded) == 3 * weekend_days  # one per weekend day and panel
    plain = build_timeline(result, HORIZON, "EUR bn")
    assert plain.layout.xaxis.ticktext is None and not [s for s in plain.layout.shapes if s.type == "rect"]
    unshaded = build_timeline(result, HORIZON, "EUR bn", start_weekday="Wednesday", weekends=False)
    assert not [s for s in unshaded.layout.shapes if s.type == "rect"]
    runs = run_monte_carlo(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"], 30, 1)
    bars = build_failure_distribution(failure_day_counts(runs), HORIZON, "Monday")
    assert "Mon 1" in bars.layout.xaxis.ticktext and "No default" in bars.layout.xaxis.ticktext
