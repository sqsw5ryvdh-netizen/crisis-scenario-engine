# STATUS

Last completed: **SPEC_V2 phase A - Model realism** (2026-11-11). M0-M6 of docs/SPEC.md are done. Next: phase B (not started).

## What was built in phase A
- **Panic can fall.** The panic input is a fading memory (`panic_decay`, 15% per day); news shocks can be negative (reassuring events). No preset uses a negative shock yet.
- **Two failure concepts.** A forward-looking "likely to fail" flag (liquidity covers fewer than 3 days of the current outflow, or CET1 projected below requirement within 3 days) and the payments default, which ends the run. A capital breach alone no longer ends it.
- **Capital exhausted.** Sales at a loss stop when CET1 would go below zero; the run ends that day.
- **ELA.** A second central bank facility: loan book as extra collateral, higher haircut, only while CET1 is at or above the requirement. Off by default (`ela_granted_from_day: 0`) until phase B makes it a decision.
- **Digital archetype rebuilt** with thin liquid assets and a per-archetype run-off speed factor of 1.5 (1.0 for the others).
- **Slow-burn severity** 1.0 -> 0.3, recorded in docs/CALIBRATION.md section 8 as an order-of-magnitude check.
- App: fourth stage marker, "Likely-to-fail flag" metric, ELA column, "payments default" wording. One new script template. `tests/`: 494 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | CLI: `.venv/bin/python -m engine.run --bank digital --scenario fast_run`

## Acceptance output (phase A)
Day of recovery / early intervention / likely-to-fail flag / payments default, and deposit loss at the end:

| Bank | Fast run | Slow burn | Cyber outage | Geopolitical |
|---|---|---|---|---|
| Universal | 1/1/1/4, 56.7% | 7/30/-/-, 25.3% | 1/4/4/7, 58.5% | 2/3/3/9, 57.3% |
| Retail | 1/1/1/4, 40.0% | 12/-/-/-, 13.9% | 2/4/4/7, 41.9% | 2/5/6/10, 41.7% |
| Digital | 1/1/1/2, 28.8% | 9/26/-/-, 19.3% | 1/4/4/4, 35.6% | 2/3/3/6, 33.6% |
| Specialised | 1/1/1/4, 62.0% | 7/-/-/-, 31.0% | 1/4/4/6, 63.1% | 2/3/3/8, 61.5% |

- 4 pairs survive 30 days with neither flag nor default (the slow burn); the digital bank defaults first in the fast run.
- `pytest -q` -> `494 passed`, including the four domain checks of CLAUDE.md. App checked in the browser on two pairs.
- ELA demonstration (universal, fast run, ELA from day 1): default moves from day 4 to day 9, 94.6 bn drawn.

## Deviations and choices (approved 2026-11-11)
- "Survive" means neither flag nor default. "Solvent" for ELA means CET1 ratio at or above the requirement.
- In the code the payments default keeps the name `failure` / `failed`; only the labels changed.
- The likely-to-fail flag is not required to come earlier with higher severity; only the default day is. The exercise script got one new template only; phase D rewrites it.

## Open questions
- Fast runs raise recovery, early intervention and the flag all on day 1 for three banks.
- A 500-run pass on a 30-day run takes about 1.0 s (0.45 s before), slightly over the 1 s slider target of M2.
- The demo opens on a two-day crisis; raising uninsured deposits no longer moves the default day. With more uninsured deposits a bank can default a day later (earlier recovery trigger mobilises central bank funding earlier). This predates phase A.
- Every parameter added in phase A is ASSUMPTION. The screenshots in docs/screenshots/ predate phase A; to be redone at the end of V2.
