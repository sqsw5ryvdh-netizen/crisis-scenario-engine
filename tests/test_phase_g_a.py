"""SPEC_V3 phase G, part a: Handbook vocabulary, SIMULATION label, taxonomy, solvency-led
scenario, linear or dynamic exercise."""

import io
import re
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml
from docx import Document
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from app import demo
from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.export import SIMULATION_LABEL, SIMULATION_NOTICE, pack_files, pack_to_zip
from engine.model import simulate
from engine.resolution import resolution_summary
from engine.run import main
from engine.schema import ACTORS, TAXONOMY, TAXONOMY_TITLES
from engine.script import MODE_TEXT, MODES, build_pack

CONFIG = load_config()
DECISIONS, SCRIPT = CONFIG.decisions, CONFIG.script
SHOWCASE = SCRIPT.showcase
ROOT = Path(__file__).resolve().parent.parent
APP_PATH = str(ROOT / "app" / "main.py")
TOL = 1e-9


def showcase_pack(**options):
    paths = [
        (DECISIONS.paths[key].name, choices_of(DECISIONS.paths[key]))
        for key in (SHOWCASE.compare_path, SHOWCASE.path)
    ]
    return build_pack(
        CONFIG.banks[SHOWCASE.bank], CONFIG.model, CONFIG.scenarios[SHOWCASE.scenario],
        DECISIONS, SCRIPT, paths, **options,
    )


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


# ------------------------------------------------- G1: vocabulary


def test_the_word_facilitator_is_gone_from_everything_the_user_sees():
    sources = [ROOT / "README.md", *(ROOT / "config").glob("*.yaml")]
    for folder in ("engine", "app"):
        sources += (ROOT / folder).rglob("*.py")
    for path in sources:
        text = path.read_text(encoding="utf-8").lower()
        assert "facilitator" not in text, path
        assert "participant" not in text, path  # players, in the Handbook's words
    files = pack_files(showcase_pack(), SCRIPT, DECISIONS)
    assert "control_team/control_team_pack.docx" in files
    assert all(name.startswith(("players/", "control_team/")) for name in files)
    for name, content in files.items():
        if name.endswith(".docx"):
            assert "facilitator" not in docx_text(content).lower(), name


def test_foltf_is_spelled_as_in_the_handbook():
    for path in [ROOT / "README.md", *(ROOT / "config").glob("*.yaml")]:
        assert "FOLTF" not in path.read_text(encoding="utf-8"), path
    assert DECISIONS.decisions["declare_foltf"].name == "Failing or likely to fail (FoLtF)"
    assert all("FOLTF" not in label for label in OUTCOME_LABELS.values())
    text = docx_text(pack_files(showcase_pack(), SCRIPT, DECISIONS)["control_team/control_team_pack.docx"])
    assert "FoLtF" in text and "FOLTF" not in text
    assert "Simulation operational timeline" in text and "control team" in text.lower()


# ------------------------------------------------- G9: SIMULATION label


@pytest.mark.parametrize("mode", MODES)
def test_no_exported_document_without_the_simulation_label(mode):
    """Acceptance: every document carries the label (Handbook par. 174)."""
    files = pack_files(showcase_pack(mode=mode), SCRIPT, DECISIONS)
    assert len(files) >= 11
    for name, content in files.items():
        if name.endswith(".docx"):
            document = Document(io.BytesIO(content))
            for section in document.sections:  # on every page: header and footer
                assert SIMULATION_LABEL in section.header.paragraphs[0].text, name
                assert SIMULATION_LABEL in section.footer.paragraphs[0].text, name
            first = [p.text for p in document.paragraphs[:3]]
            assert SIMULATION_NOTICE in first, name  # and under the title
            assert document.core_properties.title.startswith(SIMULATION_LABEL)
        else:
            assert name.endswith(".csv") and SIMULATION_LABEL in Path(name).name, name


def test_zip_holds_only_labelled_files():
    with zipfile.ZipFile(io.BytesIO(pack_to_zip(showcase_pack(), SCRIPT, DECISIONS))) as archive:
        for name in archive.namelist():
            if name.endswith(".csv"):
                assert Path(name).name.startswith(f"{SIMULATION_LABEL}_")
            else:
                assert name.endswith(".docx")


# ------------------------------------------------- G2: taxonomy and the solvency-led scenario


