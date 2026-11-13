"""SPEC_V3 phase G, part b: main events list (G4), challenge injects and friendly or hostile
assumptions (G5), role cards of the stakeholders the control team plays (G6)."""

import io
import re
from pathlib import Path

import pandas as pd
import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from app import demo
from engine.config import load_config
from engine.decisions import choices_of
from engine.export import inject_sheets_docx, main_events_rows, pack_files, role_card_docx
from engine.model import simulate
from engine.resolution import LEVELS, hostile_liquidity_need, indicative_bid
from engine.run import main
from engine.schema import ACTORS, DECISION_STAGES, PLAYERS, PURPOSES, ROLES, ROLE_STAGES
from engine.script import (
    MODES,
    active_challenges,
    build_pack,
    scenario_with_challenges,
    sheet_id,
)

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
BANK, SCENARIO = CONFIG.banks[SHOWCASE.bank], CONFIG.scenarios[SHOWCASE.scenario]
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
FIGURE_TOKEN = re.compile(r"\d+\.\d+%?|\d+%")
QUIET = [key for key, item in SCRIPT.challenges.items() if item.effect != "panic_shock"]


def showcase_pack(bank=BANK, scenario=SCENARIO, **options):
    paths = [
        (DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key]))
        for key in (SHOWCASE.compare_path, SHOWCASE.path)
    ]
    return build_pack(bank, CONFIG.model, scenario, DECISIONS, SCRIPT, paths, **options)


@pytest.fixture(scope="module")
def dynamic():
    return showcase_pack()


@pytest.fixture(scope="module")
def linear():
    return showcase_pack(mode="linear")


@pytest.fixture(scope="module")
def hostile():
    return showcase_pack(hostile=True)


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def docx_text_in_order(content: bytes) -> str:
    """Paragraphs and tables in the order they are printed."""
    body = Document(io.BytesIO(content)).element.body
    return "\n".join(node.text or "" for node in body.iter() if node.tag.endswith("}t"))


def page_breaks(content: bytes) -> int:
    document = Document(io.BytesIO(content))
    return sum(run._r.xml.count('w:type="page"') for p in document.paragraphs for run in p.runs)


def player_injects(pack):
    """(player, section, position, inject) for every sheet of the pack."""
    for document in pack.documents:
        if document.actor not in PLAYERS:
            continue
        sections = [("", document.shared)] + [(t.split()[1], i) for t, i in document.branches]
        for section, injects in sections:
            for position, inject in enumerate(injects):
                yield document.actor, section, position, inject


# ------------------------------------------------- players and simulated stakeholders


def test_the_four_authorities_are_the_players_and_the_bank_is_simulated():
    assert PLAYERS == ("competent_authority", "resolution_authority", "central_bank",
                       "deposit_guarantee_scheme")
    assert "bank_management" in ROLES and "bank_management" not in PLAYERS
    assert set(SCRIPT.roles) == set(ROLES) == {
        "bank_management", "independent_valuer", "media", "finance_minister", "potential_buyers"}


def test_pack_layout_inject_sheets_main_events_list_and_role_cards(dynamic):
    files = pack_files(dynamic, SCRIPT, DECISIONS)
    for player in PLAYERS:
        assert f"control_team/inject_sheets/{player}.docx" in files
    assert "control_team/inject_sheets/bank_management.docx" not in files
    for role in ROLES:
        assert f"control_team/role_cards/{role}.docx" in files
    assert "control_team/main_events_list.docx" in files
    assert "control_team/data/SIMULATION_main_events_list.csv" in files
    assert not any(name.startswith("players/") for name in files)


# ------------------------------------------------- G4: main events list


