# SPEC - Crisis Scenario Engine

## 1. Purpose
The EBA must run EU-wide crisis simulation exercises (new BRRD mandate, first exercise planned for 2028).
These exercises are role-plays between authorities. They already have a handbook and a document-sharing platform.
What we build is the missing quantitative piece: a tool that produces a realistic, internally consistent, day-by-day numeric crisis script for a fictitious bank.

Not in scope: a collaboration platform, a supervisory early-warning dashboard, a capital stress test.

## 2. User and demo story
User: an EBA or national-authority analyst preparing an exercise.
Demo (5 minutes): pick a bank archetype -> pick a trigger -> press run -> see the crisis unfold day by day -> move a slider (e.g. share of uninsured deposits) and see the failure day shift -> export the exercise script.

## 3. Model

### 3.1 Bank state (start of day 0)
Assets: cash and central bank reserves; HQLA Level 1; HQLA Level 2; other unencumbered central-bank-eligible collateral; illiquid assets (loans).
Liabilities: deposits in 4 categories (insured retail, uninsured retail, corporate, financial/wholesale); wholesale funding with a maturity ladder; other liabilities.
Capital: CET1, RWA, capital requirement, MREL (optional field).

Ship 4 archetypes in `config/banks.yaml`: large universal bank, mid-size retail bank, digital/online-deposit bank, specialised lender with concentrated uninsured deposits.
All amounts are fictitious but with realistic proportions.

### 3.2 Daily loop (horizon: 30 days, daily steps)
1. Run-off: for each deposit category, outflow = stock x base rate x severity x panic multiplier.
   - Panic multiplier rises with cumulative outflows and with news events (logistic curve, parameters in config).
   - Maturing wholesale funding rolls over with a probability that falls as panic rises.
2. Funding the outflow, in this order (waterfall):
   a. cash and reserves;
   b. repo/sale of HQLA with haircuts;
   c. central bank funding against eligible collateral, with haircut and a mobilisation lag in days;
   d. fire sale of illiquid assets with a discount; the loss reduces CET1.
3. Update balance sheet and indicators.
4. Check triggers and log events.

### 3.3 Daily indicators
Liquidity buffer; LCR proxy; survival horizon in days at the current outflow speed; cumulative deposit loss %; CET1 ratio; unencumbered collateral left.

### 3.4 Triggers (thresholds in config)
Recovery indicator breached -> early intervention conditions -> failing-or-likely-to-fail (liquidity exhausted, or CET1 below requirement).
At failure: compute funding need in resolution = liquidity needed to cover the next N days of projected outflows.

### 3.5 Trigger scenarios (presets in `config/scenarios.yaml`)
- Fast digital run (hours-to-days, social-media driven)
- Slow burn (weeks of confidence erosion)
- Cyber/ICT outage (payments blocked, then run when service returns)
- Geopolitical shock (market haircuts widen, wholesale funding closes)
Each preset is only a set of parameter values plus a list of dated news events.

### 3.6 Uncertainty
Run-off speeds are the weakest part of any such model. So: every key parameter has low/central/high values, and the tool shows ranges (Monte Carlo, default 500 runs) not single numbers: e.g. "failure day: 4-9, median 6".

## 4. Outputs
1. Timeline chart: liquidity buffer, deposits, CET1 ratio by day, with trigger markers and uncertainty band.
2. Waterfall table: how each day's outflow was funded.
3. Exercise script ("injects"): for each key day, what each actor sees and which decision is due. Actors: competent authority, resolution authority, central bank, deposit guarantee scheme, bank management. Generated from templates in `config/injects.yaml` filled with the simulated numbers.
4. Export: script as .docx or .pdf, data as .csv.
5. Assumptions page in the app: every parameter with value and source, ASSUMPTION ones clearly flagged.

## 5. Milestones

M0 - Skeleton. Repo structure, dependencies, config loading with pydantic validation, empty Streamlit page, pytest running.
Accept: `pytest` green; app starts.

M1 - Engine, deterministic, one bank. Sections 3.1-3.4 with central parameter values. CLI: `python -m engine.run --bank digital --scenario fast_run` prints the daily table.
Accept: the 4 sanity checks in CLAUDE.md are tests and pass; printed table for 2 bank/scenario pairs.

M2 - App. Bank and scenario selectors, sliders for the 6 most important parameters, timeline chart, waterfall table, trigger markers.
Accept: moving a slider re-runs in under 1 second; screenshot of the result.

M3 - Exercise script. Inject generator and export (docx/pdf + csv).
Accept: one exported script for the fast-run scenario, readable by a non-technical person.

M4 - Uncertainty. Monte Carlo ranges, bands on charts, failure-day distribution, assumptions page.
Accept: 500 runs in under 5 seconds; results identical with the same seed.

M5 - Calibration and contagion.
   a. Calibrate archetype proportions on public EBA data (Transparency Exercise / Pillar 3 data hub). Files go in `data/raw/`; document exactly which file and column each number comes from.
   b. Compare the model's run speed against well-documented real cases, using only figures with a verifiable public source.
   c. Simple contagion: a second bank whose panic multiplier rises when the first bank hits a trigger, scaled by similarity (same archetype/country).
Accept: a short `docs/CALIBRATION.md` with sources; contagion visible in the app.

M6 - Demo polish. Clean layout, a guided "demo mode" following the story in section 2, README with a one-command start.
Accept: a full run-through of the demo story without touching the code.

## 6. Out of scope until all milestones are done
Real bank names in the UI, user accounts, databases, cloud deployment, intraday (hourly) steps, multi-currency.