def test_every_crisis_scenario_has_a_place_in_the_handbook_taxonomy():
    assert set(TAXONOMY) == set(TAXONOMY_TITLES) == {"issue", "scope", "speed"}
    assert set(TAXONOMY["issue"]) == {"liquidity", "solvency", "operational", "combination"}
    assert set(TAXONOMY["scope"]) == {"idiosyncratic", "system_wide"}
    assert set(TAXONOMY["speed"]) == {"slow_burning", "rapid_solvency", "fast_burning_liquidity"}
    placed = {key: s.taxonomy for key, s in CONFIG.scenarios.items() if s.taxonomy is not None}
    assert set(CONFIG.scenarios) - set(placed) == {"baseline"}  # calm backdrop, not a crisis
    assert (placed["fast_run"].issue, placed["fast_run"].speed) == ("liquidity", "fast_burning_liquidity")
    assert (placed["slow_burn"].issue, placed["slow_burn"].speed) == ("liquidity", "slow_burning")
    assert placed["cyber_outage"].issue == "operational"
    assert (placed["loss_revealed"].issue, placed["loss_revealed"].speed) == ("solvency", "rapid_solvency")
    # No preset is system-wide: that needs several banks.
    assert {t.scope for t in placed.values()} == {"idiosyncratic"}
    assert placed["geopolitical"].note  # the market-wide trigger is explained
    assert placed["loss_revealed"].label() == "solvency, idiosyncratic, rapid solvency issue"


def test_invalid_taxonomy_or_loss_is_rejected(tmp_path):
    for change in (
        lambda d: d["fast_run"]["taxonomy"].update(speed="instant"),
        lambda d: d["fast_run"]["taxonomy"].pop("scope"),
        lambda d: d["loss_revealed"]["news_events"][0].update(loan_loss_share=1.5),
    ):
        target = tmp_path / f"config{id(change)}"
        shutil.copytree(DEFAULT_CONFIG_DIR, target)
        data = yaml.safe_load((target / "scenarios.yaml").read_text())
        change(data)
        (target / "scenarios.yaml").write_text(yaml.safe_dump(data))
        with pytest.raises(ValidationError):
            load_config(target)


@pytest.mark.parametrize("bank", list(CONFIG.banks))
def test_in_the_solvency_led_scenario_capital_falls_first_and_liquidity_follows(bank):
    scenario = CONFIG.scenarios["loss_revealed"]
    config = CONFIG.banks[bank]
    result = simulate(config, CONFIG.model, scenario)
    table = result.table.set_index("day")
    loss_day = next(e.day for e in scenario.news_events if e.loan_loss_share > 0)
    share = next(e.loan_loss_share for e in scenario.news_events if e.loan_loss_share > 0)
    # Before the loss: no stage is reached and capital is untouched.
    assert (table.loc[: loss_day - 1, "status"] == "normal").all()
    assert table.loc[loss_day - 1, "cet1"] == pytest.approx(config.capital.cet1)
    # The day it is revealed: loans and CET1 fall by the same amount, RWA in proportion.
    written_off = share * table.loc[loss_day - 1, "illiquid_assets"]
    assert table.loc[loss_day, "loan_loss"] == pytest.approx(written_off)
    assert table["loan_loss"].sum() == pytest.approx(written_off)
    assert table.loc[loss_day, "cet1"] == pytest.approx(config.capital.cet1 - written_off)
    assert table.loc[loss_day, "rwa"] == pytest.approx(config.capital.rwa * (1 - share))
    gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
    assert gap.abs().max() < 1e-8
    # Early intervention is triggered by capital, on that day.
    early = next(e for e in result.events if e.kind == "early_intervention")
    assert early.day == loss_day and "CET1" in early.detail
    assert any(name == "cet1_ratio" for name, _, _ in early.figures)
    # Liquidity follows: most of the deposit loss comes after the loss is revealed.
    lost = table["cum_deposit_loss_pct"]
    assert lost.iloc[-1] > 4 * lost.loc[loss_day - 1]
    assert result.scorecard.losses_booked >= written_off - TOL


def test_solvency_led_scenario_differs_from_the_liquidity_led_ones():
    requirement = lambda bank: CONFIG.banks[bank].capital.capital_requirement_ratio  # noqa: E731
    for bank in ("universal", "retail", "digital"):
        solvency = simulate(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios["loss_revealed"])
        flag = next(e for e in solvency.events if e.kind == "likely_to_fail")
        assert "below requirement" in flag.detail  # flagged on capital
        assert solvency.table["cet1_ratio"].iloc[flag.day] < requirement(bank)
        liquidity = simulate(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios["fast_run"])
        flag = next(e for e in liquidity.events if e.kind == "likely_to_fail")
        assert "liquidity covers" in flag.detail  # flagged on liquidity
        assert liquidity.table["cet1_ratio"].iloc[flag.day] > requirement(bank)


def test_ela_is_not_available_to_a_bank_below_its_capital_requirement():
    choices = choices_of(DECISIONS.paths["ela_granted"])
    result = simulate(
        CONFIG.banks["retail"], CONFIG.model, CONFIG.scenarios["loss_revealed"], DECISIONS, choices
    )
    assert result.decision_log and result.table["funded_ela"].abs().max() < TOL


