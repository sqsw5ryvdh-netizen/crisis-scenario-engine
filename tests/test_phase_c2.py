"""SPEC_V2 phase C2: liability stack, resolution numbers, write-down levels, resolution weekend."""

import io
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml
from docx import Document
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from app import demo
from app.assumptions import bank_table
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import Choice, choices_of
from engine.export import pack_files
from engine.model import simulate
from engine.resolution import (
    LEVELS,
    breakpoint_share,
    breakpoint_text,
    level_value,
    resolution_levels,
    resolution_summary,
    summary_rows,
)
from engine.run import main
from engine.schema import ACTORS, Parameter
from engine.script import build_pack

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
TOL = 1e-9
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
# Paths that end with a tool, on runs where the bank is declared FoLtF.
RESOLVED = [
    (bank, scenario, path)
    for bank in CONFIG.banks
    for scenario in ("fast_run", "geopolitical")
    for path in ("early_foltf", "early_foltf_liquidation")
] + [("retail", "geopolitical", "weekend_bail_in"), ("retail", "geopolitical", "weekend_liquidation")]


def run_path(bank: str, scenario: str, path: str):
    return simulate(
        CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], DECISIONS,
        choices_of(DECISIONS.paths[path]),
    )


def levels_for(bank: str, scenario: str, path: str):
    return resolution_levels(CONFIG.banks[bank], CONFIG.model, run_path(bank, scenario, path))


def showcase_pack(level: str = "central"):
    paths = [
        (DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key]))
        for key in (SHOWCASE.compare_path, SHOWCASE.path)
    ]
    return build_pack(
        CONFIG.banks[SHOWCASE.bank], CONFIG.model, CONFIG.scenarios[SHOWCASE.scenario],
        DECISIONS, SCRIPT, paths, writedown_level=level,
    )


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


# ------------------------------------------------- liability stack and MREL in config


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_every_bank_has_a_stack_inside_its_balance_sheet_and_meets_mrel(bank):
    config = CONFIG.banks[bank]
    stack = config.resolution
    assert 0 < stack.at1_t2 <= config.liabilities.other_liabilities
    assert 0 < stack.senior_non_preferred <= config.liabilities.wholesale_funding.beyond_horizon
    assert config.mrel_available == pytest.approx(
        config.capital.cet1 + stack.at1_t2 + stack.senior_non_preferred
    )
    assert config.mrel_requirement == pytest.approx(stack.mrel_requirement_ratio * config.capital.rwa)
    assert config.mrel_available >= config.mrel_requirement  # no shortfall on day 0
    assert stack.mrel_requirement_ratio > config.capital.capital_requirement_ratio


def test_at1_t2_is_sourced_for_the_two_calibrated_archetypes_only():
    for key in ("universal", "retail"):
        source = CONFIG.banks[key].source_of("at1_t2")
        assert "2520128" in source and "2520134" in source
        assert CONFIG.banks[key].source_of("senior_non_preferred") == "ASSUMPTION"
    for key in ("digital", "specialised"):
        assert CONFIG.banks[key].source_of("at1_t2") == "ASSUMPTION"
    table = bank_table(CONFIG)
    rows = table[table["Item"].str.startswith(("AT1", "Senior non-preferred", "MREL"))]
    assert len(rows) == 3 * len(CONFIG.banks)
    assert (rows[rows["Item"].str.startswith("Senior")]["Status"] == "ASSUMPTION").all()


def _broken(tmp_path, file: str, change):
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    data = yaml.safe_load((target / file).read_text())
    change(data)
    (target / file).write_text(yaml.safe_dump(data))
    return target


@pytest.mark.parametrize(
    "file,change",
    [
        ("banks.yaml", lambda d: d["retail"]["resolution"].update(at1_t2=50.0)),
        ("banks.yaml", lambda d: d["retail"]["resolution"].update(senior_non_preferred=50.0)),
        ("banks.yaml", lambda d: d["retail"]["resolution"].pop("mrel_requirement_ratio")),
        ("banks.yaml", lambda d: d["retail"].pop("resolution")),
        ("model.yaml", lambda d: [d["resolution_writedown_share"].pop(k) for k in ("low", "high")]),
        ("model.yaml", lambda d: d["liquidation_haircut"].update(value=1.2, high=1.3)),
        ("script.yaml", lambda d: d["resolution"]["valuation"].update(figures=["share_price"])),
        ("script.yaml", lambda d: d["resolution"]["valuation"].update(text="Write-down of 4 percent.")),
        ("script.yaml", lambda d: d["resolution"]["valuation"].update(recipients=[])),
    ],
)
def test_invalid_resolution_config_is_rejected(tmp_path, file, change):
    with pytest.raises(ValidationError):
        load_config(_broken(tmp_path, file, change))


