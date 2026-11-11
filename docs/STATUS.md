# STATUS

Last completed milestone: **M3 - Exercise script** (2026-11-11). Next: M4 (not started).

## What was built
- M0-M2: validated config, deterministic engine, CLI, 4 archetypes, 4 scenarios, app with selectors, 6 sliders, timeline chart and waterfall table.
- `config/injects.yaml`: templates per key event and actor ("what you see", "decision due"), summary texts, glossary, disclaimer. Validated at load: unknown placeholders, actors or missing events are rejected.
- `engine/injects.py`: pure generator. Key days = opening, news, trigger stages, first use of each funding source, outage start and end, last day.
- `engine/export.py`: script as .docx, injects as .csv, daily table as .csv, all returned as bytes.
- CLI `--export-dir`; app section "Exercise script" with the injects per key day and three download buttons.
- `tests/`: 253 tests.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py`
- Export: `.venv/bin/python -m engine.run --bank digital --scenario fast_run --export-dir exports`

## Acceptance output
- `pytest -q` -> `253 passed in 8.06s`.
- Exported `exports/exercise_script_digital_fast_run.docx` (8 key days, days 0 to 10, 31 injects), read back paragraph by paragraph; plus `injects_...csv` and `daily_table_...csv`.
- Slider re-run with the export built on every run: max 0.13 s (limit 1 s).

## Deviations from spec and choices (approved 2026-11-11)
- New dependency `python-docx`; export is .docx only (no .pdf).
- Inject wording is a draft written for the prototype, with no legal references. It must be reviewed by the team before the demo.
- One facilitator document that includes the outcome; no per-actor handouts.
- `exports/` is ignored by git; no sample export is committed.
- Earlier choices unchanged: equity = CET1; HQLA sold outright; carry-over fire-sale capacity; central bank mobilisation at the recovery trigger; run stops at failure; outage queue; geopolitical shock from day 1; uninsured-share slider is a bank property.

## Open questions
- "Higher severity never fails later" is verified on a grid, not proven.
- Archetype numbers are placeholders (the digital bank is the most resilient archetype). Calibration is M5.
- The LCR proxy shows 0% once HQLA are sold even while central bank capacity remains; this can read oddly in the script.
- The download buttons were checked for presence, not clicked, in the browser; the same bytes are tested through the CLI export.
