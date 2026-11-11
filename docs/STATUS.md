# STATUS

Last completed: **SPEC_V2 phase B - Decision points** (2026-11-11). Done before: M0-M6 (docs/SPEC.md), phase A. Next by agreement: phase D, then C and E.

## What was built in phase B
- `config/decisions.yaml`: nine decisions for the five actors, each with an enabling condition, options and an effect; six named paths. Effects are a fixed list in the engine (panic shock, portfolio sale, ELA with cap, forced recovery option, FOLTF, moratorium, resolution tool, payout preparation, payout).
- `simulate()` takes a decision set. Timing rule: a decision is taken after the close of a day and is in effect the next day; if its condition is not met, it is taken as soon as it is.
- A run now ends at the payments default or on the day a resolution tool is applied. Results carry an outcome, a decision log (actor, day decided, day in effect, the actor's figures that day) and an outcome scorecard.
- Scenario pressure can fade (`fade` in `config/scenarios.yaml`), set for the cyber outage and the geopolitical shock.
- App: decision panel in the sidebar; Decisions tab with path comparison chart, side-by-side scorecard and decision log; decisions table on the Assumptions page. CLI: `--path`.
- `tests/`: 687 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q`
- CLI: `.venv/bin/python -m engine.run --bank universal --scenario fast_run --path early_foltf`

## Acceptance output (phase B)
Universal bank, fast run:

| Path | Outcome | Deposits lost | ELA outstanding | Losses booked |
|---|---|---|---|---|
| ELA refused | payments default, day 4 | 56.7% | 0 | 9.2 |
| ELA granted | payments default, day 9 | 75.0% | 94.6 | 15.6 |
| Early FOLTF, 2-day moratorium, bail-in | resolution, day 4 | 15.2% | 0 | 0 |
| Same with liquidation | liquidation, day 4, DGS payout 214.1 in 5 days | 15.2% | 0 | 0 |

Less severe scenario (universal bank, cyber outage): without decisions, default on day 7. With ELA alone, no default but CET1 ends at 10.8%, below the 11.0% requirement. With statement + portfolio sale + ELA, no default in 30 days and CET1 never below 12.8%; it loses 74.3% of deposits and ends with no liquidity buffer. Same pattern for the retail bank in the geopolitical shock (default day 10; package: CET1 15.1%, 52.3% lost).
- `pytest -q` -> `687 passed`. The default path reproduces the phase A table exactly. App checked in the browser.

## Deviations and choices (approved 2026-11-11)
- Pressure fade added after no combination of decisions could save a bank with constant severity. Its values were chosen so that runs without decisions are unchanged; they are ASSUMPTION.
- Bail-in and sale of business are outcome labels only until phase C adds the liability stack.
- "Survives to weekend" is shown as the default moving from day 4 to day 9: there is no calendar before phase C.
- Every actor sees the same day's figures in the decision log; lags and scopes come with phase D.
- Identity and no-negative-balance checks hold on every path; "higher severity never defaults later" is required on the default path only.

## Open questions
- A rescued bank still loses half to three quarters of its deposits and ends with no liquidity buffer; the flag is raised in every rescue.
- In the fast run a public statement or an early intervention measure cannot change the outcome. The exported script does not mention decisions yet (phase D). The contagion tab ignores decisions (phase F replaces it).
- A 500-run pass on a 30-day run takes about 1 s. Every decision effect and timing value is ASSUMPTION. Screenshots in docs/screenshots/ predate phases A and B.