@pytest.mark.parametrize("mode", MODES)
def test_every_inject_to_a_player_is_on_the_main_events_list_once(mode):
    pack = showcase_pack(mode=mode)
    sheets = [sheet for event in pack.events for sheet in event.sheets]
    expected = [sheet_id(actor, section, position)
                for actor, section, position, _ in player_injects(pack)]
    assert sorted(sheets) == sorted(expected) and len(set(sheets)) == len(sheets)
    assert [event.number for event in pack.events] == list(range(1, len(pack.events) + 1))
    for event in pack.events:
        assert event.purpose in PURPOSES
        assert event.trigger and event.expected and event.delivered_by
        assert event.trigger.startswith(("Time:", "Milestone:", "Fallback:"))
        assert set(event.recipients) <= set(PLAYERS) and len(event.recipients) == len(event.sheets)
    # Within a section the list is in time order.
    for section in {event.section for event in pack.events}:
        moments = [(e.day, e.time) for e in pack.events if e.section == section]
        assert moments == sorted(moments)
    # Nothing addressed to bank management is on the list.
    assert not any(sheet.startswith("BM-") for sheet in sheets)


def test_each_purpose_is_used_and_matches_the_kind_of_inject(dynamic):
    assert {event.purpose for event in dynamic.events} == set(PURPOSES)
    for event in dynamic.events:
        if event.kind in ("ambient", "challenge"):
            assert event.purpose == "challenge"
        if event.kind in ("resolution", "end_open") or event.fallback:
            assert event.purpose == "control"
        if event.kind in ("opening", "decision_due", "resolution_step"):
            assert event.purpose == "advance"


def test_injects_are_delivered_by_the_control_team_or_a_role_it_plays(dynamic):
    names = {SCRIPT.roles[role].name for role in ROLES}
    for event in dynamic.events:
        assert event.delivered_by in names or event.delivered_by.startswith("Control team")
    by = {event.title: event.delivered_by for event in dynamic.events}
    assert by["Provisional valuation"] == "Independent valuer"
    assert by["News"] == by["Press enquiry"] == "Media"
    assert by["Call from the finance ministry"] == "Finance minister"
    assert by["A potential buyer shows interest"] == "Potential buyers"
    # The bank tells the competent authority of a stage change.
    stage = next(e for e in dynamic.events if e.kind in DECISION_STAGES and not e.fallback)
    assert stage.recipients == ("competent_authority",) and stage.delivered_by == "Bank management"
    assert stage.trigger.startswith("Milestone:")


def test_what_players_should_pass_on_is_an_expected_response_with_a_fallback(dynamic):
    """Dynamic exercise: the control team does not disclose what players should tell each
    other (EBA Handbook, par. 191). It keeps a fallback inject."""
    fallbacks = [event for event in dynamic.events if event.fallback]
    assert fallbacks
    for event in fallbacks:
        assert event.purpose == "control" and event.trigger.startswith("Fallback: only if the ")
        assert "in place of the" in event.delivered_by
    # Every inject whose sender is another player is a fallback, and only those.
    player_names = {SCRIPT.actors[player]: player for player in PLAYERS}
    for actor, _, _, inject in player_injects(dynamic):
        from_other_player = (
            inject.sender in player_names and player_names[inject.sender] != actor
            and inject.kind != "resolution"
        )
        assert inject.fallback == from_other_player, (actor, inject.title, inject.sender)
    # The competent authority's notification says whom to pass it on to, and exactly those
    # players have a fallback sheet for that day.
    for inject in dynamic.branches[0].injects["competent_authority"]:
        if inject.kind not in DECISION_STAGES:
            continue
        told = [
            SCRIPT.actors[other] for other in PLAYERS
            if any(i.kind == inject.kind and i.day == inject.day and i.fallback
                   for i in dynamic.branches[0].injects[other])
        ]
        assert told and all(name in inject.expected for name in told)
        silent = set(SCRIPT.actors[p] for p in PLAYERS[1:]) - set(told)
        assert not any(name in inject.expected for name in silent)


