"""SPEC_V3 phase H, part b: saving, reloading and replaying a live exercise, the players'
pages, and the scripted acceptance run of the showcase."""

import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import store
from engine import live
from engine.config import load_config
from engine.live_run import AD_HOC_DAY, main, scripted_showcase
from engine.schema import PLAYERS

CONFIG = load_config()
SCRIPT = CONFIG.script
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
FIGURE_TOKEN = re.compile(r"\d+\.\d+%?|\d+%")
CONTROL_NOTES = ("purpose", "trigger", "expected", "delivered_by", "fallback", "how", "note")


@pytest.fixture(scope="module")
def run():
    return scripted_showcase(CONFIG)


def bare(entries):
    return [live.Entry(e.kind, e.real_time, e.data) for e in entries]


def own_tokens(state, player) -> set[str]:
    """Every number in the injects this player has received."""
    tokens = set()
    for item in live.released_to(state, player):
        inject = item.inject
        for figure in inject.figures + inject.headline + inject.notified + inject.resolution:
            tokens.update(FIGURE_TOKEN.findall(figure.text))
            tokens.update(FIGURE_TOKEN.findall(figure.threshold_text))
        tokens.update(FIGURE_TOKEN.findall(inject.text))
    return tokens


# ------------------------------------------------- acceptance: the scripted run


def test_scripted_run_has_five_decisions_two_off_plan_one_ad_hoc_and_one_pause(run):
    state, _ = run
    assert state.ended and state.branch.result.outcome == "liquidation"
    assert len(state.decisions) == 5
    assert [d.decision for d in state.decisions if d.deviates] == [
        "early_intervention_measures", "resolution_tool"]
    assert sum(d.by_control for d in state.decisions) == 1
    kinds = [entry.kind for entry in state.entries]
    assert kinds.count("adhoc") == 1 and kinds.count("pause") == 1 == kinds.count("resume")
    assert kinds.count("decision") == 5 and kinds.count("relay_done") == 1
    adhoc = [r for r in state.released if r.how == "adhoc"]
    assert {r.inject.recipient for r in adhoc} == {"competent_authority", "resolution_authority"}
    assert all(r.day == AD_HOC_DAY for r in adhoc)
    # Every entry has a real and a simulated time stamp, in order.
    stamps = [(e.real_time, (e.day, e.time)) for e in state.entries]
    assert stamps == sorted(stamps) and all(e.time for e in state.entries)
    # The pause is not counted in the time the declaration took.
    foltf = next(d for d in state.decisions if d.decision == "declare_foltf")
    assert foltf.seconds_taken > 0 and state.pauses[0][1] > state.pauses[0][0]


def test_scripted_run_is_the_same_every_time(run, capsys):
    again, _ = scripted_showcase(CONFIG)
    assert again == run[0]
    assert main() == 0
    out = capsys.readouterr().out
    assert "DIFFERENT from the plan" in out and "Outcome: Liquidation on day 13" in out
    assert out.count("| decision   ") == 5 and "| adhoc" in out and "| pause" in out


@pytest.mark.parametrize("player", PLAYERS)
def test_a_players_page_never_shows_another_players_figures(run, player):
    """Acceptance. At every point of the run, every number in a player's view is one from an
    inject that player has received, and none of the control team's notes is in it."""
    state, context = run
    entries = bare(state.entries)
    for upto in range(1, len(entries) + 1, 4):
        past = live.replay(state.setup, entries[:upto], context)
        view = live.player_view(past, context, player)
        assert len(view["injects"]) == len(live.released_to(past, player))
        allowed = own_tokens(past, player)
        for inject in view["injects"]:
            assert not set(inject) & set(CONTROL_NOTES)
            body = json.dumps({k: v for k, v in inject.items() if k != "received"})
            assert set(FIGURE_TOKEN.findall(body)) <= allowed
        mine = {(r.inject.day, r.inject.time, r.inject.title) for r in live.released_to(past, player)}
        assert {(i["day"], i["time"], i["title"]) for i in view["injects"]} == mine
    # Concretely: the bank's own same-day buffer never reaches the resolution authority.
    table = state.branch.result.table.set_index("day")
    final = json.dumps(live.player_view(state, context, "resolution_authority"))
    assert f"{table.loc[2, 'liquidity_buffer']:.2f}" not in final
    assert "Liquidity buffer" not in final
    with pytest.raises(live.LiveError):
        live.player_view(state, context, "bank_management")


# ------------------------------------------------- saving, reloading, replaying


def test_a_saved_exercise_reloads_to_exactly_the_same_state(run):
    state, context = run
    data = json.loads(json.dumps(live.to_saved(state, context)))
    assert data["label"].startswith("SIMULATION")
    back, saved_context = live.from_saved(data)
    assert back == state
    assert saved_context.params == context.params and saved_context.script == context.script
    # It runs on the configuration saved with it, not on today's files.
    data["config"]["script"]["actors"]["central_bank"] = "National central bank"
    renamed, other = live.from_saved(data)
    assert other.script.actors["central_bank"] == "National central bank"
    assert live.player_view(renamed, other, "central_bank")["player"] == "National central bank"
    with pytest.raises(live.LiveError):
        live.from_saved({"format": 99})


