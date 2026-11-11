# STATUS

Last completed milestone: **M0 - Skeleton** (2026-11-11). Next: M1 (not started).

## What was built
- Repo layout: `engine/`, `app/`, `config/`, `tests/`, `data/raw/`, `docs/`.
- `engine/schema.py`: pydantic models (`Parameter`, `Bank`, `Scenario`, `Config`). Rejects negative amounts, unbalanced balance sheets, missing `source`, unknown fields.
- `engine/config.py`: `load_config()`, the only engine module that reads files.
- `config/banks.yaml` (digital archetype only), `config/model.yaml` (`horizon_days` only), `config/scenarios.yaml` (`fast_run`, `slow_burn` placeholders).
- `app/main.py`: empty Streamlit page with a title.
- `tests/test_config.py`: 10 tests.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py`

## Acceptance output
- `pytest -q` -> `10 passed in 0.66s`
- App started headless on port 8765: health endpoint HTTP 200, page renders the title "Crisis Scenario Engine".

## Deviations from spec
- Python 3.12 venv (no 3.11 on this machine; spec says 3.11+).
- Balance-sheet equity is represented by CET1 (assets = liabilities + cet1).
- Bank amounts carry one `unit` and one `source` per archetype, not per line.
- `Parameter` accepts optional `low`/`high` now; unused until M4.
- Only 1 of 4 archetypes is in `banks.yaml`; the other three come in M1.
- All numbers in `banks.yaml` and `scenarios.yaml` are ASSUMPTION placeholders.

## Open questions
- Cyber/ICT outage and geopolitical presets need mechanics not in spec 3.2 (blocked payments, time-varying haircuts, closed wholesale market) and no milestone claims them. Which milestone?
- M1 modelling defaults were accepted as proposed on 2026-11-11 (HQLA outright sale with haircut loss to CET1; central bank mobilisation starts at the recovery trigger; daily fire-sale cap; failure when the outflow cannot be met after all four waterfall steps; stop at failure day; funding need over N = 5 days).
- Regulatory figures (LCR-style rates, haircuts) stay ASSUMPTION until sources are confirmed in M5.
