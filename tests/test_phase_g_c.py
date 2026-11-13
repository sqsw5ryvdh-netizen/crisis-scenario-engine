"""SPEC_V3 phase G, part c: mock bank dossier (G7), session plan (G8), charter, rules of play,
observer sheets and feedback questionnaire (G10), and the complete pack for the showcase."""

import io
import re
import shutil
from pathlib import Path

import pytest
import yaml
from docx import Document
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from app import demo
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import choices_of
from engine.export import (
    FEEDBACK_CHECKLIST,
    HANDBOOK_MAPPING,
    SIMULATION_LABEL,
    SIMULATION_NOTICE,
    pack_files,
)
from engine.run import main
from engine.schema import PLAYERS, ROLES, RULE_CATEGORIES
from engine.script import MODES, build_pack
from engine.setup import (
    DRAFT_MARK,
    END,
    FOLTF_BREAK,
    PAUSE,
    TOOL_BREAK,
    charter_rows,
    observer_sheet,
    rules_of_play,
    session_plan,
    session_plans,
)

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SETUP, SHOWCASE = SCRIPT.setup, SCRIPT.showcase
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


def pack_for(bank=SHOWCASE.bank, scenario=SHOWCASE.scenario,
             paths=(SHOWCASE.compare_path, SHOWCASE.path), **options):
    return build_pack(
        CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], DECISIONS, SCRIPT,
        [(DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key])) for key in paths],
        bank_alias=SETUP.bank_alias, background_key=scenario, **options,
    )


@pytest.fixture(scope="module")
def pack():
    return pack_for()


@pytest.fixture(scope="module")
def files(pack):
    return pack_files(pack, SCRIPT, DECISIONS)


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def tables(content: bytes) -> list[list[list[str]]]:
    return [[[cell.text for cell in row.cells] for row in table.rows]
            for table in Document(io.BytesIO(content)).tables]


# ------------------------------------------------- acceptance: the complete pack


def test_the_showcase_pack_is_complete(files):
    """Dossier, rules of play, charter draft, session plan, main events list, role cards,
    players' documents, observer sheets, questionnaire."""
    expected = [
        "players/mock_bank_dossier.docx",
        "players/rules_of_play.docx",
        "players/feedback_questionnaire.docx",
        "control_team/simex_charter_draft.docx",
        "control_team/session_plan.docx",
        "control_team/main_events_list.docx",
        "control_team/control_team_pack.docx",
        "control_team/feedback_checklist.docx",
        "control_team/handbook_mapping.docx",
        *(f"control_team/role_cards/{role}.docx" for role in ROLES),
        *(f"control_team/inject_sheets/{player}.docx" for player in PLAYERS),
        *(f"observers/observer_sheet_{player}.docx" for player in PLAYERS),
    ]
    assert set(expected) <= set(files)
    assert {name.split("/")[0] for name in files} == {"players", "observers", "control_team"}
    assert all(len(content) > 0 for content in files.values())


@pytest.mark.parametrize("mode", MODES)
def test_no_document_without_the_simulation_label(mode):
    for name, content in pack_files(pack_for(mode=mode), SCRIPT, DECISIONS).items():
        if not name.endswith(".docx"):
            assert Path(name).name.startswith(f"{SIMULATION_LABEL}_"), name
            continue
        document = Document(io.BytesIO(content))
        assert document.core_properties.title.startswith(SIMULATION_LABEL), name
        for section in document.sections:
            assert SIMULATION_NOTICE in section.header.paragraphs[0].text, name
            assert SIMULATION_NOTICE in section.footer.paragraphs[0].text, name
        assert SIMULATION_NOTICE in [p.text for p in document.paragraphs[:3]], name


def test_every_output_is_mapped_to_the_handbook(files):
    patterns = [row[0] for row in HANDBOOK_MAPPING]
    for name in files:
        if not name.endswith(".docx"):
            continue
        generic = re.sub(r"(inject_sheets/|role_cards/)[a-z_]+", r"\1<x>", name)
        generic = re.sub(r"observer_sheet_[a-z_]+", "observer_sheet_<x>", generic)
        candidates = [re.sub(r"<[a-z]+>", "<x>", pattern) for pattern in patterns]
        assert generic in candidates or name == "control_team/handbook_mapping.docx", name
    for _, what, reference in HANDBOOK_MAPPING:
        assert what and ("par." in reference or "Annex" in reference)
    text = docx_text(files["control_team/handbook_mapping.docx"])
    assert all(row[2] in text for row in HANDBOOK_MAPPING)


# ------------------------------------------------- G7: mock bank dossier


