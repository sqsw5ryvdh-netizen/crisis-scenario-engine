# STATUS

Last completed: **SPEC_V3 phase H - Live (dynamic) mode** (parts a and b; 2026-11-13). Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, SPEC_V3 phase G. Next: phase I (closing report), to be planned first.

## What was built in H-b
- **Saving.** After every entry the console writes the exercise to one file in `exercises/` (git-ignored, name starts with `SIMULATION_`): set-up, a copy of the configuration it runs on, the log, the access codes and each player's view.
- **Players' pages.** One read-only page per player, opened with a link from the console. It shows only that team's injects so far, with the simulated time and the status, and updates by itself every 3 seconds. The page has no menu to the control team's pages.
- **Reload and replay.** A saved exercise is reloaded with the control team's code and continues where it stopped, on its own saved configuration. The console replays the log entry by entry. The log and the exercise can be downloaded.
- **Scripted acceptance run**: `python -m engine.live_run`, with fixed real times.
- H-a (live engine, control console) is described in the README, section "Live exercise".

## How to run
- App: `./start.sh`, page "Control console" | Tests: `.venv/bin/pytest -q` | Scripted run: `.venv/bin/python -m engine.live_run`

## Acceptance output (phase H, scripted run of the showcase)
| Day | Decision entered live | Against the plan |
|---|---|---|
| 2 | Central bank, ELA: no decision | as planned |
| 6 | Competent authority, early intervention: require daily reporting | different |
| 6 | Deposit guarantee scheme, payout preparation: none (taken by the control team) | as planned |
| 10 | Competent authority, FoLtF: declare | as planned |
| 12 | Resolution authority, tool: liquidation | different (plan: bail-in) |

Log: 58 entries (40 clock steps, 5 decisions, 1 ad hoc inject to two players, 1 pause of 46.5 real minutes at the FoLtF determination, 8 fallback injects released, 1 notification passed on by the player). Outcome: liquidation on day 13. Injects received: competent authority 18, resolution authority 19, central bank 12, deposit guarantee scheme 11.
- `pytest -q` -> `1360 passed`, including "a player's page never shows another player's figures" (checked at every fourth entry of the run and on the rendered pages), "a saved exercise reloads to exactly the same state" and "player page refuses a wrong code or another team's code".

## Deviations and choices
- No accounts, as specified. The codes in the links keep a team from opening another team's page by mistake; they are not security. Anyone who can reach the app can open the control console, and the exercise file holds everything in clear.
- A player's page lags the console by up to 3 seconds (it polls the file).
- The players' pages are for the four authorities. Bank management is played by the control team and is read on the console.
- A saved exercise carries its configuration, so later changes to `config/` do not alter a reload or replay.
- No real-time countdown per session on the console.

## Open questions
- Should the control console itself be behind the control team's code, so that a player who removes the query from the link cannot open it?
- From earlier phases: who relays to the deposit guarantee scheme; wording needs a practitioner's review; screenshots are out of date.
