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


def open_app(demo_on=False, tab=None):
    """Start the app; by default with the guided demo off, so results show at once."""
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = demo_on
    if tab is not None:
        at.session_state["view"] = tab
    return at.run(timeout=30)


def test_app_opens_on_the_demo_selection_with_selectors_sliders_and_timeline():
    at = open_app()
    assert not at.exception
    assert at.selectbox(key="bank").value == demo.DEMO_BANK
    assert at.selectbox(key="scenario").value == demo.DEMO_SCENARIO
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
        (demo.TAB_SCRIPT, lambda at: len(at.expander) >= 2 and len(at.get("download_button")) == 3),
        (demo.TAB_CONTAGION, lambda at: at.checkbox(key="same_country").value is True),
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


def test_contagion_section_reacts_to_the_country_checkbox():
    at = open_app(tab=demo.TAB_CONTAGION)
    similarity = next(m for m in at.metric if m.label == "Similarity")
    assert similarity.value == "100%"
    hit = next(m for m in at.metric if m.label.endswith("with contagion")).value
    at.checkbox(key="same_country").set_value(False).run(timeout=30)
    assert not at.exception
    assert next(m for m in at.metric if m.label == "Similarity").value == "50%"
    assert next(m for m in at.metric if m.label.endswith("with contagion")).value != hit
    at.selectbox(key="second_bank:digital").set_value("retail").run(timeout=30)
    assert next(m for m in at.metric if m.label == "Similarity").value == "0%"
    assert len(at.info) == 1


def test_assumptions_page_opens_from_the_navigation():
    at = open_app()
    at.switch_page("views/assumptions.py").run(timeout=30)
    assert not at.exception
    assert at.title[0].value == "Assumptions"


# ------------------------------------------------- day-by-day view


def _long_run():
    """A run with every stage on a different day and news on days 1 and 6 (default on day 9)."""
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
    assert any(m.value.startswith("**Day 2:") for m in at.markdown)
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
        "Pick", "Pick", "Press", "Watch", "Move", "Export",
    ]
    assert all(step.tab is None or step.tab in demo.TABS for step in demo.DEMO_STEPS)
    assert demo.DEMO_STEPS[-1].tab == demo.TAB_SCRIPT
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
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    at.button(key="demo_next").click().run(timeout=30)
    assert "step 2 of 6" in _panel_title(at)
    at.button(key="demo_next").click().run(timeout=30)
    assert "step 3 of 6: Press run" in _panel_title(at)
    assert len(at.metric) == 0
    assert not [b for b in at.button if b.key == "demo_next"]

    at.button(key="demo_run").click().run(timeout=30)
    assert not at.exception
    assert "step 4 of 6" in _panel_title(at)
    assert at.metric[0].value == "Day 2"
    assert at.slider(key="day").value == 2  # played from day 0 to the end
    assert at.session_state["view"] == demo.TAB_TIMELINE

    at.button(key="demo_next").click().run(timeout=30)
    assert "step 5 of 6: Move a slider" in _panel_title(at)
    next(s for s in at.slider if s.label.startswith("Uninsured")).set_value(30).run(timeout=30)
    assert at.metric[0].value == "Day 5"
    assert at.metric[0].delta.startswith("3 days later")

    at.button(key="demo_next").click().run(timeout=30)
    assert "step 6 of 6: Export" in _panel_title(at)
    assert at.session_state["view"] == demo.TAB_SCRIPT
    assert len(at.get("download_button")) == 3
    assert at.button(key="demo_next").disabled

    at.button(key="demo_back").click().run(timeout=30)
    assert "step 5 of 6" in _panel_title(at)
    assert at.session_state["view"] == demo.TAB_TIMELINE
    assert at.metric[0].value == "Day 5"  # slider values survive moving between steps

    at.button(key="demo_exit").click().run(timeout=30)
    assert not at.exception
    assert not [m for m in at.markdown if m.value.startswith("**Guided demo")]
    assert at.metric[0].value == "Day 5"


# ------------------------------------------------- one-command start


def test_readme_start_command_exists_and_launches_the_app():
    root = Path(APP_PATH).parent.parent
    script = root / "start.sh"
    assert "./start.sh" in (root / "README.md").read_text()
    assert script.stat().st_mode & 0o111  # executable
    assert "streamlit run app/main.py" in script.read_text()
