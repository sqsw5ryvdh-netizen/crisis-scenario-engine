# STATUS

Last completed: **Checkpoint before phase E** (2026-11-13): screenshots, Annex 3 challenge injects, `docs/LIMITS.md`. Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, SPEC_V3 phases G, H and I (the must-have phases). Next: phase E (reverse mode and design by objective), planned, waiting for approval.

## What was done in the checkpoint
- **Screenshots** in `docs/screenshots/`, seven, replacing the four old ones: simulation page (showcase), scenario designer, branch comparison, resolution summary, control console mid-exercise, a player page, first page of the closing report.
- **Challenge injects after Annex 3 of the Handbook**, all off by default:
  - (a) rating downgrade and (b) fall in share and bond prices: a news event with a panic shock and a cut in wholesale roll-over, each set in `config/model.yaml` (ASSUMPTION). A news event can now carry a `rollover_cut`.
  - (c) bank executives address the media without telling the authorities: reaches every player, including those not yet informed about the bank.
  - (d) the bank's notification arrives incomplete (the competent authority gets only the first figure) or at the wrong player (the central bank gets it; the competent authority's copy becomes a fallback inject).
  - (c) and (d) change who knows what and leave the daily table identical.
- **`docs/LIMITS.md`**, one page: what the model, the resolution block, the script, the live mode and the report do not cover, and the Annex 2 areas outside the tool.
- Console: the simulated clock is no longer cut off.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Market shocks: sidebar of the Simulation page, or `--challenge market_prices_fall` | Information challenges: Exercise script tab, "Challenge injects"

## Output (showcase: retail bank, geopolitical shock)
| Challenge | Stages (recovery / early intervention / likely to fail) | Deposits lost, bail-in path | Outcome |
|---|---|---|---|
| none | day 2 / 6 / 6 | 32.7% | bail-in, day 13 |
| rating downgrade (day 7) | day 2 / 6 / 6 | 33.8% | bail-in, day 13 |
| share and bond prices fall (day 3) | day 2 / 3 / 3 | 35.8% | bail-in, day 13 |
| both | day 2 / 3 / 3 | 36.0% | bail-in, day 13 |

- `pytest -q` -> `1383 passed`, including the balance sheet identity and "higher severity never defaults later" with the market shocks on, and "information challenges change no figure of the run".

## Deviations and choices
- In the showcase the roll-over cut has no effect: the geopolitical scenario already closes wholesale funding. A test shows it raises wholesale outflows in other scenarios.
- The fall in prices is strong in the showcase: it brings the "likely to fail" warning forward by three days. The size is an assumption in `config/model.yaml`.
- The roll-over cut is not in the projected outflows behind the "likely to fail" warning.
- If an incomplete and a misdirected notification fall on the same day, only the first listed in the config is played.
- The closing-report screenshot is the first page as rendered by macOS Quick Look, not by Word.

## Open questions
- Should (c) and (d) be part of the hostile assumptions by default?
- From earlier phases: who relays to the deposit guarantee scheme; console behind a code; counterfactual ranges; wording needs a practitioner's review.
