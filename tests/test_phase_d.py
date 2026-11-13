"""SPEC_V2 phase D: information sets, injects per actor, branches, control team pack, export."""

import io
import re
from dataclasses import replace
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import yaml
from docx import Document
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from app import demo
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import OUTCOME_LABELS, Choice, choices_of, lag_days
from engine.export import pack_files, pack_to_zip
from engine.model import simulate
from engine.run import main
from engine.schema import ACTORS, DECISION_STAGES, INTRADAY_BLOCKS, Parameter
from engine.script import branch_code, build_pack

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SCENARIOS = ["fast_run", "slow_burn", "cyber_outage", "geopolitical"]
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
STAGE_OF = {name: index for index, name in enumerate(DECISION_STAGES, start=1)}
# A figure as printed in a document: a decimal number, or a percentage.
FIGURE_TOKEN = re.compile(r"\d+\.\d+%?|\d+%")


def path_entry(key: str):
    return DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key])


def pack_for(bank: str, scenario: str, *paths: str, extra: tuple = ()):
    entries = [path_entry(key) for key in paths]
    if extra:
        entries.append(("Custom", extra))
    return build_pack(
        CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], DECISIONS, SCRIPT, entries
    )


@pytest.fixture(scope="module")
def acceptance():
    """The acceptance export: universal bank, fast run, act late against early FoLtF."""
    return pack_for("universal", "fast_run", "act_late", "early_foltf")


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def all_injects(pack, actor: str):
    for branch in pack.branches:
        for inject in branch.injects[actor]:
            yield branch, inject


# ------------------------------------------------- config


def test_script_texts_and_questions_carry_no_figures():
    for text in list(SCRIPT.texts.values()) + list(SCRIPT.questions.values()) + SCRIPT.debrief:
        assert not re.search(r"\d", text), text


def test_script_covers_every_decision_and_every_actor():
    assert set(SCRIPT.questions) == set(SCRIPT.coordination) == set(DECISIONS.decisions)
    assert set(SCRIPT.actors) == set(DECISIONS.information) == set(ACTORS)
    for key, coordination in SCRIPT.coordination.items():
        assert DECISIONS.decisions[key].actor not in coordination.consult + coordination.inform


def test_information_sets_follow_the_spec_examples():
    info = DECISIONS.information
    assert info["bank_management"].lag_days == 0 and info["bank_management"].informed_from is None
    assert info["competent_authority"].lag_days == 1
    assert info["resolution_authority"].informed_from == "early_intervention"
    assert info["deposit_guarantee_scheme"].informed_from == "likely_to_fail"
    assert set(info["deposit_guarantee_scheme"].figures) == {"dep_insured_retail", "cum_deposit_loss_pct"}
    central = set(info["central_bank"].figures)
    assert {"outflow_total", "unencumbered_collateral"} <= central and "cet1_ratio" not in central
    assert set(info["bank_management"].figures) > set(info["resolution_authority"].figures)


def _broken(tmp_path, file: str, change):
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    data = yaml.safe_load((target / file).read_text())
    change(data)
    (target / file).write_text(yaml.safe_dump(data))
    return target


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["texts"].update(recovery="Buffer is {liquidity_buffer}."),  # a figure
        lambda d: d["texts"].pop("news"),
        lambda d: d["clock"].update(stage="6pm"),
        lambda d: d["clock"].pop("end"),
        lambda d: d["questions"].pop("ela"),
        lambda d: d["coordination"]["ela"]["consult"].append("central_bank"),  # itself
        lambda d: d["coordination"]["ela"]["inform"].append("the_press"),
        lambda d: d["actors"].pop("central_bank"),
        lambda d: d.update(debrief=[]),
        lambda d: d["decision_window_days"].update(value=1.5),
    ],
)
def test_invalid_script_config_is_rejected(tmp_path, change):
    with pytest.raises(ValidationError):
        load_config(_broken(tmp_path, "script.yaml", change))


