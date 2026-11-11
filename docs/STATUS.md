# STATUS

Last completed: **M5a - Calibration of archetypes** (2026-11-11). M5c (contagion) is also done. Next: M5b (comparison with real cases), not started.

## What was built
- M0-M4 and M5c: validated config, deterministic engine, CLI, app (selectors, sliders, timeline with bands, waterfall table), exercise script export, Monte Carlo, assumptions page, one-way contagion.
- `scripts/calibrate_te2025.py`: reads the EBA 2025 Transparency Exercise file, builds two peer groups and prints the median proportions and the resulting `config/banks.yaml` blocks.
- `config/banks.yaml`: universal and retail archetypes now use peer medians for cash, retail deposit total, corporate and financial deposits, wholesale funding total, CET1 and RWA. Each line has its own source (`sources:`).
- `engine/schema.py`: optional per-line sources on a bank; the assumptions page shows calibrated lines as "Sourced" and flags any line that rests even partly on an assumption.
- `docs/CALIBRATION.md`: files with checksums, item and column of every number, peer banks, results, gaps.
- `tests/`: 372 tests.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py`
- Calibration (needs the raw file, see docs/CALIBRATION.md): `.venv/bin/python scripts/calibrate_te2025.py`

## Acceptance output (M5a)
- `pytest -q` -> `372 passed`.
- Peer groups at June 2025: universal 28 banks in 10 countries, retail 40 banks in 19 countries, out of 107 reporting banks.
- Opening LCR proxy: universal 118%, retail 139%, digital 215%, specialised 152%.
- Central-run failure day before -> after: universal fast run 7 -> 4, slow burn 26 -> 16; retail fast run 5 -> 4, slow burn 25 -> 21.

## Deviations from spec and choices (approved 2026-11-11)
- Only the Transparency Exercise was used; the Pillar 3 Data Hub was not explored.
- Digital and specialised archetypes stay ASSUMPTION: the data has no field that identifies them.
- `lcr_outflow_rate_financial` lowered from 1.00 to 0.60 (ASSUMPTION), because the calibrated universal bank otherwise opened below the recovery threshold.
- Government deposits count as corporate; deposits from credit institutions count as financial; equity above CET1 sits in other liabilities.
- Earlier choices unchanged (see git history of this file).

## Open questions
- M5b needs a confirmed list of real cases with primary sources. Until then every run-off parameter is ASSUMPTION.
- HQLA, the insured share of retail deposits, the wholesale maturity ladder and capital requirements have no source yet.
- The retail peer rule is purely numerical and also picks up some online or specialised banks.
- The raw EBA files are not in git; the test that compares config with the script is skipped without them.
- "Higher severity never fails later" is verified on a grid, not proven.