def test_revealed_loss_counts_in_the_resolution_summary_and_not_as_a_fire_sale():
    choices = choices_of(DECISIONS.paths["early_foltf"])
    bank = CONFIG.banks["retail"]
    result = simulate(bank, CONFIG.model, CONFIG.scenarios["loss_revealed"], DECISIONS, choices)
    summary = resolution_summary(bank, CONFIG.model, result)
    assert summary is not None
    assert summary.losses_to_date == pytest.approx(result.table["loan_loss"].sum(), abs=0.05)
    assert result.table["funded_fire_sale"].sum() == pytest.approx(0.0)
    assert summary.recapitalisation_need > 0  # capital is already below the requirement


def test_scenario_designer_filters_the_scenarios_in_the_app():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.run(timeout=60)
    assert not at.exception
    assert at.selectbox(key="scenario").options == [s.name for s in CONFIG.scenarios.values()]
    assert any("liquidity, idiosyncratic, slow burning" in c.value for c in at.caption)
    at.selectbox(key="taxonomy:issue").set_value("solvency").run(timeout=60)
    assert not at.exception
    assert at.selectbox(key="scenario").options == ["Loss revealed"]
    assert at.selectbox(key="scenario").value == "loss_revealed"
    at.selectbox(key="taxonomy:issue").set_value("liquidity").run(timeout=60)
    at.selectbox(key="taxonomy:speed").set_value("slow_burning").run(timeout=60)
    assert at.selectbox(key="scenario").options == ["Slow burn", "Geopolitical shock"]
    # System-wide needs several banks: said so, and nothing is hidden.
    at.selectbox(key="taxonomy:scope").set_value("system_wide").run(timeout=60)
    assert not at.exception
    assert any("several banks" in w.value for w in at.warning)
    assert len(at.selectbox(key="scenario").options) == len(CONFIG.scenarios)


# ------------------------------------------------- G3: linear or dynamic


def test_dynamic_pack_has_two_branches_and_branch_codes():
    pack = showcase_pack()
    assert pack.mode == "dynamic" and len(pack.branches) == 2
    codes = [
        option.branch_code
        for branch in pack.branches for actor in ACTORS
        for inject in branch.injects[actor] if inject.decision_due
        for option in inject.decision_due.options
    ]
    assert codes and all(re.fullmatch(r"D\d-[a-h]", code) for code in codes)


def test_linear_pack_follows_one_fixed_path_without_branch_codes():
    pack = showcase_pack(mode="linear")
    assert pack.mode == "linear" and len(pack.branches) == 1
    assert pack.branches[0].name == DECISIONS.paths[SHOWCASE.path].name  # the selected path
    for document in pack.documents:
        assert document.branches == () and document.shared
    dues = [
        inject.decision_due
        for actor in ACTORS for inject in pack.branches[0].injects[actor] if inject.decision_due
    ]
    assert dues  # decisions are still put to the players, to be recorded
    assert all(option.branch_code == "" for due in dues for option in due.options)
    # The run itself is the same as in the dynamic pack's branch B.
    assert pack.branches[0].result.table.equals(showcase_pack().branches[1].result.table)
    with pytest.raises(ValueError):
        showcase_pack(mode="scripted")


@pytest.mark.parametrize("mode", MODES)
def test_every_document_states_whether_the_exercise_is_linear_or_dynamic(mode):
    files = pack_files(showcase_pack(mode=mode), SCRIPT, DECISIONS)
    other = MODE_TEXT[[m for m in MODES if m != mode][0]]
    for name, content in files.items():
        if name.endswith(".docx"):
            text = docx_text(content)
            assert MODE_TEXT[mode] in text and other not in text, name
    player = docx_text(files["players/resolution_authority.docx"])
    if mode == "linear":
        assert "Branch code" not in player and "Branch A" not in player
        assert "It is recorded, but the exercise is linear" in player
    else:
        assert "Branch code" in player and "Branch A" in player
    control = docx_text(files["control_team/control_team_pack.docx"])
    assert "Scenario type (EBA Handbook, par. 115): liquidity, idiosyncratic, slow burning." in control
    assert ("these other runs are not played" in control) == (mode == "linear")


def test_cli_and_app_offer_the_exercise_type(tmp_path, capsys):
    assert main(["--showcase", "--mode", "linear", "--export-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    data = tmp_path / f"exercise_pack_{SHOWCASE.bank}_{SHOWCASE.scenario}" / "control_team" / "data"
    assert (data / "SIMULATION_daily_table_branch_A.csv").exists()
    assert not (data / "SIMULATION_daily_table_branch_B.csv").exists()
    with pytest.raises(SystemExit):
        main(["--showcase", "--mode", "improvised"])
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_SCRIPT
    at.run(timeout=60)
    assert not at.exception
    assert at.radio(key="script_mode").value == "dynamic"
    assert any(MODE_TEXT["dynamic"] in c.value for c in at.caption)
    assert any(m.value == "**Branch A**" for m in at.markdown)
    at.radio(key="script_mode").set_value("linear").run(timeout=60)
    assert not at.exception
    assert any(MODE_TEXT["linear"] in c.value for c in at.caption)
    assert not any(m.value == "**Branch A**" for m in at.markdown)
