"""Checkpoint before phase E: challenge injects that follow Annex 3 of the EBA Handbook.

(a) rating downgrade and (b) fall in share and bond prices: a panic shock and a cut in
wholesale roll-over. (c) executives address the media and (d) the bank's notification arrives
incomplete or at the wrong player: they change who knows what, not the figures.
"""

import shutil

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from engine import live
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import choices_of
from engine.export import pack_files
from engine.model import simulate
from engine.schema import DECISION_STAGES, PLAYERS, NewsEvent
from engine.script import active_challenges, build_pack, scenario_with_challenges

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
BANK, SCENARIO = CONFIG.banks[SHOWCASE.bank], CONFIG.scenarios[SHOWCASE.scenario]
MARKET = ("rating_downgrade", "market_prices_fall")
INFORMATION = ("executives_press_conference", "incomplete_notification", "misdirected_notification")
PLANNED = choices_of(DECISIONS.paths[SHOWCASE.path])


def pack_with(*challenges, mode="dynamic"):
    paths = [(DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key]))
             for key in (SHOWCASE.compare_path, SHOWCASE.path)]
    return build_pack(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, paths,
                      challenges=list(challenges), mode=mode)


@pytest.fixture(scope="module")
def plain():
    return pack_with()


def test_the_new_injects_are_off_by_default_and_their_numbers_are_assumptions(tmp_path):
    for key in (*MARKET, *INFORMATION):
        item = SCRIPT.challenges[key]
        assert not item.default and not item.hostile
        assert key not in active_challenges(SCRIPT) and key not in active_challenges(SCRIPT, hostile=True)
    for key in MARKET:
        assert SCRIPT.challenges[key].effect == "panic_shock"
        for name in (f"{key}_panic_shock", f"{key}_rollover_cut"):
            parameter = getattr(CONFIG.model, name)
            assert parameter.is_assumption and 0 < parameter.value < 1
    # A challenge with a market effect must have its two parameters in config/model.yaml.
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "script.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["challenges"]["profit_warning"] = dict(data["challenges"]["rating_downgrade"])
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="profit_warning_panic_shock"):
        load_config(tmp_path / "config")


# ------------------------------------------------- (a), (b): market shocks


@pytest.mark.parametrize("key", MARKET)
def test_market_shock_is_a_news_event_with_a_panic_shock_and_a_roll_over_cut(key):
    item = SCRIPT.challenges[key]
    shocked = scenario_with_challenges(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, [key])
    extra = [event for event in shocked.news_events if event not in SCENARIO.news_events]
    base = simulate(BANK, CONFIG.model, SCENARIO, DECISIONS, ())
    anchor = next(e.day for e in base.events if e.kind == item.anchor)
    assert [event.day for event in extra] == [anchor + item.offset_days]
    assert extra[0].panic_shock == getattr(CONFIG.model, f"{key}_panic_shock").value
    assert extra[0].rollover_cut == getattr(CONFIG.model, f"{key}_rollover_cut").value
    worse = simulate(BANK, CONFIG.model, shocked, DECISIONS, ())
    assert worse.scorecard.deposits_lost_pct > base.scorecard.deposits_lost_pct
    table = worse.table
    assert (table["total_assets"] - table["total_liabilities"] - table["cet1"]).abs().max() < 1e-6
    numbers = table.select_dtypes("number").drop(columns=["cet1_ratio_projected"])
    assert (numbers.min() > -1e-9).all()
    pack = pack_with(key)
    news = [e for e in pack.events if e.kind == "news" and e.purpose == "challenge"]
    assert len(news) == 1 and set(news[0].recipients) == set(PLAYERS)


def test_roll_over_cut_alone_raises_wholesale_outflows_where_funding_still_rolls():
    """The cut applies to the funding that would have been renewed: no effect where the
    market is already closed, more wholesale outflow where it is not."""
    found = 0
    for bank in CONFIG.banks.values():
        for key in ("slow_burn", "cyber_outage", "loss_revealed"):
            scenario = CONFIG.scenarios[key]
            base = simulate(bank, CONFIG.model, scenario, DECISIONS, ())
            cut = scenario.model_copy(update={"news_events": [
                *scenario.news_events,
                NewsEvent(day=2, headline="Funding dries up", panic_shock=0.0, rollover_cut=0.5)]})
            after = simulate(bank, CONFIG.model, cut, DECISIONS, ())
            days = min(base.end_day, after.end_day)
            a = base.table.set_index("day")["outflow_wholesale"].loc[:days].sum()
            b = after.table.set_index("day")["outflow_wholesale"].loc[:days].sum()
            assert b >= a - 1e-9
            assert after.table.set_index("day").loc[1].equals(base.table.set_index("day").loc[1])
            found += b > a + 1e-6
            none = scenario.model_copy(update={"news_events": [
                *scenario.news_events,
                NewsEvent(day=2, headline="Nothing happens", panic_shock=0.0, rollover_cut=0.0)]})
            unchanged = simulate(bank, CONFIG.model, none, DECISIONS, ())
            pd.testing.assert_frame_equal(unchanged.table, base.table)
    assert found >= 3
    with pytest.raises(ValidationError):
        NewsEvent(day=1, headline="x", panic_shock=0.0, rollover_cut=1.5)