def test_weekday_labels_start_on_the_scenario_weekday(tmp_path):
    scenario = CONFIG.scenarios["fast_run"]
    assert scenario.start_weekday == "Monday"
    assert [scenario.weekday(day) for day in (1, 5, 6, 7, 8)] == [
        "Monday", "Friday", "Saturday", "Sunday", "Monday",
    ]
    wednesday = scenario.model_copy(update={"start_weekday": "Wednesday"})
    assert wednesday.weekday(3) == "Friday"
    with pytest.raises(ValidationError):
        load_config(_broken(tmp_path, "scenarios.yaml",
                            lambda d: d["fast_run"].update(start_weekday="Someday")))


# ------------------------------------------------- the information gate (acceptance)


def allowed_tokens(pack, actor: str) -> set[str]:
    """Every number that may be printed in an actor's document."""
    tokens = set()
    for _, inject in all_injects(pack, actor):
        for figure in inject.figures + inject.headline + inject.notified + inject.resolution:
            tokens.update(FIGURE_TOKEN.findall(figure.text))
            tokens.update(FIGURE_TOKEN.findall(figure.threshold_text))
    return tokens


def check_gate(pack, actor: str) -> int:
    """Every figure an actor is given is one it may have, old enough, and real.

    - Regular report: from the actor's own set, as old as its delay.
    - Notification figures: those that triggered a stage it is told about, of that day.
    - Figures recalled in a decision inject: only ones it was notified of before.
    Returns the number of figures checked.
    """
    info = DECISIONS.information[actor]
    checked = 0
    for branch in pack.branches:
        result = branch.result
        table = result.table.set_index("day")
        stage_events = {e.kind: e for e in result.events if e.kind in DECISION_STAGES}
        informed = 0 if info.informed_from is None else (
            stage_events[info.informed_from].day if info.informed_from in stage_events else None
        )
        faster = dict(result.faster_reporting_from).get(actor)
        told: dict[str, object] = {}
        for inject in branch.injects[actor]:
            if inject.figures or inject.headline or inject.decision_due:
                assert informed is not None and inject.day >= informed, (actor, inject)
            # Regular report.
            for figure in inject.figures:
                assert figure.name in info.figures, (actor, figure.name)
                lag = lag_days(info, inject.day, faster)
                assert figure.as_of_day <= max(0, inject.day - lag), (actor, inject.kind, figure)
                expected = table.loc[figure.as_of_day, figure.name]
                if figure.block:
                    assert info.intraday
                    key = next(k for k, label in INTRADAY_BLOCKS.items() if label == figure.block)
                    expected *= getattr(CONFIG.model, f"intraday_share_{key}").value
                assert figure.value == pytest.approx(expected)
                checked += 1
            assert len({figure.as_of_day for figure in inject.figures}) <= 1
            # Notification figures.
            if inject.headline:
                assert inject.stages and inject.figures == ()
                triggered = {
                    name: value
                    for kind in inject.stages
                    for name, value, _ in stage_events[kind].figures
                }
                for kind in inject.stages:
                    assert stage_events[kind].day == inject.day
                    if info.informed_from is not None:
                        assert STAGE_OF[kind] >= STAGE_OF[info.informed_from]
                for figure in inject.headline:
                    assert figure.as_of_day == inject.day
                    assert figure.value == pytest.approx(triggered[figure.name])
                    told[figure.name] = figure
                    checked += 1
            for figure in inject.notified:
                assert told.get(figure.name) == figure and figure.as_of_day <= inject.day
            # Resolution estimates: only in the resolution period, to the listed recipients.
            if inject.resolution:
                assert inject.kind == "resolution_step" and result.foltf_day is not None
                assert result.foltf_day <= inject.day <= result.end_day
                item = next(i for i in SCRIPT.resolution.values() if i.title == inject.title)
                assert actor in item.recipients and item.figures
                checked += len(inject.resolution)
    return checked


@pytest.mark.parametrize("actor", ACTORS)
def test_no_actor_is_given_a_figure_outside_its_information_set(acceptance, actor):
    assert check_gate(acceptance, actor) > 0


