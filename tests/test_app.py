import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import demo
from app.charts import TRIGGERS, build_timeline
from engine.montecarlo import bands, run_monte_carlo
from engine.config import load_config
from engine.model import simulate, uninsured_share, with_uninsured_share

CONFIG = load_config()
HORIZON = int(CONFIG.model.horizon_days.value)
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


# ------------------------------------------------- uninsured-share helper


@pytest.mark.parametrize("bank_key", list(CONFIG.banks))
@pytest.mark.parametrize("share", [0.0, 0.1, 0.5, 0.95, 1.0])
def test_with_uninsured_share_keeps_totals(bank_key, share):
    bank = CONFIG.banks[bank_key]
    changed = with_uninsured_share(bank, share)
    assert changed.liabilities.deposits.total() == pytest.approx(bank.liabilities.deposits.total())
    assert changed.assets.total() == pytest.approx(
        changed.liabilities.total() + changed.capital.cet1
    )
    assert uninsured_share(changed) == pytest.approx(share)
    assert min(changed.liabilities.deposits.model_dump().values()) >= 0


def test_with_uninsured_share_keeps_the_mix_of_uninsured_categories():
    bank = CONFIG.banks["universal"]
    before = bank.liabilities.deposits
    after = with_uninsured_share(bank, 0.8).liabilities.deposits
    assert after.corporate / after.financial == pytest.approx(before.corporate / before.financial)


def test_with_uninsured_share_rejects_bad_share():
    with pytest.raises(ValueError):
        with_uninsured_share(CONFIG.banks["digital"], 1.2)


def test_more_uninsured_deposits_never_fail_later():
    days = []
    for share in (0.2, 0.4, 0.6, 0.8):
        bank = with_uninsured_share(CONFIG.banks["digital"], share)
        day = simulate(bank, CONFIG.model, CONFIG.scenarios["fast_run"]).failure_day
        days.append(float("inf") if day is None else day)
    assert days == sorted(days, reverse=True)


# ------------------------------------------------- chart


def test_timeline_has_three_lines_and_all_trigger_markers():
    result = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    fig = build_timeline(result, HORIZON, "EUR bn")
    lines = [trace for trace in fig.data if trace.mode == "lines"]
    assert len(lines) == 3
    assert all(len(trace.x) == len(result.table) for trace in lines)
    labels = [annotation.text for annotation in fig.layout.annotations]
    for kind in ("recovery", "early_intervention", "likely_to_fail", "failure"):
        day = next(e.day for e in result.events if e.kind == kind)
        assert f"{TRIGGERS[kind][0]}: day {day}" in labels
    news = [trace for trace in fig.data if trace.name == "News"]
    assert len(news) == 1 and len(news[0].x) == 2
    assert tuple(fig.layout.xaxis.range) == (0, HORIZON)


def test_timeline_without_triggers_has_no_markers():
    scenario = CONFIG.scenarios["fast_run"].model_copy(
        update={
            "severity": CONFIG.scenarios["fast_run"].severity.model_copy(update={"value": 0.0}),
            "news_events": [],
        }
    )
    result = simulate(CONFIG.banks["retail"], CONFIG.model, scenario)
    fig = build_timeline(result, HORIZON, "EUR bn")
    labels = [annotation.text for annotation in fig.layout.annotations]
    assert not any("day" in label for label in labels)
    assert len(fig.data) == 3


# ------------------------------------------------- Streamlit page (headless)


SHOWCASE = CONFIG.script.showcase


def open_app(demo_on=False, tab=None, bank="digital", scenario="fast_run", path="act_late"):
    """Start the app with the guided demo off, so results show at once.

    By default on the digital bank in the fast run without decisions, a short run with a
    payments default on day 2. `bank=None` opens the app as a user gets it: on the showcase.
    """
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = demo_on
    if tab is not None:
        at.session_state["view"] = tab
    if bank is not None:
        at.session_state["bank"] = bank
        at.session_state["scenario"] = scenario
        at.session_state["decision_path"] = path
    return at.run(timeout=60)


def test_app_opens_on_the_showcase_run():
    at = open_app(bank=None)
    assert not at.exception
    assert at.selectbox(key="bank").value == SHOWCASE.bank
    assert at.selectbox(key="scenario").value == SHOWCASE.scenario
    assert at.selectbox(key="decision_path").value == SHOWCASE.path
    assert at.selectbox(key="dec:declare_foltf:option").value == "declare"
    assert at.number_input(key="dec:declare_foltf:day").value == 10
    # FOLTF after the second Friday's close, resolution over the weekend, end on Monday.
    assert (at.metric[0].value, at.metric[0].delta) == ("Avoided", "Resolution: bail-in on day 13")
    assert at.slider(key="day").value == 13
    assert any(m.value.startswith("**Day 13, Monday") for m in at.markdown)
    assert any(m.label.startswith("Requested, not yet settled") for m in at.metric)


