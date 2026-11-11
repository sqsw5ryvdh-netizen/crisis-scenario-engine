# STATUS

Last completed milestone: **M6 - Demo polish** (2026-11-11). All milestones of docs/SPEC.md are done.

## What was built
- M0-M5: validated config, deterministic engine, CLI, exercise script export, Monte Carlo, assumptions page, one-way contagion, calibration of two archetypes (docs/CALIBRATION.md).
- `app/main.py`: now a router with two pages, "Simulation" and "Assumptions" (`app/views/`).
- `app/views/simulation.py`: results in five tabs (Timeline, Funding, Uncertainty, Exercise script, Contagion); only the open tab is computed. Opens on the digital bank and the fast run.
- Day-by-day view: a day slider and a Play button reveal the timeline up to a day, with that day's figures and the events so far. The uncertainty band appears on the last day.
- Reference run: the failure day shows the change against the bank and scenario as first shown, e.g. "4 days earlier than with the starting values (day 10)".
- `app/demo.py`: the guided demo, six steps following spec section 2, with Back, Next, Run and Exit. Each step opens the right tab and outlines the control to use. On by default.
- `start.sh` (one-command start) and `README.md`.
- `tests/`: 393 tests.

## How to run
- App: `./start.sh` (creates `.venv` and installs dependencies on first use; macOS and Linux)
- Tests: `.venv/bin/pytest -q`
- CLI: `.venv/bin/python -m engine.run --bank digital --scenario fast_run`
- Calibration (needs the raw file, see docs/CALIBRATION.md): `.venv/bin/python scripts/calibrate_te2025.py`

## Acceptance output (M6)
- `pytest -q` -> `393 passed`.
- Full demo story run in the browser without touching code: digital bank, fast run, Run, playback from day 0 to failure on day 10 (recovery day 1, early intervention day 4), uninsured deposits 38% -> 60% moves failure to day 6, script tab with the three downloads.
- The Word file served by the download button opens: 142 paragraphs, 9 tables, same figures as the screen.
- `./start.sh` serves the app (health check `ok`); in a clean copy it creates the environment and installs the dependencies.

## Deviations from spec and choices (approved 2026-11-11)
- "Press run" exists only in the guided demo; outside it the app recalculates on every change.
- Playback speed (0.5 s per day) and the opening selection are display settings in `app/demo.py`, not model parameters.
- Minimum Streamlit version raised to 1.67 (tabs that can be opened from code). No new dependency.
- Windows has no one-command start.
- Earlier choices (60% LCR outflow rate for financial deposits, real cases compared but not used to tune, Transparency Exercise only) are unchanged.

## Open questions
- The slow-burn scenario is much faster than the Credit Suisse case; left as is by decision.
- The digital bank is the most resilient archetype (fails on day 10 in the fast run); its numbers are placeholders.
- All behavioural parameters, HQLA, the insured share, the maturity ladder and capital requirements are still ASSUMPTION.
- The wording of the exercise script is a draft and has not been reviewed by a practitioner.
- The preview pane used for the run-through does not save downloads to disk; the file was checked by fetching it from the app. Saving from a normal browser was not tested.
- "Higher severity never fails later" is verified on a grid, not proven.
