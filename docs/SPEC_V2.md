# SPEC V2 - Crisis Scenario Engine

Follows docs/SPEC.md (M0-M6 done). Written after an external review of the code at commit ad17357.
The rules in CLAUDE.md still apply. Work one phase at a time, plan first, stop after each phase.

## Why a second spec
The engine is technically sound, but the product is aimed at the wrong centre of gravity.
Authorities already have run and liquidity models. What they lack is a script generator for exercises: who knows what, when, which decision is due, and what happens next depending on that decision.
Today the model is a one-way path to failure and the script is a set of sentences with numbers filled in. V2 fixes both.

## Findings to fix (from the review)
1. 15 of 16 bank/scenario pairs fail. Nothing can stop a run once started: panic only rises.
2. The slow burn is far too fast: the universal bank loses 61% of deposits in 16 days, against about 37% in a quarter in the Credit Suisse figures already in docs/CALIBRATION.md.
3. The digital bank is the most resilient archetype in the fast run (day 10, others day 4), which contradicts the demo story.
4. Decisions in the script have no effect on the simulation.
5. Failure is declared only when payments are already unmet. In practice "likely to fail" is a forward-looking judgement by an authority.
6. All actors see the same figures at the same time.
7. Resolution is reduced to one number (funding need).

## Phase A - Model realism
- Panic can fall as well as rise: add a decay term and negative shocks (reassuring events), parameters in config.
- Split central bank funding in two facilities: ordinary operations (existing) and emergency liquidity assistance (ELA): wider collateral including part of the loan book, higher haircut, available only if granted (see phase B) and only while the bank is judged solvent.
- Replace the mechanical failure rule by two things: a "payments default" event (unmet outflows, as now) and a forward-looking flag "likely to fail" when the survival horizon is below a configurable number of days or CET1 is projected below requirement. The formal declaration becomes a decision (phase B).
- Rebuild the digital archetype so that its weakness is visible: high share of rate-sensitive online deposits with faster run-off, thin collateral. Add a per-archetype run-off speed factor instead of changing global rates.
- Re-set slow-burn severity so the universal bank's deposit loss over 30 days is of the same order as the documented Credit Suisse figures. Record in docs/CALIBRATION.md that this is an order-of-magnitude check, not an estimate.
Accept: table of all 16 pairs showing recovery/early intervention/failure day and deposit loss; at least 4 pairs survive 30 days; digital bank fails first in the fast run; all existing sanity tests still pass.

## Phase B - Decision points
- Define decisions as data in `config/decisions.yaml`. Each has: actor, earliest day or enabling condition, options, effect on the model.
- Minimum set:
  - Bank management: public statement (small negative or positive panic shock, configurable); activate recovery options (sell a portfolio: liquidity up, loss booked).
  - Competent authority: early intervention measures; declare failing or likely to fail (FOLTF).
  - Central bank: grant or refuse ELA, with amount cap.
  - Resolution authority: moratorium (suspends outflows up to 2 business days); resolution tool: bail-in, sale of business, or no public interest -> liquidation.
  - Deposit guarantee scheme: prepare payout; pay out covered deposits in liquidation.
- `simulate()` takes a decision set (which option, on which day). Default decision set = "authorities act late", reproducing a failure path.
- App: a "Decisions" panel to switch options, and a branch comparison (path A vs path B on the same chart).
Accept: for the universal bank in the fast run, show three paths with different outcomes (e.g. ELA refused -> payments default; ELA granted -> survives to weekend; early FOLTF -> orderly resolution). Tests for each decision's effect.

## Phase C - Resolution block and calendar
- Liability stack per archetype: CET1, AT1/T2, senior non-preferred, senior preferred, uncovered deposits, covered deposits. MREL requirement and available MREL as explicit fields (no longer optional).
- At FOLTF compute: losses to date, recapitalisation need to restore the capital requirement, bail-in capacity from the stack in order, shortfall if any; covered deposits and DGS payout amount in liquidation; liquidity need in resolution (existing).
- Calendar: scenario has a start weekday. Weekends: no market sales, no wholesale maturities; online deposit withdrawals continue at a configurable reduced rate. Show weekday on every chart and inject. Flag whether FOLTF falls on a "resolution weekend".
- Intraday view (approximation, not a new time step): split each day's outflow over time blocks (e.g. 09-12, 12-15, 15-18, overnight) with a configurable profile, so the script can say what is known by a given hour. State clearly in the app that this is an allocation of the daily figure.
Accept: resolution summary table for two paths (bail-in vs liquidation); a run starting on Wednesday with FOLTF reached on Friday evening; tests on stack ordering and on weekend rules.

## Phase D - Exercise script, rewritten
This is the core deliverable. Replace the current templates.
- Information asymmetry: each actor has an information set with a lag and a scope, in config. Examples: bank management sees intraday figures; competent authority sees end-of-day figures and what the bank reports; resolution authority is informed only from early intervention; DGS only when FOLTF is likely; central bank sees collateral and payment flows.
- Each inject contains: day, weekday and time; recipient; what they are told and by whom; the figures in their information set only; the decision due, with explicit options and a deadline; who must be consulted or informed first; what happens next under each option (reference to the branch).
- Facilitator pack: full-information timeline, the list of decision points, the expected coordination steps, and questions for the debrief.
- Export: one document per actor plus the facilitator pack (.docx), and the data (.csv).
- Wording stays plain and without invented legal references. Legal bases are added only if supplied by the project team.
Accept: full export for one scenario with two branches. A reader who has only the resolution authority's document must not be able to see figures the resolution authority would not have at that time. Test that enforces this.

## Phase E - Reverse mode
- Input: target outcome, e.g. "FOLTF on day 5 (Friday), cause liquidity, deposit loss between 20% and 30% by then".
- A solver searches severity, news shocks and archetype speed within their low/high ranges and returns the parameter set that matches, or the closest one with the gap stated.
- Show how far each solved parameter is from its central value, as a plausibility indicator.
- Rename the Monte Carlo tab "Sensitivity ranges" and state on it that the ranges reflect assumed parameter intervals, not probabilities.
Accept: three target outcomes solved in under 10 seconds each; one infeasible target reported as infeasible.

## Phase F - Cross-border group (replaces the contagion tab)
- Two entities: parent in country A, subsidiary in country B, each with its own balance sheet, competent authority, resolution authority and DGS.
- Intragroup liquidity transfers with a limit. New decision for the host authority: restrict transfers (ring-fencing). Confidence in one entity affects the other (reuse the contagion code).
- Script: injects for home and host authorities, with the coordination steps between them.
Accept: one run where host ring-fencing changes the outcome for the parent; per-actor documents for both countries.

## Priority if time is short
A, B and D are essential. C is needed for credibility with resolution staff. E is cheap and high value. F is optional.
