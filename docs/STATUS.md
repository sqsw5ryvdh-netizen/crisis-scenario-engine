# STATUS

Last completed milestone: **M1 - Engine, deterministic** (2026-11-11). Next: M2 (not started).

## What was built
- `engine/model.py`: pure daily loop (run-off, funding waterfall, indicators, triggers, resolution funding need). `simulate(bank, params, scenario)` returns the daily table, events, failure day and funding need.
- `engine/run.py`: CLI printing the daily table, events and outcome.
- `engine/schema.py`: `ModelParams` with every parameter of spec 3.2-3.4, validated.
- `config/`: 4 archetypes, 25 model parameters, 2 scenarios (`fast_run`, `slow_burn`). All values ASSUMPTION.
- `tests/`: 92 tests, including the 4 sanity checks of CLAUDE.md for all 8 bank/scenario pairs.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- CLI: `.venv/bin/python -m engine.run --bank digital --scenario fast_run`
- App (still the empty M0 page): `.venv/bin/streamlit run app/main.py`

## Acceptance output
- `pytest -q` -> `92 passed in 2.58s`
- Failure day per pair (fast_run / slow_burn): universal 7 / 26, retail 5 / 25, digital 10 / none, specialised 4 / 14.
- Full tables printed for digital/fast_run (liquidity failure day 10, funding need 1.76 EUR bn) and universal/slow_burn (CET1 failure day 26, funding need 37.32 EUR bn).

## Deviations from spec and modelling choices (approved 2026-11-11)
- Equity = CET1. HQLA are sold outright; the haircut loss reduces CET1 (spec only names fire-sale losses).
- Fire sales are capped: capacity of 1% of day-0 illiquid assets is added each day and unused capacity carries over. A plain daily cap broke "higher severity never fails later".
- Central bank mobilisation starts on the recovery-trigger day; funding is usable `lag` days later.
- Failure = outflow not fully met after all four waterfall steps, or CET1 ratio below requirement. The run stops at the failure day.
- Rollover is a deterministic fraction (no random draws until M4), so no seed is used yet.
- LCR proxy uses its own outflow rates, excludes central bank capacity and has no Level 2 cap.
- Python 3.12 venv (no 3.11 on this machine).

## Open questions
- "Higher severity never fails later" is verified on a grid (8 pairs x 501 severities, 0 to 10), not proven: the central bank start day depends on severity.
- Cyber/ICT outage and geopolitical presets need mechanics not in spec 3.2 and no milestone claims them. Proposal: a small step before M2.
- Archetype numbers are placeholders: the digital bank fails later than the retail bank in the fast run because it holds more liquid assets. Calibration is M5.
