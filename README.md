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

The app opens on the showcase run, in guided demo mode: a panel at the top gives one
instruction per step and outlines the control to use. Next and Back move between steps;
Exit demo leaves the guide.

1. **Pick a bank archetype.** The demo uses the mid-size retail bank.
2. **Pick a trigger.** The demo uses the geopolitical shock, which starts on a Wednesday.
3. **Press run.** Results appear and the crisis starts playing.
4. **Watch the crisis unfold.** The timeline reveals one day at a time, with weekends
   shaded. The bank breaches a recovery indicator on Thursday (day 2), slows over the first
   weekend, is likely to fail on Monday (day 6) and has about a day of liquidity left on the
   second Friday (day 10). That evening it is declared failing or likely to fail; the
   authorities have the weekend, and the run ends on Monday (day 13).
5. **Change a decision.** In the sidebar, pick another decision path, for example
   "Authorities act late": the bank is then still open after 30 days, under stress, with no
   liquidity buffer. The Decisions tab compares the two paths.
6. **Export the exercise script.** Download the exercise pack as one zip.

Outside the guide, the app recalculates on every change. The other tabs show how each
day's outflow was funded and the spread of default days over 500 runs with uncertain
parameters. The Assumptions page lists every parameter with its value and source.

## Handbook alignment

The tool uses the terms of the EBA "Handbook on simulation exercises for resolution
authorities" (EBA/REP/2025/20, in `docs/reference/`): players, control team, design team,
observers, injects, FoLtF. Three things follow the Handbook directly so far:

- **Scenario taxonomy** (par. 115). Each scenario has a type of issue, a systemic relevance
  and a speed. The "Scenario designer" in the sidebar filters the scenarios by them. One
  scenario is solvency-led ("Loss revealed": a loss on the loan book cuts capital first and
  deposits leave afterwards); the others are liquidity-led or operational. System-wide
  scenarios are not available: only one bank is modelled.
- **Linear or dynamic** (par. 124-126). A dynamic exercise has two branches and the players'
  decisions choose between them. A linear exercise follows one fixed path; decisions are
  recorded but change nothing. Every exported document says which it is.
- **SIMULATION label** (par. 174). Every exported Word file carries it in the header and
  footer of every page, and every data file in its name.

## Calendar

Day 1 of a scenario is a weekday set in `config/scenarios.yaml` (Monday unless stated; the
geopolitical shock starts on a Wednesday). On Saturdays and Sundays there are no market
sales and no central bank drawing, and maturing wholesale funding moves to Monday.
Withdrawals continue at a reduced rate and are paid from cash only; what cash cannot cover
is shown as "requested, not yet settled" and falls due on Monday. Nobody defaults on a
weekend. The timeline's day panel also splits each day's outflow over four time blocks:
this is an allocation of the daily figure with fixed shares, not a simulation of the day.

## Decisions

A run can follow a decision path: who decides what, and from which day. Pick a ready-made
path in the sidebar ("Authorities act late", "ELA granted", "Early FoLtF, bail-in", ...) or
change single decisions in the panel below it. A decision is taken after the close of a day
and is in effect the next day. The Decisions tab compares the selected path with another
one: chart, outcome scorecard side by side, and the decision log with the figures each
actor had when it decided. Decisions and their effects are defined in
`config/decisions.yaml`.

## Exercise script

The Exercise script tab builds a pack for two branches: the path selected in the sidebar
and a second one. The players are the four authorities (competent authority, resolution
authority, central bank, deposit guarantee scheme). Bank management and the other
stakeholders are played by the control team. The zip contains, all for the control team:

- **inject sheets for each player**, one inject per page, handed out when the trigger is met.
  Each shows only what that player knows, when it knows it: the resolution authority hears
  nothing before early intervention and then gets figures that are one day old. A decision
  inject gives the options with a branch code, a deadline on that branch, and who to
  consult and inform. Shared sheets come first, then one set per branch.
- **the main events list**: every inject with its trigger (a time or a milestone), who
  delivers it, its purpose (advance, challenge, control) and the expected response. In a
  dynamic exercise, what one player should tell another is an expected response with a
  fallback inject; in a linear one the list holds the course corrections.
- **role cards** for bank management, the independent valuer, the media, the finance
  minister and potential buyers: what each knows, wants and says at each stage, with the
  figures it holds.
- **challenge injects**, switched on or off: an interested buyer, a second and worse
  valuation, and under hostile assumptions all buyers withdrawing and a challenge from
  creditors, with the liquidity need at reopening raised by an assumed share. None of
  these changes the run. A rating downgrade (sidebar, off by default) does: it adds a
  news shock. Ambient injects (press, politics, other authorities) add pressure without
  changing any figure.
- **a control team pack** with full information: both branches day by day, every decision
  point and where each option leads, the expected coordination steps and debrief questions.
- **data files** (.csv): the main events list, all injects, decision points, the decision log and the daily tables.

Who sees what is set in `config/decisions.yaml` (`information`); the wording, clock times
and coordination steps are in `config/script.yaml`. All of it is a draft.

## Resolution

When a path ends with a resolution tool or in liquidation, the tool works out what happens
to capital and creditors at the FoLtF declaration: losses booked so far, an assumed
write-down of the loan book, the capital needed to restore the requirement, and who bears
it from the liability stack (CET1, AT1 and Tier 2, senior non-preferred debt, senior
preferred debt, uncovered deposits; covered deposits are protected). In liquidation it
shows what each class of claim recovers and the payout by the deposit guarantee scheme.

The write-down is not known, so the control team picks low, central or high; all three are
assumptions. The summary also gives the breakpoint: the write-down at which available MREL
is no longer enough and senior preferred debt is reached. The exercise script continues
through the resolution period with injects on the valuation, the loss allocation,
communication before reopening and liquidity at reopening.

## Command line

```bash
.venv/bin/python -m engine.run --bank digital --scenario fast_run
```

This prints the daily table. Add `--path early_foltf` to follow a decision path,
`--runs 500` for the uncertainty ranges and `--export-dir exports` to write the exercise
pack (with `--compare-path` for the second branch, `--mode linear` for a linear exercise, `--no-ambient` to leave the ambient
injects out, `--writedown low|central|high` for the assumed write-down). `python -m engine.run --showcase --export-dir exports` exports the showcase.

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
  not been reviewed by a practitioner. Delays are whole days.
- **Resolution figures are illustrations.** They rest on an assumed write-down and on the
  order of loss absorption used in this model, which is not a statement of law. Legal
  loss-absorption thresholds are not modelled, sale of business is computed like bail-in
  with no buyer or price, and the model stops when the tool is applied.
- **Daily steps only.** A run that happens within hours is shown as one day.
- **Contagion is hidden in the app** until it is replaced by the cross-border group; its
  engine code is one-way and works through news only.

## Data

The raw EBA files are not in the repository. `docs/CALIBRATION.md` names each file, its
checksum and the columns used; place them in `data/raw/TE2025/` to re-run the calibration.