def test_resolution_authority_document_shows_only_what_it_had_at_that_time(acceptance):
    """Acceptance: a reader with only this document cannot see figures the authority lacked."""
    files = pack_files(acceptance, SCRIPT, DECISIONS)
    text = docx_text(files["players/resolution_authority.docx"])
    printed = set(FIGURE_TOKEN.findall(text))
    allowed = allowed_tokens(acceptance, "resolution_authority")
    assert printed and printed <= allowed, printed - allowed
    # Figures of other actors are absent: the bank's same-day liquidity buffer, for one.
    late = acceptance.branches[0].result.table.set_index("day")
    assert f"{late.loc[1, 'liquidity_buffer']:.2f}" not in text
    body = text.split("Glossary")[0]  # the glossary explains terms; it holds no figures
    assert "Liquidity buffer" not in body and "Unencumbered collateral" not in body
    # It is told nothing about the recovery breach, which comes before it is informed.
    injects = [inject for _, inject in all_injects(acceptance, "resolution_authority")]
    stages = {stage for inject in injects for stage in inject.stages}
    assert "recovery" not in stages and "early_intervention" in stages
    # Its first notification comes from the competent authority, with that day's triggers.
    shared = next(d for d in acceptance.documents if d.actor == "resolution_authority").shared
    first = next(i for i in shared if i.headline)
    assert (first.day, first.sender, first.time) == (1, "Competent authority", "19:00")
    assert {f.as_of_day for f in first.headline} == {1} and first.figures == ()
    # Its regular report is one day old whenever it gets one.
    reports = [i for i in injects if i.figures and i.day >= 1]
    assert reports and all(i.day - i.figures[0].as_of_day == 1 for i in reports)


def test_no_actor_is_told_of_a_stage_next_to_figures_that_deny_it(acceptance):
    """A stage notification carries the figures that triggered it and no older table."""
    for actor in ACTORS:
        for branch, inject in all_injects(acceptance, actor):
            if not inject.stages:
                continue
            assert inject.headline and inject.figures == ()
            events = {e.kind: e for e in branch.result.events}
            for figure in inject.headline:
                assert figure.as_of_day == inject.day
                if figure.name != "unmet_outflow":
                    assert figure.threshold_text
            if "likely_to_fail" in inject.stages:
                names = {name for name, _, _ in events["likely_to_fail"].figures}
                assert names <= {figure.name for figure in inject.headline}
    # In the fast run the flag is raised on day 1 with 15.2% of deposits already gone.
    authority = [i for _, i in all_injects(acceptance, "competent_authority") if i.stages][0]
    loss = next(f for f in authority.headline if f.name == "cum_deposit_loss_pct")
    assert loss.value == pytest.approx(15.2, abs=0.05)


def test_notifications_that_arrive_together_are_one_inject(acceptance):
    for actor in ACTORS:
        for branch in acceptance.branches:
            stage_injects = [i for i in branch.injects[actor] if i.stages]
            slots = [(i.day, i.time) for i in stage_injects]
            assert len(slots) == len(set(slots))
            told = [stage for i in stage_injects for stage in i.stages]
            assert len(told) == len(set(told))  # each stage change is notified once
    bank = [i for i in acceptance.branches[0].injects["bank_management"] if i.stages][0]
    assert bank.stages == ("recovery", "early_intervention", "likely_to_fail")
    assert bank.text.count("\n") == 2 and bank.title == "The bank is likely to fail"
    names = [figure.name for figure in bank.headline]
    assert len(names) == len(set(names))  # one table, each figure once


def test_stage_notifications_flow_from_the_bank_to_the_authority_to_the_others(acceptance):
    expected = {
        "bank_management": ("18:00", None),
        "competent_authority": ("18:30", "Bank management"),
        "central_bank": ("19:00", "Competent authority"),
        "resolution_authority": ("19:00", "Competent authority"),
        "deposit_guarantee_scheme": ("19:00", "Competent authority"),
    }
    for actor, (time, sender) in expected.items():
        stage_injects = [i for _, i in all_injects(acceptance, actor) if i.stages]
        assert stage_injects
        for inject in stage_injects:
            assert inject.time == time
            if sender is None:
                assert "treasury" in inject.sender  # the bank's own functions
            else:
                assert inject.sender == sender and sender in inject.text
    assert SCRIPT.clock["stage"] < SCRIPT.clock["stage_relay"] < SCRIPT.clock["stage_relay_others"]
    assert SCRIPT.clock["stage_relay_others"] < SCRIPT.clock["decision_due"]