def test_dossier_describes_the_bank_before_the_exercise(pack):
    dossier = pack.dossier
    bank = CONFIG.banks[SHOWCASE.bank]
    assert dossier.bank_name == SETUP.bank_alias and dossier.country == SETUP.country_alias
    assert dossier.business_model == bank.description
    assets, liabilities = dict(dossier.assets), dict(dossier.liabilities)
    assert sum(assets.values()) == pytest.approx(sum(liabilities.values()))
    assert assets["Cash and central bank reserves"] == pytest.approx(bank.assets.cash_reserves)
    assert liabilities["CET1 capital"] == pytest.approx(bank.capital.cet1)
    stack = dict(dossier.stack)
    assert list(stack) == ["CET1 (shareholders)", "AT1 and Tier 2", "Senior non-preferred debt",
                           "Senior preferred debt", "Uncovered deposits",
                           "Covered deposits (protected)"]
    assert stack["AT1 and Tier 2"] == pytest.approx(bank.resolution.at1_t2)
    deposits = sum(v for k, v in liabilities.items() if "eposits" in k)
    assert stack["Uncovered deposits"] + stack["Covered deposits (protected)"] == pytest.approx(deposits)
    indicators = dict(dossier.indicators)
    assert indicators["CET1 ratio"] == f"{100 * bank.capital.cet1 / bank.capital.rwa:.1f}%"
    assert indicators["CET1 requirement"] == f"{100 * bank.capital.capital_requirement_ratio:.1f}%"
    assert indicators[f"Available MREL ({bank.unit})"] == f"{bank.mrel_available:.2f}"
    assert dossier.background == SETUP.background[SHOWCASE.scenario].format(
        bank_name=SETUP.bank_alias, country=SETUP.country_alias)


def test_dossier_document_has_the_five_parts_and_no_real_or_archetype_name(files):
    text = docx_text(files["players/mock_bank_dossier.docx"])
    for heading in ("Business model", "Economic background", "Simplified balance sheet",
                    "Liability stack", "Prudential indicators"):
        assert heading in text
    assert SETUP.bank_alias in text and SETUP.country_alias in text
    # The neutral name is used in every document of the pack.
    archetype = CONFIG.banks[SHOWCASE.bank].name
    for name, content in files.items():
        if name.endswith(".docx"):
            assert archetype not in docx_text(content), name


def test_what_players_get_in_advance_gives_nothing_away(pack, files):
    """Injects are not disclosed to players ahead of the exercise (EBA Handbook, par. 177)."""
    secrets = {pack.scenario_name}
    secrets |= {event.title for event in pack.events if event.kind in ("ambient", "challenge")}
    secrets |= {event.detail for b in pack.branches for event in b.result.events if event.kind == "news"}
    secrets |= {branch.name for branch in pack.branches}
    last = pack.branches[0].result.table.iloc[-1]
    secrets.add(f"{last['cum_deposit_loss_pct']:.1f}%")
    for name in ("mock_bank_dossier", "rules_of_play", "feedback_questionnaire"):
        text = docx_text(files[f"players/{name}.docx"])
        for secret in secrets:
            assert secret not in text, (name, secret)
        assert "Branch A" not in text


