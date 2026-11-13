"""SPEC_V3 phase I: the draft closing report, with counterfactuals from the engine."""

import io
import re
from pathlib import Path

import pandas as pd
import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from engine import live
from engine.config import load_config
from engine.decisions import Choice, choices_of
from engine.export import (
    RECOMMENDATION_ROWS,
    SIMULATION_NOTICE,
    TO_COMPLETE,
    closing_report_docx,
    report_files,
)
from engine.live_run import main as live_main
from engine.live_run import scripted_showcase
from engine.model import simulate
from engine.report import CONSULT, PASS_ON, live_report, planned_report
from engine.run import main as run_main

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
PLANNED = choices_of(DECISIONS.paths[SHOWCASE.path])
# Words that would assess the players. The report states facts and differences only.
JUDGING = ("should", "failed to", "too late", "too early", "mistake", "error", "wrong", "right",
           "correct", "good", "bad", "poor", "better", "worse", "well", "badly")
TERMS = ("failing or likely to fail", "payments default")  # names of things, not judgements


@pytest.fixture(scope="module")
def run():
    return scripted_showcase(CONFIG)


@pytest.fixture(scope="module")
def report(run):
    return live_report(*run)


def docx_text(content: bytes) -> str:
    body = Document(io.BytesIO(content)).element.body
    return "\n".join(node.text or "" for node in body.iter() if node.tag.endswith("}t"))


def judging_words(text: str) -> list[str]:
    text = text.lower()
    for term in TERMS:
        text = text.replace(term, "")
    return [word for word in JUDGING if re.search(rf"\b{re.escape(word)}\b", text)]


# ------------------------------------------------- acceptance


def test_report_of_the_scripted_run_has_at_least_three_counterfactuals(run, report):
    state, _ = run
    computed = [case for case in report.counterfactuals if case.possible]
    assert len(computed) >= 3 and len(report.counterfactuals) == 17
    assert report.live and report.actual == state.branch.result.scorecard
    overview = dict(report.overview)
    assert overview["Status"] == "Ended" and overview["Outcome"] == "Liquidation (day 13)"
    assert overview["Decisions recorded"].startswith("5, of which 2 different")
    assert overview["Real duration"] == "130 minutes, of which 46 paused"
    # Every decision has other options and, where it changed the path, a day earlier and later.
    by_decision = {}
    for case in report.counterfactuals:
        by_decision.setdefault(case.decision_name, []).append(case.change)
    tool = by_decision["Resolution tool"]
    assert len(tool) == 5 and sum("one day" in change for change in tool) == 2
    assert len(by_decision["Emergency liquidity assistance (ELA)"]) == 3  # no decision: options only
    assert by_decision["All decisions"] == ["every decision as on the pre-planned path"]


def test_no_generated_sentence_judges_the_players(run, report):
    text = docx_text(closing_report_docx(report, SCRIPT))
    assert judging_words(text) == []
    for case in report.counterfactuals:
        assert judging_words(f"{case.change} {case.differences}") == []
    for step in report.steps:
        assert judging_words(f"{step.step} {step.how}") == []
    texts = SCRIPT.report
    assert judging_words(" ".join([texts.intro, texts.circulation, texts.coordination_note,
                                   texts.counterfactual_note])) == []
    planned = planned_report("Planned", CONFIG.banks[SHOWCASE.bank], CONFIG.model,
                             CONFIG.scenarios[SHOWCASE.scenario], DECISIONS, SCRIPT, PLANNED)
    assert judging_words(docx_text(closing_report_docx(planned, SCRIPT))) == []
    # The check itself works.
    assert judging_words("The authority should have acted; it was too late.") == ["should", "too late"]
    assert judging_words("Failing or likely to fail was declared.") == []


# ------------------------------------------------- decisions and figures


def test_timeline_gives_each_decision_with_its_timing_and_the_figures_held(run, report):
    state, context = run
    assert [(l.day, l.decision) for l in report.decisions] == [(d.day, d.decision) for d in state.decisions]
    table = state.branch.result.table.set_index("day")
    tool = next(l for l in report.decisions if l.decision == "resolution_tool")
    assert not tool.as_planned and tool.applied and tool.planned_name == "Bail-in"
    assert tool.consulted == ("Competent authority", "Central bank", "Deposit guarantee scheme")
    # The resolution authority's figures are one day old; they are its own set only.
    assert tool.figures_day == tool.day - 1
    labels = context.decisions.information["resolution_authority"].figures
    assert len(tool.figures) == len(labels)
    assert f"Deposits lost {table.loc[11, 'cum_deposit_loss_pct']:.1f}%" in tool.figures
    assert not any(f.startswith("Liquidity buffer") for f in tool.figures)
    # Daily reporting, decided on day 6, gives the competent authority same-day figures later.
    early, foltf = (next(l for l in report.decisions if l.decision == key)
                    for key in ("early_intervention_measures", "declare_foltf"))
    assert early.figures_day == early.day - 1 and foltf.figures_day == foltf.day
    for line, record in zip(report.decisions, state.decisions):
        assert line.minutes == pytest.approx(record.seconds_taken / 60) and line.real_time == record.real_time
    stuck = next(l for l in report.decisions if l.decision == "prepare_payout")
    assert stuck.by_control and not stuck.applied and stuck.as_planned