@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("bank", ["universal", "digital"])
def test_information_gate_holds_for_other_banks_scenarios_and_paths(bank, scenario, actor):
    pack = pack_for(bank, scenario, "support_package", "early_foltf_liquidation")
    check_gate(pack, actor)
    text = docx_text(pack_files(pack, SCRIPT, DECISIONS)[f"players/{actor}.docx"])
    assert set(FIGURE_TOKEN.findall(text)) <= allowed_tokens(pack, actor)


def test_daily_reporting_gives_the_competent_authority_same_day_figures():
    slow = pack_for("retail", "geopolitical", "act_late")
    fast = pack_for(
        "retail", "geopolitical",
        extra=(Choice("early_intervention_measures", "enhanced_reporting", 0),),
    )
    record = fast.branches[0].result.decision_log[0]
    assert fast.branches[0].result.faster_reporting_from == (
        ("competent_authority", record.effect_day),
    )
    def ages(pack):
        return {
            inject.day: inject.day - inject.figures[0].as_of_day
            for _, inject in all_injects(pack, "competent_authority")
            if inject.figures and inject.day >= 1
        }
    assert set(ages(slow).values()) == {1}
    after = {day: age for day, age in ages(fast).items() if day >= record.effect_day}
    before = {day: age for day, age in ages(fast).items() if day < record.effect_day}
    assert after and set(after.values()) == {0} and set(before.values()) == {1}
    check_gate(fast, "competent_authority")
    # Other actors are not affected.
    assert ages(slow) != ages(fast)
    assert [i for _, i in all_injects(slow, "resolution_authority")][0].figures == ()


def test_an_actor_cannot_decide_before_it_is_informed():
    # The scheme is informed only when the bank is likely to fail, a day after early
    # intervention in this run, although its decision is open from early intervention.
    # A stricter flag (one day of liquidity instead of three) puts a gap between the stages.
    scenario = CONFIG.scenarios["geopolitical"]
    stricter = Parameter(value=1, unit="days", source="ASSUMPTION")
    scenario = scenario.model_copy(
        update={"overrides": {**scenario.overrides, "likely_to_fail_survival_days": stricter}}
    )
    result = simulate(
        CONFIG.banks["retail"], CONFIG.model, scenario, DECISIONS,
        (Choice("prepare_payout", "prepare", 0),),
    )
    days = {e.kind: e.day for e in result.events}
    assert days["early_intervention"] < days["likely_to_fail"]
    assert result.decision_log[0].decided_day == days["likely_to_fail"]


# ------------------------------------------------- what an inject contains


@pytest.mark.parametrize("actor", ACTORS)
def test_every_inject_has_day_time_recipient_sender_and_text(acceptance, actor):
    injects = [inject for _, inject in all_injects(acceptance, actor)]
    assert injects and injects[0].kind == "opening"
    for inject in injects:
        assert inject.recipient == actor and inject.sender and inject.text and inject.title
        assert re.fullmatch(r"\d\d:\d\d", inject.time)
        assert (inject.weekday == "") == (inject.day == 0)
        assert not FIGURE_TOKEN.search(inject.text), inject.text  # numbers only in the figures
    for branch in acceptance.branches:
        ordered = [(i.day, i.time) for i in branch.injects[actor]]
        assert ordered == sorted(ordered)


