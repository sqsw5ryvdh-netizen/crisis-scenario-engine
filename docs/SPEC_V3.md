# SPEC V3 - Crisis Scenario Engine

Follows docs/SPEC_V2.md. Start V3 only after phases C1 and C2 are done and reviewed.
The rules in CLAUDE.md still apply: one phase at a time, plan first, stop after each phase.

## Why a third spec
The project cannot be validated with the EBA before the presentation. The best public proxy for what the EBA wants is its own
"Handbook on simulation exercises for resolution authorities" (EBA/REP/2025/20, July 2025).
V3 makes the tool speak the Handbook's language and produce the materials the Handbook asks a design team to prepare.

Positioning: the tool is the design team's and control team's workbench. The Handbook says set-up is the most time- and
resource-demanding phase of an exercise (one to six months or more). The tool's claim is that it cuts the time needed to produce the scenario-specific assets and lets a small team run a dynamic exercise.

Reference file: the project team will place the Handbook PDF in `docs/reference/`. Read it, including Annexes 1 to 8, before planning phase G.
Paragraph numbers below refer to it. If the PDF is missing, stop and ask for it. Do not reconstruct annex content from memory.

Scope discipline (Handbook par. 57-60 applied to ourselves):
- Must have: G, H, I.
- Nice to have: E (from SPEC_V2, extended below), F (from SPEC_V2, extended below).
- Optional, only if everything else is done and reviewed: J, K.

## Phase G - Handbook alignment
G1. Vocabulary. Use the Handbook's terms everywhere in the app, documents and code-facing labels: players, control team, observers, design team, inject, rules of play, simulation operational timeline. "Facilitator" becomes "control team".
G2. Scenario designer by the Handbook's taxonomy (par. 115-116): type of issue (liquidity, solvency, operational, combination); idiosyncratic or system-wide; speed (slow burning, rapid solvency, fast burning liquidity). Existing presets are mapped onto it.
   Gap to fill: a solvency-led scenario (a large loss is revealed, capital falls first, liquidity follows). Today every preset is liquidity-led.
G3. Linear or dynamic (par. 124-126): a switch. Linear: the path is fixed and decisions are recorded but do not change it. Dynamic: decisions change the path (what phase B built). Stated on every document.
G4. Main events list for the control team (par. 133c, 177): every inject with purpose (advance, challenge, control: par. 128), trigger (time or milestone), expected response from players, and the role-played stakeholder who delivers it (par. 132).
G5. Challenge injects taken from the Handbook's own examples, each with a model effect where relevant:
   - a second, deteriorated valuation arriving after the first (par. 129): uses the low/central/high write-down of phase C2;
   - an interested buyer appearing while resolution or liquidation is being assessed (par. 192);
   - all buyers withdrawing at the non-binding offer stage (par. 123).
   Plus a "friendly / hostile assumptions" setting (par. 123): e.g. no creditor challenge, no negative market reaction at reopening.
G6. Role cards for simulated stakeholders played by the control team (par. 198-200): independent valuer, media, finance minister, bank management, potential buyers. Each card: what this stakeholder knows, wants, and says at each stage, with the figures it would hold.
G7. Mock bank dossier, shared with players in advance (par. 167-168, 177): business model description, simplified balance sheet, liability stack, prudential indicators, short economic background. No real names; nothing that makes a real institution recognisable (par. 174).
G8. Session plan (par. 139-143): map simulated days to real sessions, with accelerated time and breaks placed at the FOLTF determination and at the resolution decision. Input: number and length of sessions.
G9. Every exported document carries a "SIMULATION" label (par. 174).
G10. Drafts generated from the Handbook's templates, pre-filled with the scenario: SimEx Charter (Annex 1), rules of play (par. 149-155), observer sheets with what to track per team (par. 101), feedback questionnaire (Annex 7). Mark every pre-filled field as a draft for the design team to edit.
Accept: for the showcase scenario, a complete pack: dossier, rules of play, charter draft, session plan, main events list, role cards, players' documents, observer sheets, questionnaire. A table mapping each output to the Handbook paragraph or annex it serves. Test: no document without the SIMULATION label.

## Phase H - Live (dynamic) mode
- A control console in the app. The control team starts the exercise, sees the full-information state, and records each decision when players take it (option chosen, real time).
- The engine recomputes the path from that point and releases the next injects per actor, respecting each actor's information set and delay.
- Ad hoc injects (par. 131): free text or picked from the library, sent to chosen actors, logged.
- Pause and resume, jump to next phase, and "control team takes the decision" when players are stuck (par. 87, 193, 195).
- Player view: one read-only page per actor showing only that actor's injects so far. Same machine or local network is enough; no accounts, no cloud.
- Everything is logged with real and simulated timestamps (par. 209: timing of output against input).
- A saved exercise can be reloaded and replayed.
Accept: a scripted run of the showcase with five decisions entered live, two of them different from the pre-planned branch, one ad hoc inject and one pause. Test that an actor's page never shows another actor's figures.

## Phase I - Closing report
- After a live or pre-planned run, generate a draft closing report (par. 221-225): overview, timeline of decisions with the figures each actor held, real time taken per decision, coordination steps recorded as done or not done.
- Counterfactuals from the engine: for each key decision, the outcome scorecard had the alternative options been taken, or the same option one day earlier or later.
- Sections left for humans, clearly marked: observers' notes, lessons learned, recommendations with owner, priority and deadline (par. 225).
Accept: closing report for the phase H scripted run, with at least three counterfactuals. No sentence that judges the players: the report states facts and differences in outcomes only.

## Phase E (extends SPEC_V2 phase E) - Reverse mode and design by objective
- Keep the reverse mode as specified in SPEC_V2.
- Add "design by objective": the design team picks what it wants to exercise (from the Handbook's Annex 2 list of areas to be tested, as far as the model covers them, e.g. FOLTF determination, liquidity in resolution, tool choice, payout readiness). The tool proposes the bank, scenario and timing in which that decision is hardest, i.e. where the options lead to clearly different outcomes and none dominates on every scorecard line.
- Decision map: for a scenario, the tree of outcomes over all option combinations of the key decisions, as a chart.
Accept: three objectives solved; for each, the proposed scenario and a one-paragraph explanation of why the decision is hard there.

## Phase F (extends SPEC_V2 phase F) - Cross-border group and system-wide option
- Cross-border group as in SPEC_V2, with the players structured as a resolution college (par. 43): home and host authorities, each with its own information set.
- System-wide variant (par. 115b): two banks under stress at once, reusing the existing contagion code, now aware of decisions. The contagion tab stays hidden until this works.
Accept: as in SPEC_V2, plus one system-wide run where an action on the first bank changes the second bank's path.

## Phase J (optional) - Case-inspired preset
- One preset inspired by a documented EU case with a cross-border dimension, built only from official public documents supplied by the project team in `docs/reference/`. Fictitious names. A short note listing what was taken from the sources and what is assumed.

## Phase K (optional) - Generated narrative text
- Only after reading Annex 6 of the Handbook (use of general-purpose AI in exercises: use cases, limitations and caveats) and reporting what it says. No decision on this phase before that report.
