# STATUS

Last completed: **SPEC_V3 phase G, part b - Control team materials** (2026-11-13). Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, and G part a. Next: phase G part c (set-up documents and the phase G acceptance pack), after review.

## What was built in G-b
- **Players and simulated stakeholders.** The players are the four authorities. Bank management is played by the control team; the engine still works out what it sees, for its role card.
- **G4 main events list** (.docx and .csv): every inject to a player with its trigger (time or milestone), who delivers it, its purpose (advance, challenge, control), the expected response and its sheet numbers. Dynamic: what one player should tell another is an expected response, with a fallback inject (Handbook par. 191). Linear: a course correction per decision (par. 126-127).
- **Inject sheets**: one inject per page per player, numbered (e.g. RA-A07), with nothing of the control team's notes on the pages handed out. They replace the players' documents.
- **G5 challenge injects**, each switched on or off: interested buyer with an assumed price (par. 192), second valuation one level worse than the first (par. 129), and under hostile assumptions (par. 123) all buyers withdrawing, a creditors' challenge and the liquidity need at reopening raised by 25%. A rating downgrade is off by default and adds a news shock.
- **G6 role cards** for bank management, independent valuer, media, finance minister and potential buyers: knows, wants, says at each stage, figures held, injects delivered.
- App: assumptions and challenge injects in the Exercise script tab, rating downgrade in the sidebar, main events list preview. CLI: `--hostile`, `--challenge KEY`.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Showcase pack: `.venv/bin/python -m engine.run --showcase --hostile --export-dir exports`

## Acceptance output (G-b; the phase G acceptance pack and mapping table come with part c)
Main events list of the showcase (retail bank, geopolitical shock, bail-in and liquidation branches):

| Pack | Injects | Advance | Challenge | Control | of which fallback | Course corrections |
|---|---|---|---|---|---|---|
| Dynamic, friendly | 46 | 22 | 12 | 12 | 8 | 0 |
| Dynamic, hostile | 50 | 22 | 16 | 12 | 8 | 0 |
| Linear, friendly | 35 | 19 | 8 | 8 | 0 | 6 |

Roles deliver: bank management 2 stage notifications, valuer 2 valuations per branch, media 4, finance minister 1, buyers 1 per branch. Second valuation: write-down 1.65 -> 3.30 EUR bn, need 0.27 -> 1.82, available MREL no longer enough. Indicative price 1.20 EUR bn. Liquidity need at reopening 1.94, hostile 2.43. With the rating downgrade deposits lost go from 32.7% to 33.8%; the outcome and its day do not change.
- `pytest -q` -> `1294 passed`, including: every inject to a player is on the list exactly once; challenge injects without a news shock leave the daily table identical; the sheets handed out hold no trigger or expected response.

## Deviations and choices
- Config and code keep the word "actors" for the five information sets; `PLAYERS` and `ROLES` make the split. A full rename was not done: it would touch every test for no change in output.
- The `players/` folder is gone until part c (dossier, rules of play). Everything is under `control_team/`.
- The three new numbers are in `config/model.yaml`, without ranges, so the sensitivity runs are unchanged.
- Sale of business is still computed like bail-in; the buyer's price changes no other figure. "All buyers withdraw" removes nothing in the model: the control team enforces it.
- A second valuation is not played when the players already get the high write-down.

## Open questions
- Is the competent authority the right holder of every relayed notification, or should the resolution authority relay to the deposit guarantee scheme?
- Role-card lines and expected responses are a draft and need a practitioner's review, like all wording.
- Screenshots in docs/screenshots/ are out of date.
