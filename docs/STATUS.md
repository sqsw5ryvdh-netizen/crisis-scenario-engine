# STATUS

Last completed: **SPEC_V2 phase C2 - Resolution** (2026-11-13). Done before: M0-M6, phases A, B, B2, D and C1. SPEC_V2 phases E and F are folded into docs/SPEC_V3.md. Next: SPEC_V3 phase G (planned, not started).

## What was built in phase C2
- **Liability stack** per archetype (`resolution` in `config/banks.yaml`): AT1 and Tier 2, senior non-preferred debt, and the MREL requirement; senior preferred debt, uncovered and covered deposits follow from the balance sheet. AT1 and Tier 2 are calibrated on the EBA file for the universal and retail archetypes; the rest is ASSUMPTION.
- **Resolution block** (`engine/resolution.py`), computed after a run that ends with a tool: losses booked before FOLTF, valuation write-down, CET1 after it, the need (losses beyond CET1 plus recapitalisation to the requirement), who bears it in order, any shortfall, MREL requirement and available MREL, liquidity need with the withdrawals not yet settled. Liquidation: asset value, what each class of claim recovers, DGS payout and recovery.
- **Write-down picked by the control team**: low 2%, central 4%, high 8% of the loan book (liquidation haircut 10%, 20%, 35%), all ASSUMPTION. **Breakpoint**: the write-down at which available MREL is no longer enough.
- **Resolution period in the script**: after FOLTF, injects on the provisional valuation, who bears the losses, communication before reopening and liquidity at reopening, each to its recipients. The facilitator pack shows the summary at the three levels with the breakpoint and states that legal loss-absorption thresholds are not modelled.
- App: resolution summary for two paths in the Decisions tab, with a level selector. CLI: `--writedown`. `tests/`: 1206 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Showcase: `.venv/bin/python -m engine.run --showcase --writedown high --export-dir exports`

## Acceptance output (phase C2)
Showcase: retail bank, geopolitical shock from a Wednesday, FOLTF after the close of Friday day 10 (resolution weekend), tool on Monday day 13. CET1 at FOLTF 3.77, losses booked 0.74, MREL requirement 5.70, available MREL 5.57, liquidity need 1.94 of which 0.99 not yet settled.

| Bail-in | Low (2%) | Central (4%) | High (8%) |
|---|---|---|---|
| Write-down | 0.82 | 1.65 | 3.30 |
| Need beyond CET1 | 0.00 | 0.27 | 1.82 |
| AT1 and Tier 2 used, of 0.80 | 0.00 | 0.27 | 0.80 |
| Senior non-preferred used, of 1.00 | 0.00 | 0.00 | 1.00 |
| Senior preferred used, of 2.37 | 0.00 | 0.00 | 0.02 |
| Available MREL covers the need | yes | yes | no |

Breakpoint: 7.9% of the loan book. Liquidation branch (haircut 10/20/35%): uncovered deposits lose 0 / 0 / 1.33 of 7.95; senior preferred debt and other liabilities lose 0 / 2.68 / 7.53 of 7.53; DGS pays out 22.92 and recovers it in full.
- `pytest -q` -> `1206 passed`, with tests on stack ordering, the breakpoint and the liquidation order. App checked in the browser.

## Deviations and choices (approved 2026-11-12/13)
- Other liabilities above AT1/T2 are not bailed in; in liquidation they rank with senior preferred debt. Order used in this model, not law.
- Sale of business is computed like bail-in: no buyer and no price. The model stops when the tool is applied.
- Available MREL at FOLTF is CET1 at that day plus AT1/T2 and senior non-preferred debt; it can be below the requirement after losses.

## Open questions
- All resolution wording, the stack assumptions and the write-down levels need a practitioner's review.
- Screenshots in docs/screenshots/ predate phases A to C2. A 500-run pass on a 30-day run takes about 1 s.
