# Crisis Scenario Engine

A prototype that writes the numbers of a bank crisis, day by day, for a fictitious bank:
a deposit run, the liquidity stress that follows, the trigger stages and the funding needed
in resolution. It is meant to feed crisis simulation exercises between authorities, and it
also produces the exercise script: what each authority sees on each key day and which
decision is due.

University project (ESCP MiM) prepared for a demonstration to the European Banking
Authority. It is not an EBA tool, not a supervisory early-warning system and not a capital
stress test. All banks are fictitious.

## Start

```bash
./start.sh
```

On first use this creates a local Python environment in `.venv`, installs the dependencies
and opens the app in the browser (http://localhost:8501). Later starts only open the app.
It needs Python 3.11 or newer and works on macOS and Linux. Windows is not covered: create
the environment by hand and run `streamlit run app/main.py`.

## The five-minute demo

The app opens in guided demo mode: a panel at the top gives one instruction per step and
outlines the control to use. Next and Back move between steps; Exit demo leaves the guide.

1. **Pick a bank archetype.** The demo uses the digital deposit bank.
2. **Pick a trigger.** The demo uses the fast digital run.
3. **Press run.** Results appear and the crisis starts playing.
4. **Watch the crisis unfold.** The timeline reveals one day at a time. The bank goes
   through recovery, early intervention and the likely-to-fail warning on day 1 and cannot
   pay its obligations on day 2. Play replays it; the day slider stops on a day.
5. **Move a slider.** Lower Uninsured deposits from 45% to 30%: the payments default moves
   from day 2 to day 5, and the line under the figure states the change.
6. **Export the exercise script.** Download the exercise pack as one zip.

Outside the guide, the app recalculates on every change. The other tabs show how each
day's outflow was funded, the spread of failure days over 500 runs with uncertain
parameters, and contagion to a second bank. The Assumptions page lists every parameter
with its value and source.

## Decisions

A run can follow a decision path: who decides what, and from which day. Pick a ready-made
path in the sidebar ("Authorities act late", "ELA granted", "Early FOLTF, bail-in", ...) or
change single decisions in the panel below it. A decision is taken after the close of a day
and is in effect the next day. The Decisions tab compares the selected path with another
one: chart, outcome scorecard side by side, and the decision log with the figures each
actor had when it decided. Decisions and their effects are defined in
`config/decisions.yaml`.

## Exercise script

The Exercise script tab builds a pack for two branches: the path selected in the sidebar
and a second one. The zip contains:

- **one Word document per actor** (competent authority, resolution authority, central bank,
  deposit guarantee scheme, bank management). Each shows only what that actor knows, when it
  knows it: the resolution authority hears nothing before early intervention and then gets
  figures that are one day old; the bank's management has same-day figures. Each inject has
  a day and time, a sender, the actor's own figures, and for a decision: the options with a
  branch code, a deadline, and who to consult and inform. A shared part comes first, then
  one section per branch.
- **a facilitator pack** with full information: both branches day by day, every decision
  point and where each option leads, the expected coordination steps and debrief questions.
- **data files** (.csv): all injects, decision points, the decision log and the daily tables.

Who sees what is set in `config/decisions.yaml` (`information`); the wording, clock times
and coordination steps are in `config/script.yaml`. All of it is a draft.

## Command line

```bash
.venv/bin/python -m engine.run --bank digital --scenario fast_run
```

This prints the daily table. Add `--path early_foltf` to follow a decision path,
`--runs 500` for the uncertainty ranges and `--export-dir exports` to write the exercise
pack (with `--compare-path` for the second branch).

## Tests

```bash
.venv/bin/pytest -q
```

## Layout

- `engine/`: the model, in pure Python (simulation, decisions, Monte Carlo, contagion, exercise script, export).
- `config/`: every parameter, bank, scenario, decision and script template, each with a unit and a source.
- `app/`: the Streamlit app. It only calls the engine.
- `scripts/calibrate_te2025.py`: rebuilds the calibrated bank proportions from the EBA file.
- `docs/SPEC.md` and `docs/SPEC_V2.md`: what the tool must do. `docs/STATUS.md`: what is built.
  `docs/CALIBRATION.md`: data sources and the comparison with real cases.
- `tests/`: the test suite, including the four domain checks (balance sheet identity, no
  negative balances, nothing changes with zero run-off, higher severity never fails later).

## Limits to keep in mind

- **Most parameters are assumptions.** All run-off speeds, haircuts and thresholds are
  marked ASSUMPTION: they have no verifiable source. The Assumptions page flags each one.
  Results are illustrations for an exercise, not estimates.
- **Only two archetypes are calibrated**, and only in part: the universal and retail banks
  use peer medians from the EBA 2025 Transparency Exercise for some balance sheet lines.
  The digital and specialised banks are invented proportions.
- **The slow-burn scenario was set on one figure of one real case** (see
  `docs/CALIBRATION.md`, section 8). It is an order-of-magnitude check, not a calibration,
  and at that pace no bank defaults within 30 days.
- **"Likely to fail" is a warning computed by the model.** The formal declaration is a
  decision on a path.
- **How a run ends is named by fixed rules.** A bank still open after 30 days is
  "stabilised" only if its warning conditions are gone, it has a liquidity buffer and
  outflows have almost stopped; otherwise it is "kept open on emergency funding" or "still
  under stress". A slow run that is still leaking on day 30 is not stabilised.
- **Only part of the deposits can leave.** Each category has a flight-prone share; the rest
  is a stable core. The shares are assumptions.
- **Decision effects are assumptions**, including how fast a scenario's pressure eases.
  Bail-in and sale of business are outcome labels only: the model does not yet compute
  losses for creditors.
- **The exercise script is a draft.** Its wording, coordination steps, deadlines and
  information delays were written for this prototype, contain no legal references and have
  not been reviewed by a practitioner. Delays are whole days and weekdays are labels only:
  there are no weekend rules or intraday figures yet.
- **Daily steps only.** A run that happens within hours is shown as one day.
- **Contagion is one-way** and works through news only: the second bank never affects the first.

## Data

The raw EBA files are not in the repository. `docs/CALIBRATION.md` names each file, its
checksum and the columns used; place them in `data/raw/TE2025/` to re-run the calibration.