def test_write_down_levels_are_three_assumptions():
    parameter = CONFIG.model.resolution_writedown_share
    assert parameter.is_assumption and CONFIG.model.liquidation_haircut.is_assumption
    values = [level_value(parameter, level) for level in LEVELS]
    assert LEVELS == ("low", "central", "high") and values == sorted(values)
    assert values[0] < values[1] < values[2]
    with pytest.raises(ValueError):
        level_value(parameter, "extreme")


# ------------------------------------------------- when there is a resolution summary


@pytest.mark.parametrize("path", ["act_late", "ela_granted", "support_package"])
@pytest.mark.parametrize("bank,scenario", [("universal", "fast_run"), ("retail", "geopolitical")])
def test_no_resolution_summary_without_a_tool(bank, scenario, path):
    result = run_path(bank, scenario, path)
    assert resolution_summary(CONFIG.banks[bank], CONFIG.model, result) is None
    assert resolution_levels(CONFIG.banks[bank], CONFIG.model, result) is None


def test_resolution_summary_does_not_change_the_run():
    result = run_path(*RESOLVED[-2])
    before = result.table.copy()
    for level in LEVELS:
        resolution_summary(CONFIG.banks["retail"], CONFIG.model, result, level)
    assert result.table.equals(before)


# ------------------------------------------------- stack ordering


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("bank,scenario,path", RESOLVED)
def test_losses_fall_on_the_stack_in_order(bank, scenario, path, level):
    result = run_path(bank, scenario, path)
    summary = resolution_summary(CONFIG.banks[bank], CONFIG.model, result, level)
    last = result.table.iloc[-1]
    keys = [line.key for line in summary.lines]
    assert keys == ["cet1", "at1_t2", "senior_non_preferred", "senior_preferred", "uncovered_deposits"]
    cet1 = summary.lines[0]
    # Shareholders first: CET1 absorbs the write-down up to all of it.
    assert cet1.amount == pytest.approx(last["cet1"])
    assert cet1.absorbed == pytest.approx(min(cet1.amount, summary.writedown))
    # A layer is touched only when every layer before it is used up.
    above = summary.lines[1:]
    for earlier, later in zip(above, above[1:]):
        if later.absorbed > TOL:
            assert earlier.absorbed == pytest.approx(earlier.amount)
    for line in summary.lines:
        assert -TOL <= line.absorbed <= line.amount + TOL
    assert sum(line.absorbed for line in above) + summary.shortfall == pytest.approx(
        summary.recapitalisation_need
    )
    # Covered deposits are outside the stack and add up with the uncovered ones.
    assert summary.covered_deposits == pytest.approx(last["dep_insured_retail"])
    assert above[-1].amount + summary.covered_deposits == pytest.approx(last["deposits_total"])
    assert above[0].amount == pytest.approx(CONFIG.banks[bank].resolution.at1_t2)


@pytest.mark.parametrize("bank,scenario,path", RESOLVED)
def test_recapitalisation_restores_the_requirement_and_grows_with_the_write_down(
    bank, scenario, path
):
    levels = levels_for(bank, scenario, path)
    needs = [levels[level].recapitalisation_need for level in LEVELS]
    assert needs == sorted(needs)
    writedowns = [levels[level].writedown for level in LEVELS]
    assert writedowns == sorted(writedowns) and writedowns[0] > 0
    for summary in levels.values():
        assert summary.cet1_after_writedown == pytest.approx(
            summary.cet1_at_foltf - summary.writedown
        )
        assert summary.losses_to_date == pytest.approx(
            CONFIG.banks[bank].capital.cet1 - summary.cet1_at_foltf
        )
        if summary.recapitalisation_need > TOL and summary.shortfall < TOL:
            # After conversion CET1 is exactly back at the requirement.
            restored = summary.cet1_after_writedown + summary.recapitalisation_need
            assert restored / summary.rwa_after == pytest.approx(summary.requirement)
        if summary.recapitalisation_need < TOL:
            assert summary.cet1_after_writedown / summary.rwa_after >= summary.requirement - TOL