def test_replay_stops_at_any_entry(run):
    state, context = run
    entries = bare(state.entries)
    half = live.replay(state.setup, entries[: len(entries) // 2], context)
    assert half.clock < state.clock and len(half.released) < len(state.released)
    assert half.entries == state.entries[: len(entries) // 2]
    kept = [live.inject_key(r.inject) for r in half.released]
    assert [live.inject_key(r.inject) for r in state.released][: len(kept)] == kept


def test_store_saves_one_labelled_file_and_checks_the_codes(run, tmp_path):
    state, context = run
    exercise_id, codes = store.new_id(), store.new_codes()
    assert set(codes) == {*PLAYERS, store.CONTROL} and len(set(codes.values())) == len(codes)
    path = store.save(exercise_id, state, context, codes, tmp_path)
    assert path.name.startswith("SIMULATION_") and path.parent == tmp_path
    assert store.saved_exercises(tmp_path)[0][0] == exercise_id
    view = store.player_view(exercise_id, "central_bank", codes["central_bank"], tmp_path)
    assert view == live.player_view(state, context, "central_bank")
    for wrong in ("", "nope", codes["resolution_authority"], codes[store.CONTROL]):
        assert store.player_view(exercise_id, "central_bank", wrong, tmp_path) is None
    assert store.player_view(exercise_id, "bank_management", codes[store.CONTROL], tmp_path) is None
    assert store.player_view("../../etc", "central_bank", codes["central_bank"], tmp_path) is None
    loaded, loaded_context, loaded_codes = store.load(exercise_id, codes[store.CONTROL], tmp_path)
    assert loaded == state and loaded_codes == codes
    assert store.load(exercise_id, codes["central_bank"], tmp_path) is None
    assert store.load("0" * 8, codes[store.CONTROL], tmp_path) is None


# ------------------------------------------------- the pages


def saved_run(run):
    state, context = run
    exercise_id, codes = store.new_id(), store.new_codes()
    store.save(exercise_id, state, context, codes)
    return exercise_id, codes


def player_page(exercise_id, player, code):
    at = AppTest.from_file(APP_PATH)
    at.query_params["exercise"] = exercise_id
    at.query_params["player"] = player
    at.query_params["code"] = code
    return at.run(timeout=60)


def page_text(at) -> str:
    parts = [e.value for e in (*at.title, *at.markdown, *at.caption, *at.error, *at.info)]
    parts += [str(m.value) for m in at.metric]
    return "\n".join(parts)


@pytest.mark.parametrize("player", PLAYERS)
def test_player_page_shows_only_that_players_injects(run, player):
    state, context = run
    exercise_id, codes = saved_run(run)
    at = player_page(exercise_id, player, codes[player])
    assert not at.exception and not at.error
    assert at.title[0].value == SCRIPT.actors[player]
    text = page_text(at)
    assert "SIMULATION" in text and "Day 13" in text
    mine = {r.inject.title for r in live.released_to(state, player)}
    assert all(title in text for title in mine)
    others = {r.inject.title for r in state.released if r.inject.recipient in PLAYERS} - mine
    assert not any(title in text for title in others)
    assert set(FIGURE_TOKEN.findall(text)) <= own_tokens(state, player) | set(
        FIGURE_TOKEN.findall(" ".join(r.real_time for r in state.released)))
    # Read-only: nothing to press, nothing to type.
    assert not at.button and not at.text_input and not at.selectbox


def test_player_page_refuses_a_wrong_code_or_another_teams_code(run):
    exercise_id, codes = saved_run(run)
    for player, code in (("central_bank", "wrong"), ("central_bank", ""),
                         ("central_bank", codes["resolution_authority"]),
                         ("bank_management", codes[store.CONTROL])):
        at = player_page(exercise_id, player, code)
        assert not at.exception and len(at.error) == 1
        assert not at.title and "Resolution" not in page_text(at).replace("resolution", "")
    assert len(player_page("zzzzzzzz", "central_bank", codes["central_bank"]).error) == 1


def test_console_saves_reloads_and_replays():
    at = AppTest.from_file(APP_PATH)
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="live_start").click().run(timeout=60)
    at.button(key="live_jump").click().run(timeout=60)
    at.button(key="live_step").click().run(timeout=60)
    assert not at.exception
    state, exercise_id = at.session_state["live"], at.session_state["live_id"]
    codes = at.session_state["live_codes"]
    saved = store.read(exercise_id)
    assert saved is not None and len(saved["log"]) == len(state.entries) == 3
    assert any(f"player=central_bank&code={codes['central_bank']}" in m.value for m in at.markdown)
    assert at.slider(key="live_replay_upto").value == 3
    at.slider(key="live_replay_upto").set_value(1).run(timeout=60)
    assert any("After entry 1 (start" in m.value for m in at.markdown)
    # Leave, then come back with the control team's code.
    at.button(key="live_close").click().run(timeout=60)
    assert "live" not in at.session_state
    at.selectbox(key="live_reload_id").set_value(exercise_id).run(timeout=60)
    at.text_input(key="live_reload_code").set_value("not the code").run(timeout=60)
    at.button(key="live_reload").click().run(timeout=60)
    assert "live" not in at.session_state and any("could not be reloaded" in e.value for e in at.error)
    at.text_input(key="live_reload_code").set_value(codes[store.CONTROL]).run(timeout=60)
    at.button(key="live_reload").click().run(timeout=60)
    assert not at.exception and at.session_state["live"] == state
    at.button(key="live_step").click().run(timeout=60)
    assert len(store.read(exercise_id)["log"]) == 4
