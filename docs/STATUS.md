# STATUS

Last completed: **SPEC_V3 phase I - Closing report** (2026-11-13). Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, SPEC_V3 phases G and H. The must-have phases of SPEC_V3 (G, H, I) are done. Next: phase E (reverse mode and design by objective) or F (cross-border group), to be planned first.

## What was built in phase I
- **`engine/report.py`**, pure: the draft closing report of a live exercise, from its log, or of a pre-planned path.
- **Generated, facts only:** overview; timeline of decisions with the figures the deciding player held at its own reporting delay and the real minutes taken; coordination steps the script expected, each "recorded as done" or "not recorded as done"; conduct of the exercise (injects, ad hoc injects, fallbacks, pauses, undos).
- **Counterfactuals from the engine:** for each key decision, every other option on the same day, and the same option one day earlier and one day later, with everything else as recorded; plus the pre-planned path. Each gives the difference from what happened.
- **Left to people, marked "[To be completed - not generated]":** observers' notes, feedback received, lessons learned, and a blank recommendations table (assigned to, priority, to be actioned by).
- **Consultation** is now recorded with each decision on the console ("Consulted first").
- Output: Word document and two data files, all labelled SIMULATION. Console: "Download the draft closing report". CLI: `--report-dir` on `engine.live_run` and on `engine.run`.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Report of the scripted run: `.venv/bin/python -m engine.live_run --report-dir exports/report`

## Acceptance output (closing report of the phase H scripted run)
17 counterfactual lines, 16 computed and 1 stated as not possible (daily reporting one day before the decision opened). Against what happened (liquidation on day 13, 32.7% of deposits lost):

| Change of one decision | Difference from what happened |
|---|---|
| Resolution tool: bail-in instead of liquidation, day 12 | outcome "Resolution: bail-in" |
| Resolution tool: liquidation one day later, day 13 | last day 14; deposits lost 34.8% |
| FoLtF: wait instead of declare, day 10 | no FoLtF; still under stress on day 30; deposits lost 39.0%; losses booked 1.85 instead of 0.74 EUR bn |
| FoLtF: declare one day earlier, day 9 | FoLtF in effect on day 10 instead of day 11 |
| ELA: grant up to the available collateral instead of no decision, day 2 | no FoLtF (its condition is no longer met); outcome "Stabilised"; last day 30; deposits lost 39.0%; ELA outstanding 2.60 EUR bn |
| Every decision as pre-planned | outcome "Resolution: bail-in" |

Coordination: 8 consultations expected, 5 recorded as done; 9 notifications to pass on, 1 recorded as passed on by the player and 8 delivered by the control team's fallback inject. Real duration 130 minutes, of which 46 paused.
- `pytest -q` -> `1371 passed`, including "no generated sentence judges the players" (16 words rejected in the document, the counterfactual lines and the config texts) and "counterfactuals change one decision and match the engine".

## Deviations and choices
- "Not recorded as done" means only that the control team made no record; the report never says "not done".
- A counterfactual keeps the other decisions on their recorded days; one that depends on the changed decision is taken at the first close at which its condition holds.
- The report can be drawn up before the end; it then says so and gives the outcome expected on the path at that point.
- A report for a pre-planned path has no real times and no coordination steps; it is not part of the exercise pack.
- The control console is still not behind a code (open from phase H).

## Open questions
- Counterfactuals use the central parameters only. Should each also show the low to high range?
- From earlier phases: who relays to the deposit guarantee scheme; console behind a code; wording needs a practitioner's review; screenshots are out of date.
