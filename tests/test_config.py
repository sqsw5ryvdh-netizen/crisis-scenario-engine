import ast
import copy
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.schema import Bank, Parameter

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"


@pytest.fixture
def bank_data() -> dict:
    with (DEFAULT_CONFIG_DIR / "banks.yaml").open(encoding="utf-8") as handle:
        return copy.deepcopy(yaml.safe_load(handle)["digital"])


def test_shipped_config_loads():
    config = load_config()
    assert set(config.banks) == {"universal", "retail", "digital", "specialised"}
    assert {"fast_run", "slow_burn"} <= set(config.scenarios)
    assert config.model.horizon_days.value == 30


def test_shipped_banks_balance():
    for bank in load_config().banks.values():
        equity = bank.capital.cet1
        assert bank.assets.total() == pytest.approx(bank.liabilities.total() + equity)


def test_every_parameter_has_unit_and_source():
    config = load_config()
    params = [getattr(config.model, name) for name in type(config.model).model_fields]
    for scenario in config.scenarios.values():
        params.append(scenario.severity)
        params.extend(scenario.overrides.values())
    assert params
    for param in params:
        assert param.unit and param.source


def test_parameter_requires_source():
    with pytest.raises(ValidationError):
        Parameter(value=1.0, unit="days")


def test_parameter_range_must_contain_value():
    with pytest.raises(ValidationError):
        Parameter(value=5.0, unit="x", source="ASSUMPTION", low=1.0, high=2.0)
    assert Parameter(value=1.5, unit="x", source="ASSUMPTION", low=1.0, high=2.0).is_assumption


def test_bank_rejects_negative_amount(bank_data):
    bank_data["assets"]["cash_reserves"] = -1.0
    with pytest.raises(ValidationError):
        Bank(**bank_data)


def test_bank_rejects_unbalanced_sheet(bank_data):
    bank_data["assets"]["illiquid_assets"] += 1.0
    with pytest.raises(ValidationError, match="does not balance"):
        Bank(**bank_data)


def test_bank_rejects_unknown_field(bank_data):
    bank_data["assets"]["gold"] = 1.0
    with pytest.raises(ValidationError):
        Bank(**bank_data)


def test_scenario_override_must_exist_in_model(tmp_path):
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "scenarios.yaml"
    scenarios = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios["fast_run"]["overrides"] = {
        "not_a_parameter": {"value": 1, "unit": "x", "source": "ASSUMPTION"}
    }
    path.write_text(yaml.safe_dump(scenarios), encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown parameters"):
        load_config(tmp_path / "config")


def test_model_rejects_missing_parameter(tmp_path):
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "model.yaml"
    model = yaml.safe_load(path.read_text(encoding="utf-8"))
    del model["fire_sale_discount"]
    path.write_text(yaml.safe_dump(model), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(tmp_path / "config")


def test_model_rejects_haircut_of_one_or_more(tmp_path):
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "model.yaml"
    model = yaml.safe_load(path.read_text(encoding="utf-8"))
    model["central_bank_haircut"]["value"] = 1.0
    path.write_text(yaml.safe_dump(model), encoding="utf-8")
    with pytest.raises(ValidationError, match="central_bank_haircut"):
        load_config(tmp_path / "config")


def test_engine_does_not_import_streamlit():
    for path in ENGINE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.split(".")[0] == "streamlit" for name in names), path
