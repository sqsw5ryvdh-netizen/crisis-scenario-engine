# STATUS

Last completed: **M5c - Contagion** (2026-11-11), the first of three parts of M5. Next: M5a (calibration) and M5b (real cases), both waiting on data and sources.

## What was built
- M0-M4: validated config, deterministic engine, CLI, 4 archetypes, app (selectors, 6 sliders, timeline with bands, waterfall table), exercise script with .docx/.csv export, Monte Carlo, assumptions page.
- `engine/contagion.py`: one-way contagion. Each trigger stage of the first bank becomes, one day later, a news event for the second bank; the panic shock is the stage's shock x similarity. The daily loop is unchanged.
- Similarity = 0.5 if same archetype + 0.5 if same country (config parameters).
- `baseline` scenario (severity 0.05, no news): calm conditions for the second bank.
- App section "Contagion to a second bank": second-bank selector, same-country checkbox, similarity, deposit loss with and without contagion, chart of the second bank's panic multiplier and deposits lost.
- `tests/`: 363 tests.

## How to run
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/pytest -q`
- App: `.venv/bin/streamlit run app/main.py` (contagion is at the bottom of the main page)

## Acceptance output (M5c only)
- `pytest -q` -> `363 passed in 24.01s`.
- Universal bank in the fast run -> second universal bank, same country (similarity 100%): shocks on days 2, 4 and 8; second bank's deposit loss over 30 days 8.5% with contagion against 2.4% without; it reaches the recovery stage and does not fail.
- Similarity 0: second bank identical to its standalone run (tested). More similarity never reduces deposit loss or delays failure (tested on all 16 bank pairs).
- App slider re-run including Monte Carlo, export and contagion: max 0.62 s (limit 1 s).
- The M5 acceptance item `docs/CALIBRATION.md` is NOT done yet (parts a and b).

## Deviations from spec and choices (approved 2026-11-11)
- Contagion is one-way; the second bank is shown for the central run only (no bands) and is not in the exercise script.
- Country is a checkbox in the app, not a field of the fictitious banks.
- `baseline` is also selectable as the first bank's scenario; it triggers nothing.
- Earlier choices unchanged (see git history of this file).

## Open questions
- Contagion shock sizes (0.03 / 0.06 / 0.12), similarity weights and the baseline severity are ASSUMPTION. With them, contagion alone never makes a calm second bank fail.
- After the failure shock the second bank's panic multiplier is close to its maximum; the effect is limited by the low baseline severity, not by the shock size.
- M5a needs EBA data files in `data/raw/`; M5b needs a confirmed list of real cases with primary sources.
- "Higher severity never fails later" is verified on a grid, not proven.