def test_sale_of_business_uses_the_same_numbers_as_bail_in():
    bail_in = levels_for("universal", "fast_run", "early_foltf")["central"]
    choices = [
        Choice(c.decision, "sale_of_business" if c.decision == "resolution_tool" else c.option, c.day)
        for c in choices_of(DECISIONS.paths["early_foltf"])
    ]
    result = simulate(
        CONFIG.banks["universal"], CONFIG.model, CONFIG.scenarios["fast_run"], DECISIONS, choices
    )
    sale = resolution_summary(CONFIG.banks["universal"], CONFIG.model, result)
    assert sale.tool == "sale_of_business" and bail_in.tool == "bail_in"
    assert sale.recapitalisation_need == pytest.approx(bail_in.recapitalisation_need)
    assert [l.absorbed for l in sale.lines] == pytest.approx([l.absorbed for l in bail_in.lines])
    assert sale.liquidation is None


# ------------------------------------------------- MREL and the breakpoint


def test_breakpoint_is_where_available_mrel_stops_being_enough():
    bank = CONFIG.banks[SHOWCASE.bank]
    result = run_path(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)
    summary = resolution_summary(bank, CONFIG.model, result)
    point = summary.breakpoint_share
    assert point is not None and 0 < point < 1

    def at(share: float):
        parameter = Parameter(value=share, unit="x", source="ASSUMPTION", low=share, high=share)
        params = CONFIG.model.model_copy(update={"resolution_writedown_share": parameter})
        return resolution_summary(bank, params, result)

    just_below, just_above = at(point - 0.001), at(point + 0.001)
    assert just_below.mrel_sufficient and not just_above.mrel_sufficient
    # Below the breakpoint senior preferred debt is untouched; above it, it is reached.
    preferred = lambda s: next(l for l in s.lines if l.key == "senior_preferred").absorbed  # noqa: E731
    assert preferred(just_below) == pytest.approx(0.0) and preferred(just_above) > 0
    assert at(point).breakpoint_share == pytest.approx(point)  # it does not depend on the level
    assert f"{100 * point:.1f}%" in breakpoint_text(summary)


def test_breakpoint_edge_cases():
    # Layers that cover any write-down up to the whole loan book: no breakpoint.
    assert breakpoint_share(cet1=10, rwa=50, loans=20, requirement=0.1, mrel_above_cet1=100) is None
    # Layers that do not cover the need even without a write-down.
    assert breakpoint_share(cet1=1, rwa=100, loans=80, requirement=0.1, mrel_above_cet1=2) == 0.0
    # No layers above CET1: the breakpoint is where CET1 falls to the requirement.
    point = breakpoint_share(cet1=10, rwa=50, loans=100, requirement=0.1, mrel_above_cet1=0)
    writedown = point * 100
    assert (10 - writedown) / (50 * (1 - point)) == pytest.approx(0.1)


@pytest.mark.parametrize("bank,scenario,path", RESOLVED)
def test_mrel_figures_follow_the_stack(bank, scenario, path):
    summary = levels_for(bank, scenario, path)["central"]
    layers = {line.key: line for line in summary.lines}
    assert summary.mrel_available == pytest.approx(
        summary.cet1_at_foltf + layers["at1_t2"].amount + layers["senior_non_preferred"].amount
    )
    covered = layers["at1_t2"].amount + layers["senior_non_preferred"].amount
    assert summary.mrel_sufficient == (summary.recapitalisation_need <= covered + TOL)
    if summary.mrel_sufficient:
        assert layers["senior_preferred"].absorbed == pytest.approx(0.0)
        assert layers["uncovered_deposits"].absorbed == pytest.approx(0.0)


# ------------------------------------------------- showcase (acceptance)


def test_showcase_bail_in_at_the_three_write_down_levels():
    levels = levels_for(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)
    low, central, high = (levels[level] for level in LEVELS)
    used = lambda s: {l.key: l.absorbed for l in s.lines[1:] if l.absorbed > TOL}  # noqa: E731
    # Low: CET1 alone absorbs the write-down and stays above the requirement.
    assert low.recapitalisation_need == pytest.approx(0.0) and used(low) == {}
    # Central: part of AT1 and Tier 2 is converted, nothing else.
    assert set(used(central)) == {"at1_t2"} and central.mrel_sufficient
    # High: past the breakpoint, senior preferred debt is reached.
    assert set(used(high)) == {"at1_t2", "senior_non_preferred", "senior_preferred"}
    assert not high.mrel_sufficient and high.shortfall == pytest.approx(0.0)
    assert central.writedown_share < central.breakpoint_share < high.writedown_share
    assert central.resolution_weekend and (central.foltf_day, central.end_day) == (11, 13)
    assert central.queued > 0 and central.liquidity_need >= central.queued - TOL


