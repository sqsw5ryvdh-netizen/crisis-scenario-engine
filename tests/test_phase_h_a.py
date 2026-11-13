"""SPEC_V3 phase H, part a: the live engine (log, simulated clock, release of injects,
decisions recorded live, ad hoc injects, pause, jump, undo) and the control console."""

from dataclasses import replace
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from engine import live
from engine.config import load_config
from engine.decisions import choices_of
from engine.schema import ACTORS, PLAYERS
from engine.script import build_branch

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
CONTEXT = live.Context(CONFIG.model, DECISIONS, SCRIPT)
PLANNED = choices_of(DECISIONS.paths[SHOWCASE.path])
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


def setup_for(mode="dynamic", **options):
    return live.make_setup(
        "Test exercise", CONFIG.banks[SHOWCASE.bank], CONFIG.model,
        CONFIG.scenarios[SHOWCASE.scenario], DECISIONS, SCRIPT, PLANNED, mode=mode, **options)


class Clock:
    """Real time for the tests: one minute per call unless told otherwise."""

    def __init__(self):
        self.minute = 0

    def __call__(self, minutes: int = 1) -> str:
        self.minute += minutes
        return f"2028-03-01T{9 + self.minute // 60:02d}:{self.minute % 60:02d}:00"


def do(state, now, kind, **data):
    return live.apply(state, kind, now(), CONTEXT, **data)


def run_to(state, now, moment, decisions=None):
    """Step until the clock reaches `moment`; record `decisions` {decision: (day, option)}
    at the close of their day."""
    decisions = dict(decisions or {})
    while state.clock < moment and not state.ended:
        if state.time == live.CLOSE:
            for decision, (day, option) in list(decisions.items()):
                if day == state.day:
                    state = do(state, now, "decision", decision=decision, option=option)
                    del decisions[decision]
        state = do(state, now, "step")
    return state


PLANNED_LIVE = {choice.decision: (choice.day, choice.option) for choice in PLANNED}
END = (99, live.CLOSE)


@pytest.fixture(scope="module")
def planned_run():
    now = Clock()
    return run_to(live.start(setup_for(), now(), CONTEXT), now, END, PLANNED_LIVE)


def planned_branch(setup):
    return build_branch(
        "A", setup.name, setup.bank, CONFIG.model, setup.scenario, DECISIONS, SCRIPT, PLANNED,
        setup.ambient, setup.writedown_level, setup.mode == "linear", setup.challenges,
        setup.hostile,
    )


# ------------------------------------------------- start, clock and release


def test_start_releases_only_the_opening():
    state = live.start(setup_for(), "2028-03-01T09:00:00", CONTEXT)
    assert state.started and not state.ended and not state.paused
    assert state.clock == (0, SCRIPT.clock["opening"])
    assert {r.inject.kind for r in state.released} == {"opening"}
    assert {r.inject.recipient for r in state.released} == set(ACTORS)
    assert state.setup.bank.name == SCRIPT.setup.bank_alias
    with pytest.raises(live.LiveError):
        live.apply(state, "start", "2028-03-01T09:01:00", CONTEXT)


def test_live_run_on_the_planned_decisions_gives_the_planned_script(planned_run):
    """Entered live as planned, the exercise is the pre-planned branch."""
    branch = planned_branch(planned_run.setup)
    assert planned_run.ended
    assert planned_run.branch.result.outcome == branch.result.outcome == "bail_in"
    for actor in ACTORS:
        expected = [i for i in branch.injects[actor] if not i.fallback]
        got = [r.inject for r in live.released_to(planned_run, actor)]
        without_deadline = lambda i: replace(  # noqa: E731
            i, decision_due=i.decision_due and replace(i.decision_due, deadline_day=0))
        assert sorted(map(live.inject_key, got)) == sorted(map(live.inject_key, expected))
        assert {live.inject_key(i): without_deadline(i).figures for i in got} == {
            live.inject_key(i): i.figures for i in expected}
    assert all(not d.deviates and d.applied for d in planned_run.decisions)


def test_nothing_is_released_before_its_time(planned_run):
    for item in planned_run.released:
        assert (item.inject.day, item.inject.time) <= (item.day, item.time)
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (6, "18:30"))
    assert state.clock == (6, "18:30")
    assert max((r.inject.day, r.inject.time) for r in state.released) == (6, "18:30")
    # The other authorities' notification at 19:00 is not out yet, and never by the clock.
    assert not any(r.inject.kind == "likely_to_fail" for r in live.released_to(state, "central_bank"))


