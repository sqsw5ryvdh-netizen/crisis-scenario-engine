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
5. **Move a slider.** Lower Uninsured deposits from 45% to 20%: the payments default moves
   from day 2 to day 4, and the line under the figure states the change.
6. **Export the exercise script.** Download it as a Word document, with the injects and
   the daily data as CSV files.

Outside the guide, the app recalculates on every change. The other tabs show how each
day's outflow was funded, the spread of failure days over 500 runs with uncertain
parameters, and contagion to a second bank. The Assumptions page lists every parameter
with its value and source.

## Command line

```bash
.venv/bin/python -m engine.run --bank digital --scenario fast_run
```

This prints the daily table. Add `--runs 500` for the uncertainty ranges and
`--export-dir exports` to write the script and the data files.

## Tests

```bash
.venv/bin/pytest -q
```

## Layout

- `engine/`: the model, in pure Python (simulation, Monte Carlo, contagion, exercise script, export).
- `config/`: every parameter, bank, scenario and script template, each with a unit and a source.
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
- **"Likely to fail" is a warning computed by the model**, not a decision by an authority.
  Decisions and their effects come in phase B of `docs/SPEC_V2.md`.
- **Daily steps only.** A run that happens within hours is shown as one day.
- **Contagion is one-way** and works through news only: the second bank never affects the first.
- **The wording of the exercise script is a draft** and contains no legal references.

## Data

The raw EBA files are not in the repository. `docs/CALIBRATION.md` names each file, its
checksum and the columns used; place them in `data/raw/TE2025/` to re-run the calibration.