def test_resolution_summary_table_for_bail_in_against_liquidation():
    """Acceptance: a resolution summary for two paths, from a Wednesday to a Friday FoLtF."""
    scenario = CONFIG.scenarios[SHOWCASE.scenario]
    assert scenario.start_weekday == "Wednesday" and scenario.weekday(10) == "Friday"
    bail_in = levels_for(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.path)["central"]
    wind_up = levels_for(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.compare_path)["central"]
    assert (bail_in.tool, wind_up.tool) == ("bail_in", "liquidation")
    rows_a, rows_b = dict(summary_rows(bail_in, "EUR bn")), dict(summary_rows(wind_up, "EUR bn"))
    shared = [label for label in rows_a if label in rows_b]
    assert {"Tool", "FoLtF in effect", "MREL requirement (EUR bn)"} <= set(shared)
    assert "resolution weekend" in rows_a["FoLtF in effect"]
    assert rows_a["Tool"] == "Bail-in" and rows_b["Tool"] == "Liquidation"
    assert any(label.startswith("AT1 and Tier 2: written down") for label in rows_a)
    assert any(label.startswith("Liquidation, Uncovered deposits") for label in rows_b)
    assert "DGS payout of covered deposits (EUR bn)" in rows_b
    assert rows_a["Of which requested, not yet settled (EUR bn)"] != "0.00"


# ------------------------------------------------- liquidation


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("bank,scenario,path", [r for r in RESOLVED if "liquidation" in r[2]])
def test_liquidation_pays_claims_in_order(bank, scenario, path, level):
    result = run_path(bank, scenario, path)
    summary = resolution_summary(CONFIG.banks[bank], CONFIG.model, result, level)
    wind_up, last = summary.liquidation, result.table.iloc[-1]
    assert wind_up.haircut == level_value(CONFIG.model.liquidation_haircut, level)
    assert wind_up.asset_value == pytest.approx(
        last["total_assets"] - wind_up.haircut * last["illiquid_assets"]
    )
    keys = [line.key for line in wind_up.lines]
    assert keys == ["covered_deposits", "uncovered_deposits", "senior_preferred",
                    "senior_non_preferred", "at1_t2", "cet1"]
    # A claim loses something only if every claim after it in the order loses everything.
    for senior, junior in zip(wind_up.lines, wind_up.lines[1:]):
        if senior.absorbed > TOL:
            assert junior.absorbed == pytest.approx(junior.amount)
    recovered = sum(line.amount - line.absorbed for line in wind_up.lines)
    assert recovered <= max(0.0, wind_up.asset_value - wind_up.secured) + 1e-6
    # The scheme pays covered depositors in full and recovers from the estate.
    assert wind_up.dgs_payout == pytest.approx(last["dep_insured_retail"])
    assert 0 <= wind_up.dgs_recovery <= wind_up.dgs_payout + TOL
    assert wind_up.payout_days == result.scorecard.payout_days


def test_liquidation_losses_grow_with_the_haircut():
    levels = levels_for(SHOWCASE.bank, SHOWCASE.scenario, SHOWCASE.compare_path)
    lost = [sum(line.absorbed for line in levels[level].liquidation.lines) for level in LEVELS]
    assert lost == sorted(lost) and lost[0] < lost[2]
    low, high = levels["low"].liquidation, levels["high"].liquidation
    uncovered = lambda w: next(l for l in w.lines if l.key == "uncovered_deposits").absorbed  # noqa: E731
    assert uncovered(low) == pytest.approx(0.0) and uncovered(high) > 0


# ------------------------------------------------- resolution weekend in the script


def test_script_continues_through_the_resolution_weekend():
    pack = showcase_pack()
    branch = pack.branches[1]  # bail-in
    assert branch.resolution is not None and set(branch.resolution) == set(LEVELS)
    authority = branch.injects["resolution_authority"]
    steps = [i for i in authority if i.kind == "resolution_step"]
    assert [i.title for i in steps] == [item.title for item in SCRIPT.resolution.values()]
    # Saturday: valuation. Sunday: losses, communication, liquidity. Monday: the run ends.
    assert [(i.day, i.weekday, i.time) for i in steps] == [
        (11, "Saturday", "12:00"), (12, "Sunday", "10:00"), (12, "Sunday", "15:00"),
        (12, "Sunday", "17:00"),
    ]
    assert authority[-1].kind == "resolution" and authority[-1].day == 13
    valuation, losses, communication, liquidity = steps
    text = lambda inject: " | ".join(f.text for f in inject.resolution)  # noqa: E731
    assert "Valuation write-down" in text(valuation) and "Need:" in text(valuation)
    assert "Available MREL" in text(valuation) and "Write-down at which" in text(valuation)
    assert "AT1 and Tier 2" in text(losses) and "Covered deposits, protected" in text(losses)
    assert communication.resolution == () and "announces" in communication.text
    assert "requested, not yet settled" in text(liquidity)
    assert valuation.sender == "The independent valuer"
    assert all(i.figures == () and i.headline == () for i in steps)