def test_decision_due_gives_options_deadline_and_coordination(acceptance):
    seen = set()
    for actor in ACTORS:
        for _, inject in all_injects(acceptance, actor):
            due = inject.decision_due
            if due is None:
                continue
            definition = DECISIONS.decisions[due.decision]
            seen.add(due.decision)
            assert definition.actor == actor and inject.kind == "decision_due"
            assert [o.option for o in due.options] == list(definition.options)
            assert sum(o.is_default for o in due.options) == 1
            assert [o.branch_code for o in due.options] == [
                branch_code(DECISIONS, due.decision, o.option) for o in due.options
            ]
            assert due.deadline_day >= inject.day
            assert due.question == SCRIPT.questions[due.decision]
            assert {f.name for f in inject.notified} <= {
                f.name for _, i in all_injects(acceptance, actor) for f in i.headline
            }
            coordination = SCRIPT.coordination[due.decision]
            assert due.consult == tuple(SCRIPT.actors[a] for a in coordination.consult)
            assert due.inform == tuple(SCRIPT.actors[a] for a in coordination.inform)
            assert inject.figures  # the actor decides on its own figures
    assert {"declare_foltf", "ela", "moratorium", "resolution_tool", "public_statement"} <= seen
    assert "dgs_payout" not in seen  # no liquidation on either branch


def test_branch_codes_are_unique_and_stable():
    codes = [
        branch_code(DECISIONS, key, option)
        for key, decision in DECISIONS.decisions.items()
        for option in decision.options
    ]
    assert len(set(codes)) == len(codes)
    assert branch_code(DECISIONS, "public_statement", "no_statement") == "D1-a"


def test_a_decision_is_offered_only_when_it_is_open(acceptance):
    late, early = acceptance.branches
    due = lambda branch, actor: {  # noqa: E731
        i.decision_due.decision for i in branch.injects[actor] if i.decision_due
    }
    # The resolution authority can act only once FoLtF is declared: branch B only.
    assert due(late, "resolution_authority") == set()
    assert due(early, "resolution_authority") == {"moratorium", "resolution_tool"}
    assert due(late, "central_bank") == due(early, "central_bank") == {"ela"}
    liquidation = pack_for("universal", "fast_run", "early_foltf_liquidation").branches[0]
    assert "dgs_payout" in due(liquidation, "deposit_guarantee_scheme")


def test_deadlines_are_relative_to_the_branch_being_read(acceptance):
    late, early = acceptance.branches
    assert (late.result.failure_day, early.result.end_day) == (5, 4)
    # Branch A ends in a default on day 5, branch B in resolution on day 4.
    for branch, last_day in ((late, 5), (early, 4)):
        for point in branch.decision_points:
            assert point.deadline_day == max(point.open_day, last_day - 1)
        for actor in ACTORS:
            for inject in branch.injects[actor]:
                if inject.decision_due:
                    assert inject.decision_due.deadline_day == max(inject.day, last_day - 1)
    # In the shared part the reader does not know the branch: the earlier deadline is shown.
    for document in acceptance.documents:
        for inject in document.shared:
            if inject.decision_due:
                assert inject.decision_due.deadline_day == 3
                assert inject.decision_due.deadline_shared
        for _, injects in document.branches:
            assert not any(i.decision_due.deadline_shared for i in injects if i.decision_due)
    text = docx_text(pack_files(acceptance, SCRIPT, DECISIONS)["players/central_bank.docx"])
    assert "The branches have different deadlines; this is the earlier one." in text
    # A branch that runs to the horizon has a fixed window instead.
    window = int(SCRIPT.decision_window_days.value)
    slow = pack_for("universal", "slow_burn", "act_late").branches[0]
    assert slow.result.failure_day is None and slow.decision_points
    assert all(p.deadline_day == p.open_day + window for p in slow.decision_points)


def test_decisions_of_others_reach_the_actors_to_be_informed():
    pack = pack_for("universal", "fast_run", "support_package")
    branch = pack.branches[0]
    for record in branch.result.decision_log:
        informed = SCRIPT.coordination[record.decision].inform
        for actor in ACTORS:
            got = [
                i for i in branch.injects[actor]
                if i.kind == "decision_taken" and record.decision_name in i.text
            ]
            aware = DECISIONS.information[actor].informed_from in (None, "recovery", "early_intervention", "likely_to_fail")
            if actor in informed and aware:
                assert len(got) == 1 and got[0].day == record.effect_day
                assert got[0].sender == SCRIPT.actors[record.actor] and got[0].figures == ()
            else:
                assert got == []
    assert not [i for i in branch.injects["bank_management"]
                if i.kind == "decision_taken" and "Public statement" in i.text]


# ------------------------------------------------- branches


