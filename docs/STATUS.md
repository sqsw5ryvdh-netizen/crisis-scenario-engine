# STATUS

Last completed: **M1b - cyber outage and geopolitical scenarios** (2026-11-11), an extra step after M1. Next: M2 (not started).

## What was built
- `engine/model.py`: pure daily loop (run-off, funding waterfall, indicators, triggers, resolution funding need). `simulate(bank, params, scenario)` returns the daily table, events, failure day and funding need.
- `engine/run.py`: CLI printing the daily table, events and outcome.
- `engine/schema.py`: `ModelParams` with every parameter, validated.
- `config/`: 4 archetypes, 28 model parameters, 4 scenarios (`fast_run`, `slow_burn`, `cyber_outage`, `geopolitical`). All values ASSUMPTION.
- M1b: payment outage (withdrawals and maturing wholesale funding are queued, then due on the first day after the outage) and `wholesale_rollover_cap` (0 = market closed). Both are plain parameters switched on by scenario overrides.
- `tests/`: 181 tests, including the 4 sanity checks of CLAUDE.md for all 16 bank/scenario pairs.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- CLI: `.venv/bin/python -m engine.run --bank digital --scenario cyber_outage`
- App (still the empty M0 page): `.venv/bin/streamlit run app/main.py`

## Acceptance output
- `pytest -q` -> `181 passed in 4.56s`
- Failure day (fast_run / slow_burn / cyber_outage / geopolitical): universal 7 / 26 / 12 / 12, retail 5 / 25 / 8 / 11, digital 10 / none / 17 / 20, specialised 4 / 14 / 6 / 7.
- Full tables printed for digital/cyber_outage and specialised/geopolitical.

## Deviations from spec and modelling choices (approved 2026-11-11)
- Equity = CET1. HQLA are sold outright; the haircut loss reduces CET1 (spec only names fire-sale losses).
- Fire sales: capacity of 1% of day-0 illiquid assets is added each day; unused capacity carries over.
- Central bank mobilisation starts on the recovery-trigger day; funding is usable `lag` days later.
- Failure = outflow not fully met after all four waterfall steps, or CET1 ratio below requirement. The run stops there.
- Outage: queued withdrawals count for panic and for the recovery deposit-loss trigger; unpaid withdrawals during the outage are not a failure.
- Geopolitical shock applies from day 1; wider haircuts cost capital only on assets actually sold; central bank haircut unchanged.
- "Zero run-off" = severity 0, no news events, open wholesale market, no outage.
- Rollover is a deterministic fraction (no random draws until M4), so no seed is used yet.
- LCR proxy uses its own outflow rates, excludes central bank capacity and has no Level 2 cap.
- Python 3.12 venv (no 3.11 on this machine).

## Open questions
- "Higher severity never fails later" is verified on a grid (16 pairs x 501 severities, 0 to 10), not proven.
- Archetype numbers are placeholders (the digital bank is the most resilient in every scenario). Calibration is M5.
- With carry-over, a large accumulated fire sale can happen in one day (digital/geopolitical: CET1 ratio drops to 7.6% on the failure day).