def test_app_has_selectors_sliders_tabs_and_timeline():
    at = open_app()
    assert not at.exception
    labels = [box.label for box in at.sidebar.selectbox]
    assert labels[:3] == ["Bank archetype", "Trigger scenario", "Decision path"]
    assert len(labels) == 3 + len(CONFIG.decisions.decisions)  # one option box per decision
    assert len(at.sidebar.slider) == 6
    assert [n.label for n in at.number_input] == ["Random seed"]
    assert [metric.label for metric in at.metric][:2] == ["Payments default", "Likely-to-fail flag"]
    assert [tab.label for tab in at.tabs] == demo.TABS
    assert at.slider(key="day").value == 2  # whole run shown: payments default on day 2


@pytest.mark.parametrize(
    "tab, check",
    [
        (demo.TAB_FUNDING, lambda at: len(at.dataframe) == 1),
        (demo.TAB_UNCERTAINTY, lambda at: any(
            h.value == "Payments default day distribution" for h in at.subheader)),
        (demo.TAB_SCRIPT, lambda at: len(at.expander) >= 2 and len(at.get("download_button")) == 1),
    ],
)
def test_each_tab_shows_its_content(tab, check):
    at = open_app(tab=tab)
    assert not at.exception
    assert check(at)
    assert not [s for s in at.slider if s.key == "day"]  # tabs are lazy: no timeline here


def test_moving_a_slider_reruns_in_under_one_second_and_shifts_failure_day():
    at = open_app()
    before = at.metric[0].value
    assert not at.metric[0].delta
    slider = next(s for s in at.slider if s.label.startswith("Uninsured deposits"))
    start = time.perf_counter()
    slider.set_value(30).run(timeout=30)
    elapsed = time.perf_counter() - start
    assert not at.exception
    assert elapsed < 1.0, f"re-run took {elapsed:.2f}s"
    assert (before, at.metric[0].value) == ("Day 2", "Day 5")
    assert at.metric[0].delta == "3 days later than with the starting values (day 2)"
    assert at.slider(key="day").value == 5  # the day view follows the new run


@pytest.mark.parametrize("tab", demo.TABS)
def test_every_bank_and_scenario_renders(tab):
    at = open_app(tab=tab)
    for bank_key in CONFIG.banks:
        for scenario_key in CONFIG.scenarios:
            at.selectbox(key="bank").set_value(bank_key)
            at.selectbox(key="scenario").set_value(scenario_key)
            at.run(timeout=90)  # generous: the whole suite runs many app sessions in a row
            assert not at.exception, (bank_key, scenario_key)


def test_contagion_tab_is_hidden():
    at = open_app()
    assert "Contagion" not in [tab.label for tab in at.tabs]
    assert not [box for box in at.selectbox if "Second bank" in box.label]


def test_timeline_day_panel_shows_weekday_and_outflow_by_time_block():
    at = open_app()
    at.slider(key="day").set_value(1).run(timeout=60)
    text = "\n".join(m.value for m in at.markdown)
    assert "**Day 1, Monday:" in text and "**Outflow by time of day**" in text
    for block in ("09:00-12:00", "12:00-15:00", "15:00-18:00", "after 18:00"):
        assert f"- {block}:" in text
    assert any("allocation of the daily figure" in c.value for c in at.caption)


def test_assumptions_page_opens_from_the_navigation():
    at = open_app()
    at.switch_page("views/assumptions.py").run(timeout=30)
    assert not at.exception
    assert at.title[0].value == "Assumptions"


# ------------------------------------------------- day-by-day view


def _long_run():
    """A 30-day run with stage changes on days 2 and 3 and news on days 1 and 6."""
    bank, scenario = CONFIG.banks["universal"], CONFIG.scenarios["geopolitical"]
    return bank, scenario, simulate(bank, CONFIG.model, scenario)


@pytest.mark.parametrize("day", [0, 1, 3, 4, 8])
def test_day_view_never_shows_later_days(day):
    bank, scenario, result = _long_run()
    band_data = bands(run_monte_carlo(bank, CONFIG.model, scenario, 50, 1))
    fig = build_timeline(result, HORIZON, "EUR bn", band_data, until_day=day)
    assert len(fig.data) == (3 if day == 0 else 4)  # three lines, plus news from day 1; no band
    assert all(max(trace.x) <= day for trace in fig.data)
    labels = [a.text for a in fig.layout.annotations if "day" in a.text]
    shown = [f"{TRIGGERS[e.kind][0]}: day {e.day}" for e in result.events
             if e.kind in TRIGGERS and e.day <= day]
    assert labels == shown


def test_day_view_keeps_the_axes_of_the_whole_run_and_ends_with_the_band():
    bank, scenario, result = _long_run()
    band_data = bands(run_monte_carlo(bank, CONFIG.model, scenario, 50, 1))
    whole = build_timeline(result, HORIZON, "EUR bn", band_data)
    for day in (0, 5):
        part = build_timeline(result, HORIZON, "EUR bn", band_data, until_day=day)
        for axis in ("yaxis", "yaxis2", "yaxis3"):
            assert part.layout[axis].range == whole.layout[axis].range
    last = build_timeline(result, HORIZON, "EUR bn", band_data, until_day=result.failure_day)
    assert len(last.data) == len(whole.data) == 10  # 3 lines, 6 band edges, news


