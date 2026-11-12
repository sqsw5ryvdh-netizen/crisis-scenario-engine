# STATUS

Last completed: **SPEC_V2 phase C1 - Calendar and script fixes** (2026-11-13). Done before: M0-M6, phases A, B, B2 and D. Next: phase C2 (resolution), after review. Then E.

## What was built in phase C1
- **Calendar.** Day 1 is the scenario's start weekday. On weekends: no market sales, no central bank drawing, maturities move to Monday; withdrawals come in at 30% of a business day and are paid from cash only, the rest is "requested, not yet settled" until Monday. Nobody defaults on a weekend. Moratorium and sale settlement count business days. Switch: `weekend_rules_on`.
- **The queue is visible**: in bank management's and the resolution authority's figures, in the timeline and in the liquidity need at the end of a run (now projected outflows plus the queue, less remaining liquidity).
- **Intraday view**: each day's outflow allocated over four time blocks with fixed shares, in the timeline's day panel and in bank management's injects. Labelled as an allocation.
- **Script fixes**: notifications that arrive together are one inject; a stage notification carries the figures that triggered it, of that day, and no older table; notifications flow bank -> competent authority -> others (18:00, 18:30, 19:00); deadlines are those of the branch being read (the earlier one in the shared part); six ambient injects from `config/script.yaml`, no model effect, can be switched off.
- **Showcase**: the app, the guided demo and `--showcase` open on the mid-size retail bank in the geopolitical shock, from a Wednesday. Charts label weekdays and shade weekends. The contagion tab is hidden (engine code kept).
- Faster uncertainty runs (the result table is built only when needed). `tests/`: 1054 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Showcase pack: `.venv/bin/python -m engine.run --showcase --export-dir exports`

## Acceptance output (phase C1)
Without decisions. R/E/F = day of recovery, early intervention, likely-to-fail flag; then outcome and deposits lost.

| Bank | Fast run | Slow burn | Cyber outage | Geopolitical (from Wed) |
|---|---|---|---|---|
| Universal | 1/1/1, default day 5, 57% | 7/-/-, under stress, 25% | 3/4/4, under stress, 28% | 2/3/3, under stress, 58% |
| Retail | 1/1/1, default day 5, 41% | 11/-/-, under stress, 14% | 4/-/-, under stress, 14% | 2/6/6, under stress, 39% |
| Digital | 1/1/1, default day 2, 29% | 9/-/-, under stress, 18% | 3/4/4, under stress, 19% | 2/3/3, default day 9, 36% |
| Specialised | 1/1/1, default day 4, 62% | 6/-/-, under stress, 29% | 2/-/-, under stress, 34% | 2/3/3, default day 10, 62% |

Showcase (retail, geopolitical): recovery Thu day 2; first weekend days 4-5 with 0.83 requested and not settled; likely to fail Mon day 6; one day of liquidity left on Fri day 10; FOLTF declared that evening (resolution weekend); tool applied Mon day 13. 32.7% of deposits lost, 0.99 requested and not settled, liquidity need 1.94. Branch B: liquidation, payout 22.9.
- `pytest -q` -> `1054 passed`. "Higher severity never defaults later" holds with the calendar on, so no exception was needed. App checked in the browser.

## Deviations and choices (approved 2026-11-12)
- Slow-burn severity re-set from 0.3 to 0.38 to keep its agreed target under weekend rules (docs/CALIBRATION.md, section 11).
- The geopolitical shock starts on a Wednesday; all other presets on a Monday.
- The capital projection behind the likely-to-fail flag ignores the calendar.
- The guided demo's fifth step is now "change a decision", not "move a slider".

## Open questions
- Resolution is still a label: capital need, loss allocation and the resolution weekend injects are phase C2. Legal loss-absorption thresholds are not modelled (stated in the facilitator pack).
- Ambient injects, clock times, information sets and all script wording are drafts.
- A 500-run pass on a 30-day run takes about 1 s. Screenshots in docs/screenshots/ predate phases A to C1.