def test_documents_share_the_part_before_the_branches_diverge(acceptance):
    late, early = acceptance.branches
    for document in acceptance.documents:
        a, b = late.injects[document.actor], early.injects[document.actor]
        n = len(document.shared)
        strip = lambda items: [  # noqa: E731
            replace(i, decision_due=replace(i.decision_due, deadline_day=0, deadline_shared=False))
            if i.decision_due else i
            for i in items
        ]
        assert n >= 2 and strip(a[:n]) == strip(b[:n]) == strip(document.shared)
        assert [title for title, _ in document.branches] == ["Branch A", "Branch B"]
        assert document.branches[0][1] == a[n:] and document.branches[1][1] == b[n:]
        assert n == len(a) or n == len(b) or strip([a[n]]) != strip([b[n]])
    # The two branches part after the first evening: day 1 is shared, day 2 is not.
    bank = next(d for d in acceptance.documents if d.actor == "bank_management")
    assert max(i.day for i in bank.shared) == 1


def test_one_path_gives_documents_without_branches():
    pack = pack_for("universal", "fast_run", "ela_granted")
    assert len(pack.branches) == 1
    assert all(document.branches == () and document.shared for document in pack.documents)
    with pytest.raises(ValueError):
        pack_for("universal", "fast_run", "act_late", "ela_granted", "early_foltf")


def test_player_documents_do_not_give_the_outcome_away(acceptance):
    files = pack_files(acceptance, SCRIPT, DECISIONS)
    for actor in ACTORS:
        text = docx_text(files[f"players/{actor}.docx"])
        for path in DECISIONS.paths.values():
            assert path.name not in text  # branch names stay with the control team
        assert acceptance.scenario_name not in text
        for outcome in ("stabilised", "emergency_funding", "under_stress"):
            assert OUTCOME_LABELS[outcome] not in text
        assert "Branch A" in text and "Branch B" in text and SCRIPT.actors[actor] in text
        assert SCRIPT.draft_notice in text and SCRIPT.disclaimer in text


# ------------------------------------------------- control team pack


def test_decision_points_show_where_each_option_leads(acceptance):
    for branch in acceptance.branches:
        open_decisions = {
            i.decision_due.decision
            for actor in ACTORS for i in branch.injects[actor] if i.decision_due
        }
        assert {p.decision for p in branch.decision_points} == open_decisions
        for decision in open_decisions:
            points = [p for p in branch.decision_points if p.decision == decision]
            assert len(points) == len(DECISIONS.decisions[decision].options)
            (followed,) = [p for p in points if p.taken]
            assert followed.outcome == branch.result.outcome
            assert followed.end_day == branch.result.end_day
    late = {p.code: p for p in acceptance.branches[0].decision_points}
    full = branch_code(DECISIONS, "ela", "grant_full")
    assert late[full].outcome == "stabilised" and not late[full].taken
    assert late[branch_code(DECISIONS, "ela", "not_decided")].outcome == "payments_default"


def test_control_team_pack_has_full_information(acceptance):
    files = pack_files(acceptance, SCRIPT, DECISIONS)
    text = docx_text(files["control_team/control_team_pack.docx"])
    for branch in acceptance.branches:
        assert f"Branch {branch.label}: {branch.name}" in text
        assert OUTCOME_LABELS[branch.result.outcome] in text
        for point in branch.decision_points:
            assert point.code in text
    late = acceptance.branches[0].result.table.set_index("day")
    assert f"{late.loc[1, 'liquidity_buffer']:.2f}" in text  # same-day figures, for everyone
    assert acceptance.scenario_name in text
    for heading in ("Simulation operational timeline (full information)", "Who sees what, and how late",
                    "Expected coordination steps", "Questions for the debrief"):
        assert heading in text
    assert all(question in text for question in SCRIPT.debrief)
    assert "Deposit guarantee scheme" in text and "liquidity exhausted" in text


