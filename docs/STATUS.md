# STATUS

Last completed: **SPEC_V3 phase H, part a - Live engine and control console** (2026-11-13). Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, SPEC_V3 phase G. Next: phase H part b (players' pages, save, reload and replay, acceptance run), after review.

## What was built in H-a
- **`engine/live.py`**, pure: a live exercise is a set-up plus a log, and the state is what replaying the log gives. Every entry carries the real time given by the caller and the simulated moment it led to. The engine never reads the clock.
- **Simulated clock** stepped by the control team: next inject time, close of the day, or a jump to the next milestone (stage change, decision opening, end).
- **Path.** The pre-planned path, changed by what the players decide. A decision recorded during day N is taken at the close of day N. A planned decision not taken by the close of its day drops out and can still be taken later. A linear exercise keeps its path and releases a course correction when the players deviate.
- **Release.** An inject goes to its recipient when the clock reaches it, as it stands on the path known then; injects already handed out are never rewritten. What a player should pass on is released only by the control team, or marked as passed on.
- **Also:** ad hoc injects (free text or library), pause and resume, undo of the last entry (both stay in the log), real time taken per decision with pauses excluded, "taken by the control team" flag.
- **Control console** page in the app: start, clock, open decisions, pending notifications, ad hoc inject, full-information state, what comes next, what each recipient has received, log.

## How to run
- App: `./start.sh`, page "Control console" | Tests: `.venv/bin/pytest -q`

## Output (H-a; the phase H acceptance run comes with part b)
Showcase entered live as planned: 42 log entries, 13 simulated days, outcome bail-in, and every recipient gets exactly the injects of the pre-planned branch. With liquidation chosen at the close of day 12 instead: outcome liquidation, earlier injects unchanged. With FoLtF not declared on day 10: no declaration on the path from day 11; declared on day 11, in effect day 12.
- `pytest -q` -> `1345 passed`, including: nothing is released before its time; a recipient only gets its own injects and figures; replaying the log gives the same exercise; undo restores the previous state exactly.

## Deviations and choices
- "Recomputes the path from that point" is done by re-running the whole deterministic simulation with the decisions so far: the past is identical by construction.
- Until the players decide, the path assumes the planned decisions still ahead. Without this the resolution-period injects (valuation, loss allocation) could not exist before the tool is chosen.
- If the players choose another tool than planned, the loss allocation they already received is the planned tool's and is not re-sent. The control team can send an ad hoc inject.
- Recording the option that means "no decision yet" is logged and timed but leaves the decision open.
- A library inject with resolution figures can be sent ad hoc only once FoLtF is in effect; the rating downgrade cannot be sent ad hoc because it changes the run.
- The console keeps the exercise in the browser session: closing the tab loses it until part b adds saving.

## Open questions
- Replay needs the same config files as the original run; should a saved exercise carry a copy of the config (part b)?
- Should the console show a real-time countdown per session from the session plan?
- From phase G: who relays to the deposit guarantee scheme; wording needs a practitioner's review; screenshots are out of date.