def test_higher_severity_never_defaults_later_with_the_market_shocks():
    params = CONFIG.model.model_copy(
        update={"weekend_rules_on": CONFIG.model.weekend_rules_on.model_copy(update={"value": 0})})
    for bank in ("universal", "digital"):
        previous = None
        for severity in (0.5, 1.0, 1.5, 2.0, 3.0):
            base = CONFIG.scenarios["fast_run"]
            scenario = base.model_copy(update={"severity": base.severity.model_copy(
                update={"value": severity, "low": None, "high": None})})
            shocked = scenario_with_challenges(
                CONFIG.banks[bank], params, scenario, DECISIONS, SCRIPT, list(MARKET))
            day = simulate(CONFIG.banks[bank], params, shocked, DECISIONS, ()).failure_day
            day = float("inf") if day is None else day
            assert previous is None or day <= previous
            previous = day


# ------------------------------------------------- (c), (d): who knows what


@pytest.mark.parametrize("key", INFORMATION)
def test_information_challenges_change_no_figure_of_the_run(plain, key):
    assert scenario_with_challenges(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, [key]) is SCENARIO
    pack = pack_with(key)
    for branch, reference in zip(pack.branches, plain.branches):
        pd.testing.assert_frame_equal(branch.result.table, reference.result.table)
    assert not any(character.isdigit() for character in SCRIPT.challenges[key].text)
    assert all(len(content) > 0 for content in pack_files(pack, SCRIPT, DECISIONS).values())


def test_press_conference_reaches_players_before_they_are_informed(plain):
    """(c) Bank executives address the media without telling the authorities first."""
    item = SCRIPT.challenges["executives_press_conference"]
    pack = pack_with("executives_press_conference")
    branch = pack.branches[0]
    stage_day = {e.kind: e.day for e in branch.result.events if e.kind in DECISION_STAGES}
    day = stage_day[item.anchor] + item.offset_days
    for player in PLAYERS:
        got = [i for i in branch.injects[player] if i.title == item.title]
        assert len(got) == 1 and got[0].day == day and got[0].purpose == "challenge"
        assert not got[0].figures and not got[0].headline and not got[0].resolution
        assert got[0].delivered_by == "Bank management" and got[0].sender == "Public media"
    # The resolution authority and the deposit guarantee scheme are told nothing about the
    # bank until later stages: through the media they now learn of its difficulty earlier.
    for player in ("resolution_authority", "deposit_guarantee_scheme"):
        informed = stage_day[DECISIONS.information[player].informed_from]
        assert day < informed
        before = [i for i in plain.branches[0].injects[player]
                  if i.day < informed and i.kind not in ("opening", "news")]
        assert before == []


def test_incomplete_notification_withholds_part_of_the_figures(plain):
    """(d) The bank's information is delivered incomplete."""
    item = SCRIPT.challenges["incomplete_notification"]
    pack = pack_with("incomplete_notification")
    stage = lambda p: next(i for i in p.branches[0].injects["competent_authority"]  # noqa: E731
                           if item.anchor in i.stages)
    full, cut = stage(plain), stage(pack)
    assert len(full.headline) > 1 and cut.headline == full.headline[:1]
    assert item.text in cut.text and cut.purpose == "challenge" and cut.expected == item.expected
    assert cut.delivered_by == "Bank management"
    # What it was not told is not recalled in its later decision injects either.
    missing = {f.name for f in full.headline[1:]}
    later = [i for i in pack.branches[0].injects["competent_authority"]
             if i.decision_due and i.day == cut.day]
    assert later and all(not missing & {f.name for f in i.notified} for i in later)
    # The other players' notifications are untouched.
    for player in PLAYERS[1:]:
        assert pack.branches[0].injects[player] == plain.branches[0].injects[player]


def test_misdirected_notification_goes_to_another_player():
    """(d) The bank's information is delivered to the wrong player."""
    item = SCRIPT.challenges["misdirected_notification"]
    holder = item.recipients[0]
    pack = pack_with("misdirected_notification")
    branch = pack.branches[0]
    got = next(i for i in branch.injects[holder] if i.title == item.title)
    assert got.kind == "challenge" and got.sender == "Bank management"
    assert item.anchor in got.stages and len(got.headline) > 1 and item.text in got.text
    assert got.time == SCRIPT.clock["stage_relay"]
    intended = next(i for i in branch.injects["competent_authority"] if item.anchor in i.stages)
    assert intended.fallback and intended.purpose == "control"
    assert intended.time == SCRIPT.clock["stage_relay_others"] > got.time
    assert intended.delivered_by.endswith(f"in place of the {SCRIPT.actors[holder].lower()}")
    assert SCRIPT.actors[holder].lower() in intended.trigger
    # In a linear exercise the control team hands it over itself, later.
    linear = pack_with("misdirected_notification", mode="linear").branches[0]
    late = next(i for i in linear.injects["competent_authority"] if item.anchor in i.stages)
    assert not late.fallback and late.purpose == "control" and late.time == intended.time


def test_live_exercise_holds_back_what_went_to_the_wrong_player():
    context = live.Context(CONFIG.model, DECISIONS, SCRIPT)
    setup = live.make_setup("Misdirected", BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT,
                            PLANNED, challenges=["misdirected_notification"])
    state = live.start(setup, "2028-03-01T09:00:00", context)
    while state.clock < (6, "19:00"):
        state = live.apply(state, "step", "2028-03-01T09:01:00", context)
    title = SCRIPT.challenges["misdirected_notification"].title
    assert any(r.inject.title == title for r in live.released_to(state, "central_bank"))
    assert not any(r.inject.stages for r in live.released_to(state, "competent_authority")
                   if r.inject.day == 6)
    waiting = [i for i in live.pending_fallbacks(state) if i.recipient == "competent_authority"]
    assert len(waiting) == 1 and "early_intervention" in waiting[0].stages
