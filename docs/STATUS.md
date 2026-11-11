# STATUS

Last completed milestone: **M2 - App** (2026-11-11). Next: M3 (not started).

## What was built
- M0-M1b: validated config (`engine/schema.py`, `engine/config.py`), deterministic engine (`engine/model.py`), CLI (`engine/run.py`), 4 archetypes, 4 scenarios.
- `app/main.py`: bank and scenario selectors, 6 sliders, headline row (failure day, deposit loss, funding need), timeline chart, waterfall table.
- `app/charts.py`: plotly timeline with three stacked panels (liquidity buffer, deposits, CET1 ratio), trigger markers with labels, news markers with the headline on hover.
- `engine/model.py`: `with_uninsured_share(bank, share)` moves deposits between insured retail and the other categories, total unchanged.
- Config: `low`/`high` ranges for the slider parameters and for each scenario's severity (all ASSUMPTION).
- `tests/`: 209 tests (config, engine, chart, headless app tests).

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py`
- CLI: `.venv/bin/python -m engine.run --bank digital --scenario fast_run`

## Acceptance output
- `pytest -q` -> `209 passed`.
- Slider re-run, measured headless over 7 moves of the uninsured-share slider: max 0.036 s, median 0.035 s (limit 1 s; also asserted in `tests/test_app.py`).
- Digital bank, fast run: failure day by uninsured share 30% -> 12, 40% -> 10, 50% -> 7, 60% -> 6, 70% -> 5, 80% -> 4.
- Screenshots taken in the browser (digital / fast run, default and 73% uninsured); shown in chat, not stored in the repo.

## Deviations from spec and choices (approved 2026-11-11)
- Sliders: severity, uninsured deposit share, maximum panic multiplier, central bank lag, central bank haircut, fire-sale discount. The uninsured share is a bank property, not a model parameter; its 10-95% slider range is a display limit in `app/main.py`.
- Headline row added (not listed in M2) so the failure-day shift is visible.
- `app/main.py` adds the repo root to `sys.path` so `streamlit run app/main.py` can import `engine`.
- Earlier modelling choices are unchanged: equity = CET1; HQLA sold outright with haircut loss; carry-over fire-sale capacity (1% per day); central bank mobilisation starts at the recovery trigger; run stops at failure; outage queue; geopolitical shock from day 1.

## Open questions
- "Higher severity never fails later" is verified on a grid, not proven.
- Archetype numbers are placeholders (the digital bank is the most resilient archetype). Calibration is M5.
- No uncertainty band on the chart yet (M4).