def test_linear_exercise_has_no_fallbacks_and_a_course_correction_per_decision(linear):
    """EBA Handbook, par. 126-127: in a linear exercise the control team corrects course."""
    assert not any(event.fallback for event in linear.events)
    result = linear.branches[0].result
    taken = {record.decision_name: record.option_name for record in result.decision_log}
    corrections = [e for e in linear.events if e.kind == "course_correction"]
    due = [
        inject.decision_due for actor in PLAYERS
        for inject in linear.branches[0].injects[actor] if inject.decision_due
    ]
    assert corrections and len(corrections) <= len(due)
    for event in corrections:
        assert event.purpose == "control" and "differs from the fixed path" in event.trigger
        decision = next(d for d in due if f'"{d.name}"' in event.text)
        definition = DECISIONS.decisions[decision.decision]
        option = taken.get(decision.name, definition.options[definition.default_option].name)
        assert event.text.rstrip(".").endswith(option + ". Your own decision is recorded for the debrief")
    # A dynamic exercise has none.
    assert not any(e.kind == "course_correction" for e in showcase_pack().events)


def test_main_events_list_document_and_data(dynamic):
    files = pack_files(dynamic, SCRIPT, DECISIONS)
    text = docx_text(files["control_team/main_events_list.docx"])
    for column in ("Trigger", "Delivered by", "Purpose", "Expected response", "Sheets"):
        assert column in text
    for word in ("Advance", "Challenge", "Control (fallback)", "Branch A", "Branch B"):
        assert word in text
    table = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_main_events_list.csv"]))
    assert len(table) == len(dynamic.events) == len(main_events_rows(dynamic, SCRIPT))
    assert table["Expected response"].notna().all() and table["Trigger"].notna().all()
    injects = pd.read_csv(io.BytesIO(files["control_team/data/SIMULATION_injects.csv"]))
    assert {"purpose", "trigger", "delivered_by", "expected_response", "fallback"} <= set(injects)


# ------------------------------------------------- inject sheets


@pytest.mark.parametrize("player", PLAYERS)
def test_inject_sheets_are_one_per_page_and_hold_no_control_team_notes(dynamic, player):
    content = inject_sheets_docx(dynamic, SCRIPT, player)
    document = next(d for d in dynamic.documents if d.actor == player)
    count = len(document.shared) + sum(len(injects) for _, injects in document.branches)
    empty = sum(1 for _, injects in document.branches if not injects)
    # One page for the control team, one briefing sheet, one per inject, one glossary.
    assert page_breaks(content) == 1 + count + empty + 1
    text = docx_text(content)
    for _, section, position, _ in (x for x in player_injects(dynamic) if x[0] == player):
        assert f"Sheet {sheet_id(player, section, position)}" in text
    handed_out = docx_text_in_order(content).split("Briefing sheet: what you see")[1]
    for actor, _, _, inject in player_injects(dynamic):
        if actor == player:
            assert inject.expected not in handed_out and inject.trigger not in handed_out
    assert "fallback" not in handed_out.lower() and "main events list" not in handed_out.lower()


# ------------------------------------------------- G5: challenge injects and assumptions


def test_default_challenges_and_the_hostile_ones():
    assert active_challenges(SCRIPT) == ("buyer_interest", "second_valuation")
    assert active_challenges(SCRIPT, hostile=True) == (
        "buyer_interest", "second_valuation", "buyers_withdraw", "creditor_challenge")
    assert active_challenges(SCRIPT, ["rating_downgrade"]) == ("rating_downgrade",)
    assert active_challenges(SCRIPT, []) == ()
    with pytest.raises(ValueError):
        active_challenges(SCRIPT, ["meteor"])
    for name in ("hostile_liquidity_uplift", "buyer_indicative_bid_share",
                 "rating_downgrade_panic_shock"):
        assert getattr(CONFIG.model, name).is_assumption