@pytest.mark.parametrize("actor", ACTORS)
def test_a_recipient_only_ever_gets_its_own_injects_and_figures(planned_run, actor):
    """What a player's page will show: its own injects, with figures from its own set."""
    info = DECISIONS.information[actor]
    mine = live.released_to(planned_run, actor)
    assert mine and all(r.inject.recipient == actor for r in mine)
    others = [r for r in planned_run.released if r.inject.recipient != actor]
    assert not set(map(id, mine)) & set(map(id, others))
    for item in mine:
        assert {f.name for f in item.inject.figures} <= set(info.figures) | {"outflow_total"}
        if any(f.block for f in item.inject.figures):
            assert info.intraday
    if info.informed_from is not None:
        first = next(e.day for e in planned_run.branch.result.events if e.kind == info.informed_from)
        assert all(r.inject.day >= first for r in mine if r.inject.kind != "news"
                   and r.inject.kind != "opening")


# ------------------------------------------------- decisions


def test_a_decision_different_from_the_plan_changes_the_path():
    now = Clock()
    choices = dict(PLANNED_LIVE, resolution_tool=(12, "liquidation"))
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, END, choices)
    assert state.ended and state.branch.result.outcome == "liquidation"
    tool = next(d for d in state.decisions if d.decision == "resolution_tool")
    assert tool.deviates and tool.planned_option == "bail_in" and tool.day == 12
    # Injects already handed out are not rewritten when the path changes.
    before = run_to(live.start(setup_for(), Clock()(), CONTEXT), Clock(), (12, live.CLOSE), PLANNED_LIVE)
    kept = [live.inject_key(r.inject) for r in before.released]
    assert [live.inject_key(r.inject) for r in state.released][:len(kept)] == kept


def test_a_planned_decision_not_taken_in_time_drops_out_and_can_be_taken_later():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (10, live.CLOSE))
    assert state.branch.result.foltf_day == 11  # still expected: the day is not over
    state = do(state, now, "step")  # the players let the close of day 10 pass
    assert state.day == 11 and state.branch.result.foltf_day is None
    assert not any(r.inject.kind == "foltf" for r in state.released)
    state = run_to(state, now, (11, live.CLOSE))
    state = do(state, now, "decision", decision="declare_foltf", option="declare")
    record = state.decisions[-1]
    assert record.day == 11 and record.applied and not record.deviates
    assert state.branch.result.foltf_day == 12
    state = do(state, now, "step")
    assert any(r.inject.kind == "foltf" for r in live.released_to(state, "competent_authority"))


def test_recording_no_decision_yet_is_logged_and_leaves_the_decision_open():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (6, live.CLOSE))
    path = state.branch.result.table
    state = do(state, now, "decision", decision="declare_foltf", option="wait")
    record = state.decisions[-1]
    assert not record.applied and record.deviates and record.option == "wait"
    assert "declare_foltf" in {i.decision_due.decision for i in live.open_decisions(state)}
    assert state.branch.result.table.equals(path)
    state = do(state, now, "decision", decision="declare_foltf", option="declare", by_control=True)
    assert state.decisions[-1].applied and state.decisions[-1].by_control
    assert "declare_foltf" not in {i.decision_due.decision for i in live.open_decisions(state)}
    assert state.branch.result.foltf_day == 7


def test_decisions_that_cannot_be_recorded_are_refused():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (2, "18:30"))
    with pytest.raises(live.LiveError, match="not open"):
        do(state, now, "decision", decision="declare_foltf", option="declare")
    state = run_to(state, now, (2, "19:30"))
    assert "ela" in {i.decision_due.decision for i in live.open_decisions(state)}
    with pytest.raises(live.LiveError, match="not an option"):
        do(state, now, "decision", decision="ela", option="print_money")
    state = do(state, now, "decision", decision="ela", option="grant_capped")
    with pytest.raises(live.LiveError, match="not open"):
        do(state, now, "decision", decision="ela", option="refuse")
    with pytest.raises(live.LiveError):
        live.apply(state, "teleport", now(), CONTEXT)


def test_real_time_taken_excludes_pauses():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (2, "19:30"))
    opened = next(r.real_time for r in state.released
                  if r.inject.decision_due and r.inject.decision_due.decision == "ela")
    state = live.apply(state, "pause", now(5), CONTEXT)
    with pytest.raises(live.LiveError, match="paused"):
        do(state, now, "step")
    with pytest.raises(live.LiveError, match="paused"):
        do(state, now, "decision", decision="ela", option="refuse")
    state = live.apply(state, "resume", now(20), CONTEXT)
    assert not state.paused and state.pauses[-1][1]
    with pytest.raises(live.LiveError):
        do(state, now, "resume")
    decided = now(10)
    state = live.apply(state, "decision", decided, CONTEXT, decision="ela", option="refuse")
    minutes = lambda stamp: int(stamp[11:13]) * 60 + int(stamp[14:16])  # noqa: E731
    elapsed = 60 * (minutes(decided) - minutes(opened))
    paused = 60 * (minutes(state.pauses[0][1]) - minutes(state.pauses[0][0]))
    assert paused >= 20 * 60
    assert state.decisions[-1].seconds_taken == pytest.approx(elapsed - paused)