def test_counterfactuals_change_one_decision_and_match_the_engine(run, report):
    state, context = run
    setup = state.setup
    actual = list(live.effective_choices(state, context))
    assert {(c.decision, c.option, c.day) for c in actual} == {
        ("early_intervention_measures", "enhanced_reporting", 6), ("declare_foltf", "declare", 10),
        ("resolution_tool", "liquidation", 12)}
    sim = lambda choices: simulate(setup.bank, context.params, setup.scenario,  # noqa: E731
                                   context.decisions, choices).scorecard
    others = [c for c in actual if c.decision != "resolution_tool"]
    cases = {case.change: case for case in report.counterfactuals}
    bail_in = next(case for change, case in cases.items() if change.startswith('"Bail-in" instead'))
    assert bail_in.scorecard == sim(others + [Choice("resolution_tool", "bail_in", 12)])
    assert "Resolution: bail-in" in bail_in.differences
    later = next(case for change, case in cases.items()
                 if "liquidation\" one day later" in change)
    assert later.scorecard == sim(others + [Choice("resolution_tool", "liquidation", 13)])
    assert "last simulated day 14 instead of 13" in later.differences
    planned = cases["every decision as on the pre-planned path"]
    assert planned.scorecard == sim(setup.planned) and planned.scorecard.outcome == "bail_in"
    # A day earlier than the decision opened is said, not computed.
    early = next(case for change, case in cases.items() if "Require daily reporting\" one day earlier" in change)
    assert not early.possible and early.scorecard is None and "not open" in early.differences
    # Moving the declaration by a day shows in the day FoLtF is in effect.
    moved = next(case for change, case in cases.items() if "likely to fail\" one day earlier" in change)
    assert moved.differences == "FoLtF in effect on day 10 instead of day 11"


def test_coordination_steps_are_recorded_as_done_or_not_recorded_as_done(run, report):
    state, _ = run
    consult = [s for s in report.steps if s.step == CONSULT]
    relays = [s for s in report.steps if s.step == PASS_ON]
    expected = sum(len(SCRIPT.coordination[d.decision].consult) for d in state.decisions)
    assert len(consult) == expected == 8
    done = {(s.by, s.to) for s in consult if s.recorded}
    assert ("Resolution authority", "Deposit guarantee scheme") in done
    assert ("Competent authority", "Central bank") not in done
    assert sum(s.recorded for s in relays) == 1 and len(relays) == 9
    assert {s.how for s in relays} == {"passed on by the player",
                                       "the control team released the fallback inject"}
    text = docx_text(closing_report_docx(report, SCRIPT))
    assert "Recorded as done" in text and "Not recorded as done" in text
    assert "Not done" not in text and SCRIPT.report.coordination_note in text


# ------------------------------------------------- the document


def test_report_document_marks_what_is_left_to_people(report):
    files = report_files(report, SCRIPT)
    assert sorted(files) == ["SIMULATION_closing_report_draft.docx",
                             "SIMULATION_report_counterfactuals.csv",
                             "SIMULATION_report_decisions.csv"]
    content = files["SIMULATION_closing_report_draft.docx"]
    document = Document(io.BytesIO(content))
    for section in document.sections:
        assert SIMULATION_NOTICE in section.header.paragraphs[0].text
        assert SIMULATION_NOTICE in section.footer.paragraphs[0].text
    text = docx_text(content)
    for heading in ("1. Overview", "2. Timeline of decisions", "3. Coordination steps",
                    "4. Conduct of the exercise", "5. What the model gives with one decision changed",
                    "6. Observers' notes", "7. Feedback received", "8. Lessons learned",
                    "9. Recommendations"):
        assert heading in text
    assert text.count(f"[{TO_COMPLETE}]") == len(SCRIPT.report.human_sections) + 1 == 4
    recommendations = document.tables[-1]
    assert [cell.text for cell in recommendations.rows[0].cells] == [
        "Recommendation", "Assigned to", "Priority", "To be actioned by"]
    assert len(recommendations.rows) == RECOMMENDATION_ROWS + 1
    assert all(cell.text == "" for row in recommendations.rows[1:] for cell in row.cells)
    decisions = pd.read_csv(io.BytesIO(files["SIMULATION_report_decisions.csv"]))
    cases = pd.read_csv(io.BytesIO(files["SIMULATION_report_counterfactuals.csv"]))
    assert len(decisions) == 5 and len(cases) == 17 and "Outcome" in cases.columns
    assert list(decisions["real_minutes_taken"]) == [3.0, 3.0, 4.5, 25.5, 25.5]


