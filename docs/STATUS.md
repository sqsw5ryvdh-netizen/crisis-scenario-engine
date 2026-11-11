# STATUS

Last completed: **SPEC_V2 phase B2 - fixes after the phase B review** (2026-11-11). Done before: M0-M6, phases A and B. Next by agreement: phase D, then C and E.

## What was built in phase B2
- **Stable core.** Each deposit category has a flight-prone share (`config/model.yaml`): insured retail 20%, uninsured retail 70%, corporate 85%, financial 100%. Only that part can leave. Run-off rates are divided by the share, so day 1 of a run is unchanged and outflows slow as the flight-prone part is used up.
- **Outcome labels.** A bank still open at day 30 is *stabilised* (flag conditions gone, positive buffer, outflows below 0.2% of opening deposits per day, on each of the last 3 days), *kept open on emergency funding* (not stabilised, ELA outstanding) or *still under stress* (not stabilised, no ELA). Otherwise: payments default, resolution, liquidation. "Survives" is gone. Results also give the day the flag conditions cleared.
- **Milder cyber outage.** Severity 2.0 -> 0.8, news shocks halved, pressure easing from day 5. The day the queue is released costs the universal bank 12% of deposits (was 41%).
- `tests/`: 851 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | CLI: `.venv/bin/python -m engine.run --bank universal --scenario geopolitical --path support_package`

## Acceptance output (phase B2)
Without decisions. R/E/F = day of recovery, early intervention, likely-to-fail flag; then outcome and deposits lost.

| Bank | Fast run | Slow burn | Cyber outage | Geopolitical |
|---|---|---|---|---|
| Universal | 1/1/1, default day 5, 57% | 7/-/-, under stress, 24% | 3/4/4, under stress, 34% | 2/3/3, under stress, 60% |
| Retail | 1/1/1, default day 5, 41% | 12/-/-, under stress, 13% | 4/-/-, stabilised, 17% | 2/5/6, under stress, 42% |
| Digital | 1/1/1, default day 2, 29% | 9/-/-, under stress, 18% | 3/4/4, under stress, 23% | 2/3/3, default day 7, 34% |
| Specialised | 1/1/1, default day 4, 62% | 7/-/-, under stress, 29% | 2/15/-, under stress, 41% | 2/3/3, default day 9, 62% |

Universal bank, fast run, by path: ELA refused -> default day 5, 57% lost. ELA granted -> stabilised, 63% lost, 76.4 ELA outstanding. Early FOLTF + moratorium + bail-in -> resolution day 4, 15% lost. Same with liquidation -> DGS payout 214.1 in 5 days.

Universal bank, geopolitical shock: without decisions, still under stress at day 30 (no buffer, CET1 0.5%, flag standing). With the support package, stabilised (buffer 43.5, CET1 never below 11.7%, flag conditions gone). Both lose 60% of deposits.
- `pytest -q` -> `851 passed`.

## Deviations and choices (approved 2026-11-11)
- The acceptance is shown on the geopolitical shock, not the cyber outage: a cyber outage clearly milder than the fast run is absorbed by the universal bank on its own.
- A fifth label, "still under stress", was added for a bank that is open at day 30 without ELA and not stabilised.
- Flight-prone shares were set so that every archetype still defaults in the fast run without decisions.

## Open questions
- "Stabilised" depends on the 30-day horizon: a slow run still leaking on day 30 is "under stress" (all slow-burn runs; the universal bank in the cyber outage, with 110 of buffer left).
- Without decisions the universal bank reaches day 30 of the geopolitical shock with 0.5% CET1 and no default: it funds itself by selling loans at a loss until the outflows stop.
- ELA alone now stabilises the universal bank in the fast run and in the geopolitical shock.
- The exported script does not mention decisions yet (phase D). Every parameter added in A, B and B2 is ASSUMPTION.
- Screenshots in docs/screenshots/ predate phases A, B and B2. A 500-run pass takes about 1.1 s on a 30-day run.