def test_background_falls_back_to_the_default_and_config_is_checked(tmp_path):
    other = pack_for(scenario="fast_run", paths=("act_late",))
    assert other.dossier.background.startswith("The economy of Country A")
    plain = build_pack(
        CONFIG.banks["retail"], CONFIG.model, CONFIG.scenarios["baseline"], DECISIONS, SCRIPT,
        [("none", ())], background_key="baseline")
    assert plain.dossier.background == SETUP.background["default"].format(
        bank_name=CONFIG.banks["retail"].name, country=SETUP.country_alias)
    for change in ("no_default", "figure", "unknown_scenario"):
        folder = tmp_path / change
        shutil.copytree(DEFAULT_CONFIG_DIR, folder)
        data = yaml.safe_load((folder / "script.yaml").read_text(encoding="utf-8"))
        if change == "no_default":
            del data["setup"]["background"]["default"]
        elif change == "figure":
            data["setup"]["rules"]["play"].append("Decide within 30 minutes.")
        else:
            data["setup"]["background"]["meteor"] = "A meteor."
        (folder / "script.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(folder)


# ------------------------------------------------- G8: session plan


def test_default_plan_breaks_at_foltf_and_at_the_resolution_decision(pack):
    """EBA Handbook, par. 141: breaks coincide with the FoLtF decision and the resolution
    decision."""
    for branch in pack.branches:
        plan = session_plan(pack, DECISIONS, branch.label, 3, 3)
        assert [s.ends_with for s in plan] == [FOLTF_BREAK, TOOL_BREAK, END]
        log = {r.decision: r.decided_day for r in branch.result.decision_log}
        assert plan[0].last_day == log["declare_foltf"]
        assert plan[1].last_day == log["resolution_tool"]
        assert plan[-1].last_day == branch.result.end_day


@pytest.mark.parametrize("sessions", [1, 2, 3, 4, 5, 8, 14, 40])
def test_plan_covers_every_simulated_day_once_and_every_inject(pack, sessions):
    for branch in pack.branches:
        plan = session_plan(pack, DECISIONS, branch.label, sessions, 2)
        days = [day for s in plan for day in range(s.first_day, s.last_day + 1)]
        assert days == list(range(0, branch.result.end_day + 1))
        assert len(plan) == min(sessions, len(days))
        assert [s.number for s in plan] == list(range(1, len(plan) + 1))
        on_branch = [e for e in pack.events if e.section in ("", branch.label)]
        assert sum(s.injects for s in plan) == len(on_branch)
        assert plan[-1].ends_with == END and all(s.ends_with != END for s in plan[:-1])
        for s in plan:
            assert s.minutes_per_day == pytest.approx(120 / (s.last_day - s.first_day + 1))
        if sessions >= 3:
            assert {FOLTF_BREAK, TOOL_BREAK} <= {s.ends_with for s in plan}


def test_plan_with_few_sessions_or_no_foltf(pack):
    assert [s.ends_with for s in session_plan(pack, DECISIONS, "A", 1, 6)] == [END]
    two = session_plan(pack, DECISIONS, "A", 2, 4)
    assert len(two) == 2 and two[0].ends_with in (FOLTF_BREAK, TOOL_BREAK)
    # A path on which nothing is declared: planned pauses only.
    quiet = pack_for(scenario="slow_burn", paths=("act_late",))
    assert quiet.branches[0].result.foltf_day is None
    plan = session_plan(quiet, DECISIONS, "A", 3, 3)
    assert [s.ends_with for s in plan] == [PAUSE, PAUSE, END]
    for bad in ((0, 3), (3, 0)):
        with pytest.raises(ValueError):
            session_plan(pack, DECISIONS, "A", *bad)


def test_session_plan_document(pack, files):
    text = docx_text(files["control_team/session_plan.docx"])
    sessions, hours = int(SETUP.sessions.value), SETUP.session_hours.value
    assert f"Input: {sessions} sessions of {hours:g} hours" in text
    assert FOLTF_BREAK in text and TOOL_BREAK in text and "Real time per simulated day" in text
    # The two branches cut the same days: one plan, with the injects of each.
    plans = session_plans(pack, DECISIONS, sessions, hours)
    assert [heading for heading, _ in plans] == ["All branches"]
    assert "(branch A)" in plans[0][1][0].injects_text
    other = docx_text(pack_files(pack, SCRIPT, DECISIONS, 1, 6)["control_team/session_plan.docx"])
    assert "Input: 1 session of 6 hours" in other and "too few sessions" in other


# ------------------------------------------------- G10: charter, rules, observers, feedback


def test_charter_follows_annex_1_and_marks_what_is_pre_filled(pack, files):
    rows = charter_rows(pack, SCRIPT)
    assert [row[0] for row in rows] == [
        "Project Manager", "Sponsor(s)", "Main Goal", "Tentative Deadline",
        "Expected Key Outcome(s)", "Elements in Scope (Must-Have)",
        "Elements in Scope (Nice to Have)", "Elements Not in Scope (Constraints)",
        "Type of SimEx that best fits the goals", "References"]
    concrete = {row[0]: row[2] for row in rows}
    for blank in ("Project Manager", "Sponsor(s)", "Tentative Deadline"):
        assert concrete[blank] == ""
    for element, text in concrete.items():
        assert text == "" or text.startswith(f"[{DRAFT_MARK}] "), element
    assert SETUP.bank_alias in concrete["Main Goal"] and pack.taxonomy in concrete["Main Goal"]
    assert "Resolution tool" in concrete["Expected Key Outcome(s)"]
    assert "dynamic" in concrete["Type of SimEx that best fits the goals"]
    table = tables(files["control_team/simex_charter_draft.docx"])[0]
    assert table[0] == ["Element", "Description", "Concrete elements"] and len(table) == 11
    assert table[1][1] == "Person responsible for the execution of the project"


def test_rules_of_play_have_the_three_categories(pack, files):
    rules = rules_of_play(pack, SCRIPT)
    assert [category for category, _ in rules] == list(RULE_CATEGORIES.values())
    flat = [rule for _, items in rules for rule in items]
    assert any("not used for any kind of professional evaluation" in rule for rule in flat)
    assert any("branch code" in rule for rule in flat)
    played = next(rule for rule in flat if rule.startswith("The control team plays"))
    assert all(card.name.lower() in played for card in pack.roles)
    assert any(pack.start_weekday in rule for rule in flat)
    text = docx_text(files["players/rules_of_play.docx"])
    assert all(rule in text for rule in flat) and "To be completed by the design team" in text
    linear = [rule for _, items in rules_of_play(pack_for(mode="linear"), SCRIPT) for rule in items]
    assert not any("branch code" in rule for rule in linear)
    assert any("linear" in rule for rule in linear)


@pytest.mark.parametrize("player", PLAYERS)
def test_observer_sheet_lists_what_to_track_for_its_team(pack, files, player):
    sheet = observer_sheet(pack, SCRIPT, player)
    document = next(d for d in pack.documents if d.actor == player)
    due = [i.decision_due.name for i in document.shared if i.decision_due]
    due += [i.decision_due.name for _, injects in document.branches for i in injects if i.decision_due]
    assert [row[2] for row in sheet.decisions] == due
    name = SCRIPT.actors[player].lower()
    fallbacks = [e for e in pack.events if e.fallback and e.delivered_by.endswith(f"of the {name}")]
    assert len(sheet.relays) == len(fallbacks)
    assert sheet.tracks == tuple(SETUP.observer_tracks)
    text = docx_text(files[f"observers/observer_sheet_{player}.docx"])
    assert "Real time taken" in text or not due
    assert all(track in text for track in SETUP.observer_tracks)
    assert "Do not show it to the players" in text


def test_observers_follow_the_relays_only_in_a_dynamic_exercise(pack):
    assert observer_sheet(pack, SCRIPT, "competent_authority").relays
    linear = pack_for(mode="linear")
    assert not any(observer_sheet(linear, SCRIPT, player).relays for player in PLAYERS)


def test_questionnaire_quotes_annex_7_and_the_checklist_is_blank(files):
    assert len(SETUP.questionnaire) == 19
    assert SETUP.questionnaire[0] == "Did the SimEx meet its aims and objectives? [1–5]"
    assert SETUP.questionnaire[-1] == (
        "If you could change three things for the next simulation, which would these be?")
    text = docx_text(files["players/feedback_questionnaire.docx"])
    assert all(question in text for question in SETUP.questionnaire)
    assert "Annex 7" in text and SETUP.questionnaire_intro in text
    checklist = tables(files["control_team/feedback_checklist.docx"])
    assert len(checklist) == len(FEEDBACK_CHECKLIST) == 4
    for table, (_, header, rows) in zip(checklist, FEEDBACK_CHECKLIST):
        assert table[0] == header and len(table) == len(rows) + 1
        assert all(row[-1] == "" for row in table[1:])  # nothing is pre-filled
    assert "Annex 8" in docx_text(files["control_team/feedback_checklist.docx"])


# ------------------------------------------------- CLI and app


def test_cli_exports_the_complete_pack_with_session_inputs(tmp_path, capsys):
    assert main(["--showcase", "--sessions", "5", "--session-hours", "2",
                 "--export-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    stem = tmp_path / f"exercise_pack_{SHOWCASE.bank}_{SHOWCASE.scenario}"
    for name in ("players/mock_bank_dossier.docx", "observers/observer_sheet_central_bank.docx",
                 "control_team/simex_charter_draft.docx", "control_team/session_plan.docx"):
        assert (stem / name).exists(), name
    plan = docx_text((stem / "control_team" / "session_plan.docx").read_bytes())
    assert "Input: 5 sessions of 2 hours" in plan
    dossier = docx_text((stem / "players" / "mock_bank_dossier.docx").read_bytes())
    assert SETUP.bank_alias in dossier
    with pytest.raises(SystemExit):
        main(["--showcase", "--sessions", "0", "--export-dir", str(tmp_path)])


def test_app_takes_the_session_inputs():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_SCRIPT
    at.run(timeout=90)
    assert not at.exception
    assert at.number_input(key="script_sessions").value == int(SETUP.sessions.value)
    assert any(e.label == "Session plan" for e in at.expander)
    at.number_input(key="script_sessions").set_value(5).run(timeout=90)
    assert not at.exception
    assert any(SETUP.bank_alias in c.value for c in at.caption)
