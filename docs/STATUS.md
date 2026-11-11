# STATUS

Last completed milestone: **M4 - Uncertainty** (2026-11-11). Next: M5 (not started).

## What was built
- M0-M3: validated config, deterministic engine, CLI, 4 archetypes, 4 scenarios, app (selectors, 6 sliders, timeline, waterfall table), exercise script with .docx/.csv export.
- `engine/montecarlo.py`: pure Monte Carlo. Each run draws severity and 14 model parameters from a triangular distribution (low, value, high), independently, and runs the deterministic simulation. Seed passed explicitly.
- Outputs: failure-day range and median, share of runs failing, funding-need range, daily 5th-95th percentile bands, failure-day counts.
- App: bands on the three timeline panels, failure-day distribution chart, range line under the headline figures, seed input.
- `app/pages/1_Assumptions.py` + `app/assumptions.py`: every parameter, scenario setting and bank item with value, range, unit and source; ASSUMPTION rows labelled, highlighted and counted.
- CLI: `--runs N --seed S`.
- `tests/`: 271 tests.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py`
- Monte Carlo from the CLI: `.venv/bin/python -m engine.run --bank digital --scenario fast_run --runs 500`

## Acceptance output
- `pytest -q` -> `271 passed in 17.17s`.
- 500 runs: 0.21 s to 0.50 s across the 16 bank/scenario pairs (limit 5 s).
- Same seed (2028) twice: failure days and all daily series identical. Seed 2029: different.
- Digital / fast run, 500 runs: fails in 99% of runs; failure day 3 to 20, median 8; funding need 0.79 to 4.21 EUR bn.
- App slider re-run including Monte Carlo and export: max 0.61 s, median 0.41 s (limit 1 s).

## Deviations from spec and choices (approved 2026-11-11)
- Parameters are drawn independently; wholesale rollover stays a deterministic fraction.
- "Range" = 5th to 95th percentile. A run that does not fail counts as beyond day 30; a failed run's last values are carried forward in the bands.
- A slider sets the central value and its range moves with it (relative width; additive for the lag).
- The chart line and the headline figures are the central run, not the median. The exercise script is unchanged and based on the central run.
- `low`/`high` in config are both the slider range and the sampled range. Slider ranges are therefore narrower than in M2.
- Earlier choices unchanged (see git history of this file).

## Open questions
- All ranges are ASSUMPTION: 108 of the 110 values on the assumptions page have no source. Bands are only as good as these ranges.
- The median run is harsher than the central run (digital fast run: central day 10, median day 8) because several ranges are skewed upwards.
- The sidebar shows the main page as "main" (Streamlit's default from the file name); to tidy in M6.
- "Higher severity never fails later" is verified on a grid, not proven.