def test_second_valuation_is_worse_and_comes_after_the_first(dynamic):
    """EBA Handbook, par. 129: a deteriorated valuation after the initial one."""
    for branch in dynamic.branches:
        injects = branch.injects["resolution_authority"]
        first = next(i for i in injects if i.title == "Provisional valuation")
        second = next(i for i in injects if i.title == SCRIPT.challenges["second_valuation"].title)
        assert (second.day, second.time) > (first.day, first.time)
        assert second.kind == "challenge" and second.purpose == "challenge"
        assert any("4% (central)" in f.text for f in first.resolution)
        assert any("8% (high)" in f.text for f in second.resolution)
        need = branch.resolution["high"].recapitalisation_need
        assert branch.resolution["high"].writedown > branch.resolution["central"].writedown
        assert any(f.label.startswith("Need:") and f"{need:.2f}" in f.text for f in second.resolution)
        # Only the resolution authority gets it: sharing it is the players' task.
        for other in ("competent_authority", "central_bank", "deposit_guarantee_scheme"):
            assert not any(i.title == second.title for i in branch.injects[other])


def test_no_second_valuation_when_the_first_is_already_the_high_one():
    pack = showcase_pack(writedown_level="high")
    assert "second_valuation" in pack.challenges
    assert not any(e.title == SCRIPT.challenges["second_valuation"].title for e in pack.events)
    text = docx_text(pack_files(pack, SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "the updated valuation is not played" in text
    low = showcase_pack(writedown_level="low")
    second = next(i for i in low.branches[0].injects["resolution_authority"]
                  if i.title == SCRIPT.challenges["second_valuation"].title)
    assert any("4% (central)" in f.text for f in second.resolution)


def test_interested_buyer_names_an_assumed_price(dynamic):
    """EBA Handbook, par. 192."""
    branch = dynamic.branches[0]
    inject = next(i for i in branch.injects["resolution_authority"]
                  if i.title == SCRIPT.challenges["buyer_interest"].title)
    summary = branch.resolution["central"]
    bid = indicative_bid(summary, CONFIG.model)
    share = CONFIG.model.buyer_indicative_bid_share.value
    assert bid == pytest.approx(share * summary.requirement * summary.rwa_after) and bid > 0
    assert [f.value for f in inject.resolution] == [pytest.approx(bid)]
    assert inject.day == branch.result.foltf_day and inject.delivered_by == "Potential buyers"


def test_hostile_assumptions_add_injects_and_raise_the_liquidity_need(dynamic, hostile):
    """EBA Handbook, par. 123: creditors challenge, buyers quit, the market reacts badly."""
    titles = {event.title for event in hostile.events}
    friendly_titles = {event.title for event in dynamic.events}
    for key in ("buyers_withdraw", "creditor_challenge"):
        assert SCRIPT.challenges[key].title in titles
        assert SCRIPT.challenges[key].title not in friendly_titles
    label = "Liquidity need with a negative market reaction"
    for pack, shown in ((dynamic, False), (hostile, True)):
        branch = pack.branches[0]
        inject = next(i for i in branch.injects["central_bank"]
                      if i.title == SCRIPT.resolution["reopening_liquidity"].title)
        assert any(f.label.startswith(label) for f in inject.resolution) == shown
    summary = hostile.branches[0].resolution["central"]
    raised = hostile_liquidity_need(summary, CONFIG.model)
    assert raised == pytest.approx(
        summary.liquidity_need * (1 + CONFIG.model.hostile_liquidity_uplift.value))
    assert any(f"{raised:.2f}" in f.text for i in hostile.branches[0].injects["central_bank"]
               for f in i.resolution)
    text = docx_text(pack_files(hostile, SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "Assumptions: hostile." in text
    friendly = docx_text(pack_files(dynamic, SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "Assumptions: friendly." in friendly


def test_challenges_without_a_news_shock_change_no_figure_of_the_run(dynamic, hostile):
    bare = showcase_pack(challenges=[])
    for pack in (dynamic, hostile, showcase_pack(challenges=QUIET)):
        for branch, reference in zip(pack.branches, bare.branches):
            pd.testing.assert_frame_equal(branch.result.table, reference.result.table)
            assert branch.result.outcome == reference.result.outcome
    assert scenario_with_challenges(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, QUIET) is SCENARIO
    assert not any(e.kind == "challenge" for e in bare.events)


def test_rating_downgrade_is_off_by_default_and_adds_a_news_shock():
    item = SCRIPT.challenges["rating_downgrade"]
    assert not item.default and not item.hostile and item.effect == "panic_shock"
    shocked = scenario_with_challenges(
        BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, ["rating_downgrade"])
    extra = [e for e in shocked.news_events if e not in SCENARIO.news_events]
    base = simulate(BANK, CONFIG.model, SCENARIO, DECISIONS, ())
    anchor = next(e.day for e in base.events if e.kind == item.anchor)
    assert len(extra) == 1 and extra[0].day == anchor + item.offset_days
    assert extra[0].panic_shock == CONFIG.model.rating_downgrade_panic_shock.value
    # Adding it twice changes nothing.
    again = scenario_with_challenges(
        BANK, CONFIG.model, shocked, DECISIONS, SCRIPT, ["rating_downgrade"])
    assert again.news_events == shocked.news_events
    # It makes the run worse, and the balance sheet still adds up with no negative balance.
    worse = simulate(BANK, CONFIG.model, shocked, DECISIONS, ())
    assert worse.scorecard.deposits_lost_pct > base.scorecard.deposits_lost_pct
    table = worse.table
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < 1e-6
    numbers = table.select_dtypes("number").drop(columns=["cet1_ratio_projected"], errors="ignore")
    assert (numbers.min() > -1e-9).all()
    # In the pack it is a public news inject with the purpose "challenge".
    pack = showcase_pack(challenges=["rating_downgrade"])
    news = [e for e in pack.events if e.kind == "news" and e.purpose == "challenge"]
    assert len(news) == 1 and news[0].day == extra[0].day
    assert set(news[0].recipients) == set(PLAYERS) and news[0].delivered_by == "Media"
    assert news[0].expected == item.expected


def test_higher_severity_never_defaults_later_with_the_rating_downgrade():
    params = CONFIG.model.model_copy(
        update={"weekend_rules_on": CONFIG.model.weekend_rules_on.model_copy(update={"value": 0})}
    )
    previous = None
    for severity in (0.5, 1.0, 1.5, 2.0, 3.0):
        scenario = CONFIG.scenarios["fast_run"].model_copy(update={
            "severity": CONFIG.scenarios["fast_run"].severity.model_copy(
                update={"value": severity, "low": None, "high": None})})
        shocked = scenario_with_challenges(
            CONFIG.banks["universal"], params, scenario, DECISIONS, SCRIPT, ["rating_downgrade"])
        day = simulate(CONFIG.banks["universal"], params, shocked, DECISIONS, ()).failure_day
        day = float("inf") if day is None else day
        assert previous is None or day <= previous
        previous = day


# ------------------------------------------------- G6: role cards


def test_one_role_card_per_simulated_stakeholder(dynamic):
    assert [card.role for card in dynamic.roles] == list(ROLES)
    for card in dynamic.roles:
        text = SCRIPT.roles[card.role]
        assert card.knows and card.wants and card.lines
        assert [moment for moment, _, _ in card.lines] == [
            ROLE_STAGES[key] for key in ROLE_STAGES if key in text.lines]
        for _, when, line in card.lines:
            assert when and line
        assert all(event.delivered_by == card.name for event in card.delivers)
        for line in [*text.knows, *text.wants, *text.lines.values()]:
            assert not any(character.isdigit() for character in line)
    delivered = sum(len(card.delivers) for card in dynamic.roles)
    assert delivered == sum(1 for e in dynamic.events if not e.delivered_by.startswith("Control team"))


def test_role_cards_carry_the_figures_the_role_holds(dynamic):
    cards = {card.role: card for card in dynamic.roles}
    branch = dynamic.branches[0]
    table = branch.result.table.set_index("day")
    # Bank management: its own same-day figures at each stage.
    bank = dict(cards["bank_management"].figures)
    recovery_day = next(e.day for e in branch.result.events if e.kind == "recovery")
    heading = next(h for h in bank if f"day {recovery_day} " in h and "recovery" in h)
    assert dict(bank[heading])["Liquidity buffer"].startswith(
        f"{table.loc[recovery_day, 'liquidity_buffer']:.2f}")
    assert any(h.startswith("Your decisions, as scripted") for h in bank)
    # Valuer: the three levels, and which one it delivers first and second.
    valuer = dict(next(rows for h, rows in cards["independent_valuer"].figures))
    assert valuer["Provisional valuation uses"] == "central"
    assert valuer["Updated valuation uses"] == "high"
    writedowns = " | ".join(f"{branch.resolution[level].writedown:.2f}" for level in LEVELS)
    assert writedowns in valuer.values()
    # Buyers: the price in their inject.
    price = dict(cards["potential_buyers"].figures[0][1])
    bid = indicative_bid(branch.resolution["central"], CONFIG.model)
    assert f"{bid:.2f}" in price.values()
    # Media: the public news only. The finance minister holds no figure.
    assert [h for h, _ in cards["media"].figures] == ["The news you report (All branches)"]
    assert cards["finance_minister"].figures == ()


@pytest.mark.parametrize("role", ROLES)
def test_role_card_documents(dynamic, role):
    text = docx_text(role_card_docx(dynamic, SCRIPT, role))
    for heading in ("What you know", "What you want", "What you say, and when",
                    "Figures you hold", "Injects you deliver", "Played by"):
        assert heading in text
    assert SCRIPT.roles[role].name in text
    if role in ("media", "finance_minister"):
        # They hold no figure of the bank: none is printed on their card.
        assert not FIGURE_TOKEN.findall(text.split("Glossary")[0])
    if role == "bank_management":
        assert "What you see, day by day" in text and "Outflow 09:00-12:00" in text


def test_control_team_pack_lists_the_roles_and_the_challenges(dynamic):
    text = docx_text(pack_files(dynamic, SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "Simulated stakeholders" in text and "Challenge injects and assumptions" in text
    for role in ROLES:
        assert SCRIPT.roles[role].name in text
    for item in SCRIPT.challenges.values():
        assert item.title in text
    assert "The players are the four authorities" in text


def test_bank_management_still_sees_only_its_own_information(dynamic):
    """It moved to a role card, but what it is shown is still worked out the same way."""
    for branch in dynamic.branches:
        assert set(branch.injects) == set(ACTORS)
        assert any(f.block for i in branch.injects["bank_management"] for f in i.figures)


# ------------------------------------------------- CLI and app


def test_cli_takes_hostile_assumptions_and_challenge_injects(tmp_path, capsys):
    assert main(["--showcase", "--hostile", "--export-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Liquidity need with a negative market reaction (hostile assumption)" in out
    stem = tmp_path / f"exercise_pack_{SHOWCASE.bank}_{SHOWCASE.scenario}" / "control_team"
    assert (stem / "main_events_list.docx").exists()
    assert (stem / "role_cards" / "independent_valuer.docx").exists()
    events = pd.read_csv(stem / "data" / "SIMULATION_main_events_list.csv")
    assert SCRIPT.challenges["creditor_challenge"].title in set(events["Inject"])
    assert main(["--showcase", "--challenge", "rating_downgrade"]) == 0
    assert "rating agency downgrades" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--showcase", "--challenge", "meteor"])


def test_app_offers_assumptions_challenges_and_the_main_events_list():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_SCRIPT
    at.run(timeout=90)
    assert not at.exception
    assert at.radio(key="script_assumptions").value == "Friendly"
    assert at.multiselect(key="script_challenges_friendly").value == [
        "buyer_interest", "second_valuation"]
    assert any("Main events list" in e.label for e in at.expander)
    assert [o for o in at.selectbox(key="script_actor").options] == [
        SCRIPT.actors[player] for player in PLAYERS]
    at.radio(key="script_assumptions").set_value("Hostile").run(timeout=90)
    assert not at.exception
    assert at.multiselect(key="script_challenges_hostile").value == QUIET
    at.checkbox(key="challenge_rating_downgrade").check().run(timeout=90)
    assert not at.exception
