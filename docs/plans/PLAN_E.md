# PLAN E - Reverse mode and design by objective

Plan for SPEC_V2 phase E and SPEC_V3 phase E. Written before any code. Status: **waiting for review**.

## What the specs ask
- SPEC_V2 E: a target outcome goes in; a solver searches severity, news shocks and archetype speed within their low and high values and returns the matching parameter set, or the closest one with the gap stated. A plausibility indicator shows how far each solved parameter is from its central value. The Monte Carlo tab is renamed "Sensitivity ranges" and says that the ranges are assumed intervals, not probabilities. Accept: three targets solved in under 10 seconds each; one infeasible target reported as infeasible.
- SPEC_V3 E: "design by objective": the design team picks what it wants to exercise and the tool proposes the bank, scenario and timing in which that decision is hardest. A decision map shows the tree of outcomes over all option combinations of the key decisions. Accept: three objectives solved, each with the proposed scenario and one paragraph on why the decision is hard there.

## Two findings that shape the plan
1. **Annex 2 does not contain the spec's examples.** The spec says the objectives come "from the Handbook's Annex 2 list of areas to be tested, as far as the model covers them, e.g. FoLtF determination, liquidity in resolution, tool choice, payout readiness". Annex 2 lists the resolution authority's own tasks (see `docs/LIMITS.md`); FoLtF determination, liquidity in resolution and payout readiness are not in it. The model touches three Annex 2 areas in part: the valuation report, the resolution conditions, and write-down and bail-in.
2. **Two of the three search parameters have no range.** Scenario severity has low and high values. News shocks and the archetype speed factor have none. The solver needs them; they are new assumptions.

## Part E-a: reverse mode
1. **`engine/reverse.py`**, pure, no new dependency.
   - `Target`: day of the "likely to fail" flag, cause (liquidity or capital), band for deposits lost by that day, optional weekday.
   - Search variables: scenario severity, one scale factor on all news shocks of the scenario, the archetype's run-off speed factor. Each within its low and high values.
   - Method: a coarse grid over the three variables, then a refinement around the best points. Deterministic: no random numbers, so the same target always gives the same answer. About 3 ms per run, so some 2,000 runs fit well under 10 seconds.
   - Result: the matching set closest to the central values, or, if nothing matches, the closest miss with each gap stated (days, cause, percentage points of deposits) and the word "infeasible".
   - Plausibility: for each solved parameter, its distance from the central value as a share of the distance to the bound (0 = central, 1 = at the bound).
2. **Config**: two ranges in `config/model.yaml`, both `ASSUMPTION`: the news-shock scale and the range of the speed factor.
3. **App**: a "Reverse mode" section on the Design page: target inputs, the solved parameters with their plausibility bars, the resulting run, and a button that loads the solved parameters into the Design page. The Uncertainty tab becomes "Sensitivity ranges" with the statement on assumed intervals.
4. **CLI**: `python -m engine.reverse --bank ... --scenario ... --flag-day ... --cause ... --deposits-lost 20 30`.
5. **Tests**: three feasible targets solved under 10 seconds each and re-simulated to confirm the match; one infeasible target reported with its gaps; same input, same output; solved values inside their ranges; plausibility 0 for a target the central run already meets.

## Part E-b: design by objective and decision map
1. **`engine/objective.py`**, pure.
   - An objective is one decision of `config/decisions.yaml`.
   - For every bank and scenario, and for the decision taken on its opening day or one or two days later, run each option. Decisions that must come first are taken at the first possible close; all others are left undecided.
   - "Hard" has two parts. Spread: how different the outcomes are across the options (distinct outcomes, and the range of each scorecard line). No dominance: no option is at least as low as every other option on every scorecard line.
   - The proposal is the setting with the largest spread among those where no option dominates. If an option dominates everywhere, the tool says so instead of proposing.
   - One paragraph of explanation built from the numbers of the proposed setting, from a template in `config/script.yaml`.
2. **Decision map**: for a bank, a scenario and up to four decisions, every combination of options, each decision taken at its first possible close. Output: the tree as data, and a chart on the Design page (one level per decision, leaves coloured by outcome).
3. **Config**: the list of objectives in `config/script.yaml`, each with its label and the Annex 2 area it serves, or "not in Annex 2".
4. **CLI**: `python -m engine.objective --decision resolution_tool`.
5. **Tests**: three objectives solved; the proposed setting re-simulated and its "no option dominates" property checked; an objective where one option dominates reported as such; decision map leaf count equals the product of the options; every leaf equals a direct simulation of its combination.

Each part ends with tests, an update of `docs/STATUS.md`, one commit, and a stop for review.

## Questions, with proposed defaults
1. **Objectives list.** Default: the model's own decisions (FoLtF determination, ELA, tool choice, moratorium, payout preparation, early intervention), each labelled with the Annex 2 area it serves where there is one and "not in Annex 2" otherwise. Nothing is shown as an Annex 2 item unless it is one.
2. **New ranges.** Default: news shocks scale between 0.5 and 1.5 times their configured size; the speed factor between 0.75 and 1.25 times its value. Both `ASSUMPTION`.
3. **What "FoLtF on day 5" means in a target.** FoLtF is a decision, so without decisions it never happens. Default: the target is the day the model raises the "likely to fail" flag, on the path without decisions.
4. **Weekday in a target.** Default: the user gives the day number and, optionally, a weekday; the solver sets the scenario's start weekday to fit, and the weekend rules apply as usual.
5. **Matching.** Default: the flag day and the cause must match exactly and deposits lost must be inside the band. Anything else is "closest, with the gap stated".
6. **"None dominates".** This needs a direction per scorecard line. Default: lower is preferred for deposits lost, losses booked, ELA outstanding and DGS payout; the outcome label is not ranked. The page says this is an assumption of the tool.
7. **Prerequisites of an objective.** Default: decisions that must come before it are taken at the first possible close; all others are left undecided.
8. **Size of the decision map.** All nine decisions give about 7,000 combinations (roughly 20 seconds). Default: the user picks up to four decisions.
9. **Challenge injects.** Default: the solver and the objective search run without challenge injects; the two market shocks are not search variables.
10. **Where it lives in the app.** Default: both parts on the Design page, as sections below the scenario set-up, not as a new page.

## Out of scope for phase E
- Any change to the simulation model itself.
- Probabilities: the solver returns one parameter set, not a likelihood.
- Optimising over decision timing beyond the three days listed in E-b.
