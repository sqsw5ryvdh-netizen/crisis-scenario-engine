# STATUS

Last completed: **Product-shape pass** (2026-11-13): navigation by the Handbook's phases, console labels, explanation of buffer rises, screenshots. No model feature was added. Done before: M0-M6, SPEC_V2 phases A, B, B2, D, C1, C2, SPEC_V3 phases G, H and I, and the checkpoint (Annex 3 challenge injects, `docs/LIMITS.md`). Next: phase E, planned in `docs/plans/PLAN_E.md`, waiting for review.

## What was done
- **Navigation follows the Handbook's phases.**
  - Design: bank, scenario designer, decision path, timeline, branch comparison, sensitivity. Parameter sliders and the random seed are under "Advanced: parameters".
  - Prepare: exercise pack, challenge injects (including the two market shocks), session plan. It uses the design set on the Design page, or the showcase if none was set.
  - Deliver: the control console and the links to the players' pages.
  - Close: the draft closing report of the exercise open on Deliver, or of a reloaded one.
  - Assumptions and limits: the parameters, and `docs/LIMITS.md` in a second tab.
- **Control team's code** in front of Deliver and Close: `console_code` in `config/script.yaml`, or the environment variable `CRISIS_CONSOLE_CODE`.
- **Console labels**: no truncated label or button; the five headline values are plain text instead of metrics; each decision has its text on one line and its controls below.
- **Why the buffer rises**: a label on the timeline and a line under it, from `engine.model.buffer_rises`. In the showcase, day 4: "+4.21: collateral mobilised at the central bank becomes usable".
- **Answers applied**: the three information challenges are off by default and part of the hostile assumptions; counterfactuals stay on central parameters; the competent authority keeps relaying to the deposit guarantee scheme.
- **Screenshots** refreshed: eight, in `docs/screenshots/`.
- **Phase plans** now go to `docs/plans/PLAN_<phase>.md` and are pushed before coding.

## How to run
- App: `./start.sh` (control team's code: see `config/script.yaml`) | Tests: `.venv/bin/pytest -q`

## Output
- Every rise of the liquidity buffer over all banks, scenarios and decision paths (165 cases) is attributed to one of three causes: central bank collateral becoming usable, ELA becoming available, proceeds of a portfolio sale.
- Hostile assumptions on the showcase now add the press conference to the buyers' withdrawal and the creditors' challenge, and make the bank's "likely to fail" notification incomplete. The misdirected notification falls on the same day and is not played.
- `pytest -q` -> `1384 passed`, including "pages follow the phases of the Handbook", "control team's pages ask for the code" and "timeline explains why the liquidity buffer rises".

## Deviations and choices
- The Deliver page keeps the address `/console`.
- The guided demo's last step sends the user to the Prepare page instead of opening a tab.
- The tab "Uncertainty" keeps its name until phase E-a renames it "Sensitivity ranges".
- The control team's code is a deterrent, stored in clear in the config; it is not security.
- A market shock switched on in Prepare changes the run shown in Design; Design says so.

## Open questions
- Should Design also show a short summary of what Prepare will build (branches, challenge injects)?
- Wording still needs a practitioner's review.