# ------------------------------------------------- fallbacks, jump, ad hoc, undo


def test_what_players_should_pass_on_waits_for_the_control_team():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (2, "19:00"))
    pending = live.pending_fallbacks(state)
    assert [(i.recipient, i.kind) for i in pending] == [("central_bank", "recovery")]
    key = live.inject_key(pending[0])
    assert not any(r.inject.kind == "recovery" for r in live.released_to(state, "central_bank"))
    released = do(state, now, "fallback", key=key)
    got = live.released_to(released, "central_bank")[-1]
    assert got.how == "fallback" and got.inject.kind == "recovery" and got.inject.headline
    assert not live.pending_fallbacks(released)
    passed = do(state, now, "relay_done", key=key)
    assert not live.pending_fallbacks(passed) and key in passed.passed_on
    assert not any(r.inject.kind == "recovery" for r in live.released_to(passed, "central_bank"))
    with pytest.raises(live.LiveError):
        do(passed, now, "fallback", key=key)


def test_jump_goes_to_the_next_milestone_and_releases_what_it_passes():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (2, live.CLOSE))
    count = len(state.released)
    jumped = do(state, now, "jump")
    assert jumped.clock == (6, "18:00")  # the next stage change, as the bank learns of it
    new = jumped.released[count:]
    assert new and all(r.how == "jump" for r in new)
    assert {r.inject.title for r in new} >= {"Press enquiry", "News"}
    # From the last milestone a jump goes to the end.
    state = run_to(jumped, now, (13, "18:00"), PLANNED_LIVE)
    assert do(state, now, "jump").ended
    with pytest.raises(live.LiveError):
        do(do(state, now, "jump"), now, "step")


def test_ad_hoc_injects_free_text_and_from_the_library():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (3, "11:00"))
    sent = do(state, now, "adhoc", recipients=["central_bank", "resolution_authority"],
              title="Call from a large depositor", text="A large depositor asks whether to stay.",
              sender="A corporate treasurer")
    new = sent.released[len(state.released):]
    assert [r.inject.recipient for r in new] == ["central_bank", "resolution_authority"]
    assert all(r.how == "adhoc" and "figures not checked" in r.note for r in new)
    assert all((r.inject.day, r.inject.time) == (3, "11:00") and not r.inject.figures for r in new)
    library = do(state, now, "adhoc", recipients=["competent_authority"],
                 library="ambient:parliamentary_question")
    item = library.released[-1]
    assert item.inject.title == SCRIPT.ambient["parliamentary_question"].title
    assert SCRIPT.setup.bank_alias in item.inject.text
    for bad in ({"recipients": ["bank_management"], "title": "x", "text": "y"},
                {"recipients": [], "title": "x", "text": "y"},
                {"recipients": ["central_bank"], "title": "", "text": "y"},
                {"recipients": ["central_bank"], "library": "ambient:meteor"},
                {"recipients": ["central_bank"], "library": "challenge:rating_downgrade"}):
        with pytest.raises(live.LiveError):
            do(state, now, "adhoc", **bad)
    # A library inject with figures can be sent only once its figures exist.
    with pytest.raises(live.LiveError, match="no figures yet"):
        do(state, now, "adhoc", recipients=["resolution_authority"],
           library="challenge:second_valuation")
    late = run_to(state, now, (11, "12:00"), PLANNED_LIVE)
    valued = do(late, now, "adhoc", recipients=["resolution_authority"],
                library="challenge:buyer_interest")
    assert valued.released[-1].inject.resolution
    # Ad hoc injects change no figure of the run.
    assert valued.branch.result.table.equals(late.branch.result.table)


def test_undo_takes_back_the_last_entry_and_stays_in_the_log():
    now = Clock()
    state = run_to(live.start(setup_for(), now(), CONTEXT), now, (6, live.CLOSE))
    wrong = do(state, now, "decision", decision="declare_foltf", option="declare")
    assert wrong.branch.result.foltf_day == 7
    undone = do(wrong, now, "undo")
    assert replace(undone, entries=()) == replace(state, entries=())
    assert [e.kind for e in undone.entries[-2:]] == ["decision", "undo"]
    assert len(undone.entries) == len(state.entries) + 2
    again = do(do(undone, now, "undo"), now, "step")  # undo also takes back a step
    assert again.started
    fresh = live.start(setup_for(), now(), CONTEXT)
    with pytest.raises(live.LiveError, match="nothing to undo"):
        do(fresh, now, "undo")


