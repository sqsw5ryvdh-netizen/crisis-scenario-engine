import io
import itertools
import shutil

import pandas as pd
import pytest
import yaml
from docx import Document
from pydantic import ValidationError

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.export import daily_table_to_csv, injects_to_csv, injects_to_frame, script_to_docx
from engine.injects import build_script
from engine.model import simulate
from engine.run import main
from engine.schema import ACTORS

CONFIG = load_config()
PAIRS = list(itertools.product(CONFIG.banks, CONFIG.scenarios))


def make(bank_key: str, scenario_key: str, scenario=None):
    bank = CONFIG.banks[bank_key]
    scenario = scenario or CONFIG.scenarios[scenario_key]
    result = simulate(bank, CONFIG.model, scenario)
    return result, build_script(bank, CONFIG.model, scenario, result, CONFIG.injects)


def all_text(script) -> str:
    parts = [script.summary]
    for key_day in script.key_days:
        for event in key_day.events:
            parts += [event.title, event.situation]
            for inject in event.injects:
                parts += [inject.sees, inject.decision]
    return "\n".join(parts)


def edit_injects(tmp_path, change):
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "injects.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path / "config"


# ------------------------------------------------- templates


def test_template_with_unknown_placeholder_is_rejected(tmp_path):
    def change(data):
        data["events"]["news"]["situation"] = "Rating is {credit_rating}."

    with pytest.raises(ValidationError, match="credit_rating"):
        load_config(edit_injects(tmp_path, change))


def test_missing_event_template_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="first_fire_sale"):
        load_config(edit_injects(tmp_path, lambda data: data["events"].pop("first_fire_sale")))


def test_unknown_actor_is_rejected(tmp_path):
    def change(data):
        data["events"]["news"]["injects"]["journalist"] = {"sees": "x", "decision": "y"}

    with pytest.raises(ValidationError, match="journalist"):
        load_config(edit_injects(tmp_path, change))


# ------------------------------------------------- generator


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_every_simulation_event_is_in_the_script(bank, scenario):
    result, script = make(bank, scenario)
    by_day = {key_day.day: [event.kind for event in key_day.events] for key_day in script.key_days}
    for event in result.events:
        assert event.kind in by_day[event.day], event
    assert by_day[0][0] == "opening"
    days = [key_day.day for key_day in script.key_days]
    assert days == sorted(set(days))
    assert days[-1] == result.table["day"].iloc[-1]
    last_kinds = by_day[days[-1]]
    assert ("failure" in last_kinds) == (result.failure_day is not None)
    assert ("end_no_failure" in last_kinds) == (result.failure_day is None)


@pytest.mark.parametrize("bank,scenario", PAIRS)
def test_all_actors_appear_and_no_placeholder_is_left(bank, scenario):
    _, script = make(bank, scenario)
    actors = {
        inject.actor for key_day in script.key_days for event in key_day.events
        for inject in event.injects
    }
    assert actors == set(ACTORS)
    text = all_text(script)
    assert "{" not in text and "}" not in text


def test_first_use_of_each_funding_source_is_a_key_event():
    result, script = make("digital", "fast_run")
    by_kind = {
        event.kind: key_day.day for key_day in script.key_days for event in key_day.events
    }
    table = result.table
    for kind, column in [
        ("first_hqla_sale", "funded_hqla"),
        ("first_central_bank", "funded_central_bank"),
        ("first_fire_sale", "funded_fire_sale"),
    ]:
        assert by_kind[kind] == table.loc[table[column] > 1e-9, "day"].iloc[0]


def test_numbers_in_the_text_match_the_simulation():
    result, script = make("digital", "fast_run")
    table = result.table.set_index("day")
    failure_day = script.key_days[-1]
    assert failure_day.day == result.failure_day
    failure = next(e for e in failure_day.events if e.kind == "failure")
    assert f"{result.funding_need.need:,.2f} EUR bn" in failure.situation
    assert f"day {result.failure_day}" in script.summary
    assert f"{table.loc[result.failure_day, 'cum_deposit_loss_pct']:.1f}%" in script.summary
    opening = script.key_days[0].events[0]
    assert f"{table.loc[0, 'deposits_total']:,.2f} EUR bn" in opening.situation
    figures = dict(script.key_days[1].figures)
    assert figures["Liquidity buffer (EUR bn)"] == f"{table.loc[1, 'liquidity_buffer']:,.2f}"


def test_outage_events_are_in_the_cyber_script():
    _, script = make("retail", "cyber_outage")
    by_kind = {
        event.kind: key_day.day for key_day in script.key_days for event in key_day.events
    }
    assert by_kind["outage_start"] == 1
    assert by_kind["outage_end"] == 4


def test_settings_list_parameters_changed_from_default():
    _, script = make("universal", "geopolitical")
    labels = [label for label, _ in script.settings]
    assert "Fire sale discount" in labels
    assert "Wholesale rollover cap" in labels
    _, plain = make("universal", "fast_run")
    assert len(plain.settings) == 4


def test_script_without_failure_ends_with_survival():
    result, script = make("digital", "slow_burn")
    assert result.failure_day is None
    assert "does not fail" in script.summary
    assert script.key_days[-1].events[-1].kind == "end_no_failure"


# ------------------------------------------------- export


def test_docx_opens_and_contains_every_key_day_and_actor():
    _, script = make("digital", "fast_run")
    document = Document(io.BytesIO(script_to_docx(script)))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    for key_day in script.key_days:
        assert any(h.startswith(f"Day {key_day.day}:") for h in headings)
    for name in CONFIG.injects.actors.values():
        assert name in headings
    for expected in ("Summary", "Run settings", "Timeline of key days", "Glossary"):
        assert expected in headings
    body = "\n".join(p.text for p in document.paragraphs)
    assert body.count("Decision due:") == len(injects_to_frame(script))
    assert script.summary in body


def test_injects_csv_round_trips():
    _, script = make("digital", "fast_run")
    frame = pd.read_csv(io.BytesIO(injects_to_csv(script)))
    assert list(frame.columns) == [
        "day", "event", "event_title", "situation", "actor", "what_you_see", "decision_due",
    ]
    assert len(frame) == len(injects_to_frame(script))
    assert set(frame["actor"]) == set(CONFIG.injects.actors.values())


def test_daily_table_csv_round_trips():
    result, _ = make("digital", "fast_run")
    frame = pd.read_csv(io.BytesIO(daily_table_to_csv(result)))
    assert list(frame.columns) == list(result.table.columns)
    assert len(frame) == len(result.table)
    assert frame["deposits_total"].tolist() == pytest.approx(result.table["deposits_total"].tolist())


def test_cli_export_writes_three_files(tmp_path, capsys):
    assert main(["--bank", "digital", "--scenario", "fast_run", "--export-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [
        "daily_table_digital_fast_run.csv",
        "exercise_script_digital_fast_run.docx",
        "injects_digital_fast_run.csv",
    ]
    Document(str(tmp_path / "exercise_script_digital_fast_run.docx"))