def test_play_reveals_the_run_from_day_zero_to_the_last_day(monkeypatch):
    monkeypatch.setattr(demo, "PLAYBACK_SECONDS", 0)
    at = open_app()
    at.selectbox(key="bank").set_value("universal").run(timeout=30)
    at.slider(key="day").set_value(2).run(timeout=30)
    assert any(m.value.startswith("**Day 2, Tuesday:") for m in at.markdown)
    assert not any("Day 5," in m.value for m in at.markdown)  # the default is not shown yet
    at.button(key="play").click().run(timeout=30)
    assert not at.exception
    assert at.slider(key="day").value == 5
    assert at.session_state["play_next"] is None


# ------------------------------------------------- guided demo


def test_describe_shift():
    assert demo.describe_shift(10, 10) is None
    assert demo.describe_shift(None, None) is None
    assert demo.describe_shift(5, 10) == "5 days earlier than with the starting values (day 10)"
    assert demo.describe_shift(11, 10) == "1 day later than with the starting values (day 10)"
    assert demo.describe_shift(None, 10) == "Payments default on day 10 with the starting values"
    assert demo.describe_shift(7, None) == "No payments default with the starting values"


def test_demo_steps_follow_the_story_and_name_existing_tabs():
    assert len(demo.DEMO_STEPS) == 6
    assert [step.title.split()[0] for step in demo.DEMO_STEPS] == [
        "Pick", "Pick", "Press", "Watch", "Change", "Export",
    ]
    assert all(step.tab is None or step.tab in demo.TABS for step in demo.DEMO_STEPS)
    assert demo.DEMO_STEPS[-1].tab == demo.TAB_SCRIPT
    assert demo.DEMO_STEPS[4].tab == demo.TAB_DECISIONS
    assert ".st-key-ctl_bank" in demo.highlight_css("ctl_bank")


def _panel_title(at):
    return next(m.value for m in at.markdown if m.value.startswith("**Guided demo"))


def test_demo_is_on_at_start_and_hides_results_until_run():
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not at.exception
    assert "step 1 of 6: Pick a bank archetype" in _panel_title(at)
    assert len(at.metric) == 0 and len(at.info) == 1
    assert at.button(key="demo_back").disabled


def test_full_demo_walk_forward_and_back(monkeypatch):
    monkeypatch.setattr(demo, "PLAYBACK_SECONDS", 0)
    at = AppTest.from_file(APP_PATH).run(timeout=60)
    assert at.selectbox(key="bank").value == SHOWCASE.bank
    at.button(key="demo_next").click().run(timeout=60)
    assert "step 2 of 6" in _panel_title(at)
    at.button(key="demo_next").click().run(timeout=60)
    assert "step 3 of 6: Press run" in _panel_title(at)
    assert len(at.metric) == 0
    assert not [b for b in at.button if b.key == "demo_next"]

    at.button(key="demo_run").click().run(timeout=60)
    assert not at.exception
    assert "step 4 of 6" in _panel_title(at)
    assert (at.metric[0].value, at.metric[0].delta) == ("Avoided", "Resolution: bail-in on day 13")
    assert at.slider(key="day").value == 13  # played from day 0 to the end, across a weekend
    assert at.session_state["view"] == demo.TAB_TIMELINE

    at.button(key="demo_next").click().run(timeout=60)
    assert "step 5 of 6: Change a decision" in _panel_title(at)
    assert at.session_state["view"] == demo.TAB_DECISIONS
    at.selectbox(key="decision_path").set_value("act_late").run(timeout=60)
    assert not at.exception
    assert (at.metric[0].value, at.metric[0].delta) == (
        "None in 30 days", "Still under stress, no default",
    )

    at.button(key="demo_next").click().run(timeout=60)
    assert "step 6 of 6: Export" in _panel_title(at)
    assert at.session_state["view"] == demo.TAB_SCRIPT
    assert len(at.get("download_button")) == 1
    assert at.button(key="demo_next").disabled

    at.button(key="demo_back").click().run(timeout=60)
    assert "step 5 of 6" in _panel_title(at)
    assert at.session_state["view"] == demo.TAB_DECISIONS
    assert at.metric[0].value == "None in 30 days"  # the choice survives moving between steps

    at.button(key="demo_exit").click().run(timeout=60)
    assert not at.exception
    assert not [m for m in at.markdown if m.value.startswith("**Guided demo")]
    assert at.metric[0].value == "None in 30 days"


# ------------------------------------------------- one-command start


def test_readme_start_command_exists_and_launches_the_app():
    root = Path(APP_PATH).parent.parent
    script = root / "start.sh"
    assert "./start.sh" in (root / "README.md").read_text()
    assert script.stat().st_mode & 0o111  # executable
    assert "streamlit run app/main.py" in script.read_text()