def test_resolution_injects_go_only_to_their_recipients():
    pack = showcase_pack()
    for branch in pack.branches:
        for actor in ACTORS:
            titles = {i.title for i in branch.injects[actor] if i.kind == "resolution_step"}
            expected = {item.title for item in SCRIPT.resolution.values() if actor in item.recipients}
            assert titles == expected, actor
    scheme = [i for i in pack.branches[0].injects["deposit_guarantee_scheme"] if i.resolution]
    assert [i.title for i in scheme] == ["Who bears the losses"]
    assert any("DGS payout" in f.text for f in scheme[0].resolution)  # the liquidation branch


def test_players_documents_use_the_chosen_write_down_level():
    central, high = showcase_pack("central"), showcase_pack("high")
    assert (central.writedown_level, high.writedown_level) == ("central", "high")
    files_central = pack_files(central, SCRIPT, DECISIONS)
    files_high = pack_files(high, SCRIPT, DECISIONS)
    name = "control_team/inject_sheets/resolution_authority.docx"
    assert "4% (central)" in docx_text(files_central[name])
    text = docx_text(files_high[name])
    assert "8% (high)" in text and "4% (central)" not in text
    assert "Estimates. They rest on an assumed write-down" in text
    assert "Communication before the bank reopens" in text
    # The run itself is the same at every level.
    assert central.branches[1].result.table.equals(high.branches[1].result.table)


def test_resolution_period_is_clamped_to_the_run_when_foltf_is_midweek():
    pack = build_pack(
        CONFIG.banks["universal"], CONFIG.model, CONFIG.scenarios["fast_run"], DECISIONS, SCRIPT,
        [(DECISIONS.paths["early_foltf"].name, choices_of(DECISIONS.paths["early_foltf"]))],
    )
    branch = pack.branches[0]
    result = branch.result
    assert not result.resolution_weekend
    steps = [i for i in branch.injects["resolution_authority"] if i.kind == "resolution_step"]
    assert len(steps) == len(SCRIPT.resolution)
    assert all(result.foltf_day <= i.day <= result.end_day for i in steps)


def test_control_team_pack_has_the_resolution_summary_and_the_limits():
    pack = showcase_pack()
    files = pack_files(pack, SCRIPT, DECISIONS)
    text = docx_text(files["control_team/control_team_pack.docx"])
    assert text.count("Resolution summary") >= 2  # one per branch
    for word in ("Low", "Central", "High"):
        assert word in text
    assert breakpoint_text(pack.branches[1].resolution["central"]) in text
    assert "Legal loss-absorption thresholds are not modelled" in text
    assert "order of loss absorption used in this model" in text
    assert "2% (low)" in text and "4% (central)" in text and "8% (high)" in text
    table = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_resolution_summary.csv"]))
    assert set(table["branch"]) == {"A", "B"} and set(table["level"]) == set(LEVELS)
    assert len(files) == 18


# ------------------------------------------------- CLI and app


def test_cli_prints_the_resolution_summary(capsys):
    assert main(["--showcase", "--writedown", "high"]) == 0
    out = capsys.readouterr().out
    assert "Resolution summary" in out and "8% (high)" in out
    assert "Write-down at which available MREL is no longer enough" in out
    assert "+ requested, not yet settled" in out
    assert main(["--bank", "universal", "--scenario", "fast_run"]) == 0
    assert "Resolution summary" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--showcase", "--writedown", "extreme"])


def test_app_shows_the_resolution_summary_for_both_paths():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_DECISIONS
    at.run(timeout=60)
    assert not at.exception
    at.selectbox(key="reference_path").set_value(SHOWCASE.compare_path).run(timeout=60)
    assert any(h.value == "Resolution summary" for h in at.subheader)
    table = at.dataframe[1].value
    assert list(table.columns) == ["Figure", "FoLtF on the second Friday, liquidation", "Selected path"]
    tool = table[table["Figure"] == "Tool"].iloc[0]
    assert tool["Selected path"] == "Bail-in" and tool.iloc[1] == "Liquidation"
    writedown = lambda: at.dataframe[1].value.set_index("Figure").loc[  # noqa: E731
        "Valuation write-down (assumed, share of the loan book)", "Selected path"
    ]
    assert writedown() == "4% (central)"
    at.radio(key="writedown_level").set_value("high").run(timeout=60)
    assert not at.exception and writedown() == "8% (high)"
    assert any("7.9% of the loan book" in c.value for c in at.caption)