def test_pack_holds_one_document_per_actor_the_control_team_pack_and_the_data(acceptance):
    files = pack_files(acceptance, SCRIPT, DECISIONS)
    assert sorted(files) == sorted(
        [f"players/{actor}.docx" for actor in ACTORS]
        + ["control_team/control_team_pack.docx"]
        + [f"control_team/data/SIMULATION_{name}.csv" for name in (
            "injects", "decision_points", "decision_log", "resolution_summary",
            "daily_table_branch_A", "daily_table_branch_B")]
    )
    injects = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_injects.csv"]))
    assert set(injects["actor"]) == set(ACTORS) and set(injects["branch"]) == {"A", "B"}
    assert len(injects) == sum(len(b.injects[a]) for b in acceptance.branches for a in ACTORS)
    points = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_decision_points.csv"]))
    assert len(points) == sum(len(b.decision_points) for b in acceptance.branches)
    daily = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_daily_table_branch_B.csv"]))
    assert len(daily) == len(acceptance.branches[1].result.table)
    log = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_decision_log.csv"]))
    assert set(log["branch"]) == {"B"} and "Figures as of day" in log.columns


def test_zip_is_complete_and_reproducible(acceptance):
    first = pack_to_zip(acceptance, SCRIPT, DECISIONS)
    again = pack_to_zip(
        pack_for("universal", "fast_run", "act_late", "early_foltf"), SCRIPT, DECISIONS
    )
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
    assert len(names) == 12 and "control_team/control_team_pack.docx" in names
    with zipfile.ZipFile(io.BytesIO(again)) as archive:
        assert archive.namelist() == names
        csv = archive.read("control_team/data/SIMULATION_injects.csv")
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.read("control_team/data/SIMULATION_injects.csv") == csv


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_a_pack_can_be_built_for_every_bank_and_scenario(bank, scenario):
    pack = pack_for(bank, scenario, "act_late", "support_package")
    files = pack_files(pack, SCRIPT, DECISIONS)
    # No resolution on either branch here, so no resolution summary file.
    assert len(files) == 11 and all(len(content) > 0 for content in files.values())
    for document in pack.documents:
        assert document.shared[0].kind == "opening"


# ------------------------------------------------- CLI and app


def test_cli_exports_the_pack(tmp_path, capsys):
    args = ["--bank", "universal", "--scenario", "fast_run", "--path", "early_foltf",
            "--export-dir", str(tmp_path)]
    assert main(args) == 0
    out = capsys.readouterr().out
    stem = "exercise_pack_universal_fast_run"
    assert (tmp_path / f"{stem}.zip").exists() and f"{stem}.zip" in out
    assert (tmp_path / stem / "players" / "resolution_authority.docx").exists()
    assert (tmp_path / stem / "control_team" / "data" / "SIMULATION_daily_table_branch_B.csv").exists()
    # The same path twice gives a single branch.
    assert main(["--bank", "retail", "--scenario", "slow_burn", "--export-dir", str(tmp_path)]) == 0
    single = tmp_path / "exercise_pack_retail_slow_burn" / "control_team" / "data"
    assert (single / "SIMULATION_daily_table_branch_A.csv").exists()
    assert not (single / "SIMULATION_daily_table_branch_B.csv").exists()
    with pytest.raises(SystemExit):
        main(args + ["--compare-path", "no_such_path"])


def test_app_script_tab_previews_one_actor_and_offers_one_download():
    at = AppTest.from_file(APP_PATH)
    for key, value in dict(
        demo_on=False, view=demo.TAB_SCRIPT, bank="universal", scenario="fast_run",
        decision_path="act_late", script_branch="act_late",
    ).items():
        at.session_state[key] = value
    at.run(timeout=60)
    assert not at.exception
    assert len(at.info) == 1  # both branches are "authorities act late": a single branch
    at.selectbox(key="decision_path").set_value("early_foltf").run(timeout=60)
    assert not at.exception and len(at.info) == 0
    assert len(at.get("download_button")) == 1
    at.selectbox(key="script_actor").set_value("resolution_authority").run(timeout=60)
    assert not at.exception
    text = "\n".join(m.value for m in at.markdown)
    assert "Branch A" in text and "Branch B" in text
    assert "You receive nothing about the bank before this stage" in text
    assert "Liquidity buffer" not in text
    assert "`D7-b` Bail-in" in text
