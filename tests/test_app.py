import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.charts import TRIGGERS, build_timeline
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
    for kind, (label, _) in TRIGGERS.items():
        day = next(e.day for e in result.events if e.kind == kind)
        assert f"{label}: day {day}" in labels
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


def test_app_runs_and_shows_selectors_sliders_chart_and_table():
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not at.exception
    assert sorted(box.label for box in at.selectbox) == [
        "Bank archetype", "Second bank archetype", "Trigger scenario",
    ]
    assert [box.label for box in at.checkbox] == ["Same country as the first bank"]
    assert len(at.slider) == 6
    assert [n.label for n in at.number_input] == ["Random seed"]
    assert [metric.label for metric in at.metric][0] == "Failure day"
    assert len(at.dataframe) == 1
    assert any(h.value == "Exercise script" for h in at.subheader)
    assert len(at.expander) >= 2


def test_moving_a_slider_reruns_in_under_one_second_and_shifts_failure_day():
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    at.selectbox(key="bank").set_value("digital")
    at.selectbox(key="scenario").set_value("fast_run")
    at.run(timeout=30)
    before = at.metric[0].value
    slider = next(s for s in at.slider if s.label.startswith("Uninsured deposits"))
    start = time.perf_counter()
    slider.set_value(80).run(timeout=30)
    elapsed = time.perf_counter() - start
    assert not at.exception
    assert elapsed < 1.0, f"re-run took {elapsed:.2f}s"
    assert at.metric[0].value != before


def test_every_bank_and_scenario_renders():
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    for bank_key in CONFIG.banks:
        for scenario_key in CONFIG.scenarios:
            at.selectbox(key="bank").set_value(bank_key)
            at.selectbox(key="scenario").set_value(scenario_key)
            at.run(timeout=30)
            assert not at.exception, (bank_key, scenario_key)


def test_contagion_section_reacts_to_the_country_checkbox():
    at = AppTest.from_file(APP_PATH).run(timeout=30)
    at.selectbox(key="bank").set_value("digital")
    at.selectbox(key="scenario").set_value("fast_run")
    at.run(timeout=30)
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
