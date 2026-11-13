# STATUS

Last completed: **SPEC_V3 phase G, part a - Handbook language and scenarios** (2026-11-13). Done before: M0-M6 and SPEC_V2 phases A, B, B2, D, C1, C2. Next: phase G part b (control team materials), after review; then part c (set-up documents).

## What was built in G-a
- **G1 vocabulary.** "Facilitator" is now "control team", "participants" are "players", "FOLTF" is spelled "FoLtF", and the control team's timeline is the "simulation operational timeline", in the app, the exported documents, config, CLI and code. Pack folders are `players/` and `control_team/`.
- **G9 SIMULATION label.** Every exported Word file carries "SIMULATION - exercise material, not a real event" in the header and footer of every page and under the title; every data file has `SIMULATION_` in its name.
- **G2 taxonomy and designer.** Each scenario has a type of issue, systemic relevance and speed (Handbook par. 115). A "Scenario designer" in the sidebar filters the scenarios by them. New solvency-led scenario "Loss revealed": a news event can now write off part of the loan book against CET1.
- **G3 linear or dynamic.** A switch in the Exercise script tab and `--mode`. Dynamic: two branches with branch codes. Linear: one fixed path, decisions put to the players but without branch codes. Every document states the type.
- `tests/`: 1262 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Linear showcase: `.venv/bin/python -m engine.run --showcase --mode linear --export-dir exports`

## Acceptance output (G-a; the phase G acceptance pack comes with part c)
Mapping of scenarios: fast run = liquidity, fast burning; slow burn = liquidity, slow burning; cyber outage = operational, slow burning; geopolitical = liquidity, slow burning (market-wide trigger, one bank modelled); loss revealed = solvency, rapid solvency issue. All idiosyncratic.

"Loss revealed" without decisions (R/E/F = day of recovery, early intervention, flag):

| Bank | Stages | Outcome at day 30 | Deposits lost | CET1 ratio at the end |
|---|---|---|---|---|
| Universal | 3/3/3 | under stress | 48% | 2.0% |
| Retail | 3/3/3 | under stress | 28% | 8.0% |
| Digital | 3/3/3 | under stress | 34% | 4.2% |
| Specialised | 3/3/11 | under stress | 60% | 10.1% |

No stage is reached before the loss; early intervention is triggered by capital on day 3; ELA is unavailable below the requirement. The other 16 pairs are unchanged from phase C1.
- `pytest -q` -> `1262 passed`, including "no exported document without the SIMULATION label" in both exercise types and the four domain checks on the new scenario.

## Deviations and choices (approved 2026-11-13)
- The designer filters presets; it does not generate scenarios. System-wide is shown as not available until several banks are modelled (phase F).
- The app's Timeline tab keeps its name; the Handbook term is used in the documents.
- Config and code still call the five recipients "actors". Bank management moves to a control-team role card in part b, which is when the split into players and simulated stakeholders is made.

## Open questions
- In "Loss revealed" no bank defaults on payments within 30 days: the failure is on capital, so the outcome depends on when FoLtF is declared.
- Players' documents still hold all injects of a player in one file; inject sheets, expected responses and the main events list are part b.
- Wording of scenarios, injects and resolution texts is a draft. Screenshots in docs/screenshots/ are out of date.