def test_replaying_the_log_gives_the_same_exercise(planned_run):
    bare = [live.Entry(e.kind, e.real_time, e.data) for e in planned_run.entries]
    again = live.replay(planned_run.setup, bare, CONTEXT)
    assert again == planned_run
    rows = live.log_rows(planned_run, CONTEXT)
    assert len(rows) == len(planned_run.entries)
    assert rows[0]["Entry"] == "start" and rows[0]["Simulated time"] == "Day 0, 08:00"
    assert all(row["Real time"].startswith("2028-03-01T") for row in rows)
    decision = next(row for row in rows if row["Entry"] == "decision")
    assert "Competent authority" in decision["What"] and "Day 10, 24:00" == decision["Simulated time"]


# ------------------------------------------------- linear exercise


def test_linear_exercise_keeps_its_path_and_corrects_course():
    now = Clock()
    state = run_to(live.start(setup_for("linear"), now(), CONTEXT), now, (6, live.CLOSE))
    assert not live.pending_fallbacks(state)  # a linear exercise has none
    path = state.branch.result.table
    state = do(state, now, "decision", decision="declare_foltf", option="declare")
    record = state.decisions[-1]
    # Recorded and timed, but the fixed path is what counts; the option is the planned one.
    assert not record.applied and not record.deviates
    state = run_to(state, now, END)
    assert state.ended and state.branch.result.outcome == "bail_in"
    assert state.branch.result.table.equals(path)


def test_linear_exercise_releases_a_course_correction_when_players_deviate():
    now = Clock()
    state = run_to(live.start(setup_for("linear"), now(), CONTEXT), now, (10, live.CLOSE))
    state = do(state, now, "decision", decision="resolution_tool", option="liquidation")
    item = live.released_to(state, "resolution_authority")[-1]
    assert item.how == "course_correction" and "bail-in" in item.inject.text.lower()
    conform = do(run_to(live.start(setup_for("linear"), now(), CONTEXT), now, (10, live.CLOSE)),
                 now, "decision", decision="resolution_tool", option="bail_in")
    assert not any(r.how == "course_correction" for r in conform.released)
    assert run_to(state, now, END).branch.result.outcome == "bail_in"


# ------------------------------------------------- control console


def console():
    at = AppTest.from_file(APP_PATH)
    at.switch_page("views/console.py").run(timeout=60)
    return at


def test_console_starts_an_exercise_and_steps_the_clock():
    at = console()
    assert not at.exception and at.title[0].value == "Control console"
    assert at.selectbox(key="live_bank").value == SHOWCASE.bank
    at.button(key="live_start").click().run(timeout=60)
    assert not at.exception
    state = at.session_state["live"]
    assert state.started and state.clock == (0, "08:00")
    assert at.metric[0].value == "Day 0, 08:00" and at.metric[1].value == "Running"
    at.button(key="live_step").click().run(timeout=60)
    at.button(key="live_jump").click().run(timeout=60)
    assert not at.exception
    assert at.session_state["live"].clock > (0, "24:00")
    assert len(at.session_state["live"].entries) == 3


def test_console_records_a_decision_a_pause_an_ad_hoc_inject_and_an_undo():
    at = console()
    at.button(key="live_start").click().run(timeout=60)
    for _ in range(40):
        if any(i.decision_due.decision == "ela"
               for i in live.open_decisions(at.session_state["live"])):
            break
        at.button(key="live_step").click().run(timeout=60)
    at.selectbox(key="live_option_ela").set_value("grant_capped").run(timeout=60)
    at.button(key="live_record_ela").click().run(timeout=60)
    assert not at.exception
    state = at.session_state["live"]
    assert [(d.decision, d.option, d.deviates) for d in state.decisions] == [
        ("ela", "grant_capped", True)]
    at.button(key="live_pause").click().run(timeout=60)
    assert at.session_state["live"].paused and at.metric[1].value == "Paused"
    assert at.button(key="live_step").disabled
    at.button(key="live_resume").click().run(timeout=60)
    at.multiselect(key="live_adhoc_to").set_value(["central_bank"]).run(timeout=60)
    at.text_input(key="live_adhoc_title").set_value("Question from the press").run(timeout=60)
    at.text_area(key="live_adhoc_text").set_value("A journalist calls.").run(timeout=60)
    at.button(key="live_adhoc_send").click().run(timeout=60)
    assert not at.exception and not at.error
    assert at.session_state["live"].released[-1].how == "adhoc"
    count = len(at.session_state["live"].released)
    at.button(key="live_undo").click().run(timeout=60)
    assert len(at.session_state["live"].released) == count - 1
    # An entry the engine refuses is shown, not applied.
    at.button(key="live_adhoc_send").click().run(timeout=60)
    at.multiselect(key="live_adhoc_to").set_value([]).run(timeout=60)
    at.button(key="live_adhoc_send").click().run(timeout=60)
    assert any("recipients" in e.value for e in at.error)