def test_report_can_be_drawn_up_before_the_end_and_for_a_linear_exercise(run):
    state, context = run
    entries = [live.Entry(e.kind, e.real_time, e.data) for e in state.entries[:30]]
    part = live_report(live.replay(state.setup, entries, context), context)
    assert dict(part.overview)["Status"].startswith("Not finished")
    assert any(label.startswith("Outcome expected") for label, _ in part.overview)
    setup = live.make_setup("Linear", CONFIG.banks[SHOWCASE.bank], CONFIG.model,
                            CONFIG.scenarios[SHOWCASE.scenario], DECISIONS, SCRIPT, PLANNED,
                            mode="linear")
    linear = live.start(setup, "2028-03-01T09:00:00", context)
    minute = 1
    while not linear.ended:
        if linear.clock == (10, live.CLOSE) and not linear.decisions:
            linear = live.apply(linear, "decision", f"2028-03-01T09:{minute:02d}:30", context,
                                decision="resolution_tool", option="liquidation")
        linear = live.apply(linear, "step", f"2028-03-01T09:{minute:02d}:00", context)
        minute += 1
    result = live_report(linear, context)
    assert result.actual.outcome == "bail_in"  # the fixed path, whatever was recorded
    line = result.decisions[0]
    assert not line.as_planned and not line.applied
    assert not any(step.step == PASS_ON for step in result.steps)
    assert any('"No public interest, liquidation" instead of "Bail-in"' in case.change
               for case in result.counterfactuals)


def test_report_for_a_pre_planned_path_has_no_real_times():
    planned = planned_report("Planned", CONFIG.banks[SHOWCASE.bank], CONFIG.model,
                             CONFIG.scenarios[SHOWCASE.scenario], DECISIONS, SCRIPT, PLANNED)
    assert not planned.live and planned.bank_name == SCRIPT.setup.bank_alias
    assert [l.decision for l in planned.decisions] == ["declare_foltf", "resolution_tool"]
    assert all(l.real_time == "not recorded" and l.minutes is None for l in planned.decisions)
    assert planned.steps == () and len([c for c in planned.counterfactuals if c.possible]) >= 3
    text = docx_text(closing_report_docx(planned, SCRIPT))
    assert "The path was not played" in text and "4. Conduct of the exercise" not in text


# ------------------------------------------------- consultation, CLI and console


def test_consultation_is_recorded_with_the_decision(run):
    state, context = run
    assert [d.consulted for d in state.decisions][0] == ("competent_authority",)
    setup = live.make_setup("x", CONFIG.banks[SHOWCASE.bank], CONFIG.model,
                            CONFIG.scenarios[SHOWCASE.scenario], DECISIONS, SCRIPT, PLANNED)
    opened = live.start(setup, "2028-03-01T09:00:00", context)
    while not live.open_decisions(opened):
        opened = live.apply(opened, "step", "2028-03-01T09:01:00", context)
    for bad in (["central_bank"], ["the_press"]):
        with pytest.raises(live.LiveError):
            live.apply(opened, "decision", "2028-03-01T09:02:00", context, decision="ela",
                       option="refuse", consulted=bad)


def test_cli_writes_the_report(tmp_path, capsys):
    assert live_main(["--report-dir", str(tmp_path / "live")]) == 0
    out = capsys.readouterr().out
    assert "what the model gives with one decision changed" in out
    assert (tmp_path / "live" / "SIMULATION_closing_report_draft.docx").exists()
    assert run_main(["--showcase", "--report-dir", str(tmp_path / "planned")]) == 0
    capsys.readouterr()
    text = docx_text((tmp_path / "planned" / "SIMULATION_closing_report_draft.docx").read_bytes())
    assert "Pre-planned path" in text


def test_console_offers_the_report_and_the_consulted_field():
    at = AppTest.from_file(APP_PATH)
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="live_start").click().run(timeout=60)
    for _ in range(40):
        if any(i.decision_due.decision == "ela"
               for i in live.open_decisions(at.session_state["live"])):
            break
        at.button(key="live_step").click().run(timeout=60)
    at.multiselect(key="live_consulted_ela").set_value(["competent_authority"]).run(timeout=60)
    at.selectbox(key="live_option_ela").set_value("refuse").run(timeout=60)
    at.button(key="live_record_ela").click().run(timeout=60)
    assert not at.exception
    assert at.session_state["live"].decisions[-1].consulted == ("competent_authority",)
    report = live_report(at.session_state["live"],
                         live.Context(CONFIG.model, DECISIONS, SCRIPT))
    assert any(step.recorded for step in report.steps if step.step == CONSULT)
