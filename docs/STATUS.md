# STATUS

Last completed: **SPEC_V3 phase G - Speak the Handbook's language** (parts a, b, c; 2026-11-13). Done before: M0-M6 and SPEC_V2 phases A, B, B2, D, C1, C2. Next: phase H (live mode), to be planned first.

## What was built in G-c
- **G7 mock bank dossier** for the players: business model, simplified balance sheet, liability stack, prudential indicators at day 0, short economic background per scenario. Neutral names: the bank is "Bank A" in "Country A" in every document of the pack.
- **G8 session plan**: simulated days mapped to real sessions (inputs: number and hours), with breaks at the FoLtF determination and at the resolution decision, and the real minutes per simulated day.
- **G10 drafts**: SimEx Charter on the Annex 1 template (pre-filled cells marked `[DRAFT - to edit]`, the rest blank), rules of play in the Handbook's three categories, one observer sheet per team, feedback questionnaire quoting Annex 7, blank Annex 8 checklist.
- **Mapping table** of each output to the Handbook paragraph or annex, as a document in the pack.
- Pack folders: `players/` (shared in advance), `observers/`, `control_team/`.
## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Acceptance pack: `.venv/bin/python -m engine.run --showcase --export-dir exports` (options: `--sessions`, `--session-hours`, `--mode`, `--hostile`, `--challenge`)
## Acceptance output (phase G, showcase: retail bank, geopolitical shock, bail-in and liquidation branches)
29 files, every one labelled SIMULATION. Session plan for 3 sessions of 3 hours: days 0-10 (16 min per day, break at the FoLtF determination), days 11-12 (90 min, break at the resolution decision), day 13 (end).

| Output | EBA Handbook |
|---|---|
| players/mock_bank_dossier.docx | par. 167-168, 177; par. 174 |
| players/rules_of_play.docx | par. 149-155, 200 |
| players/feedback_questionnaire.docx | Annex 7; par. 216-217 |
| observers/observer_sheet_&lt;player&gt;.docx (4) | par. 99-101 |
| control_team/simex_charter_draft.docx | Annex 1 |
| control_team/session_plan.docx | par. 139-143, 195-196 |
| control_team/main_events_list.docx | par. 128, 133, 177 |
| control_team/inject_sheets/&lt;player&gt;.docx (4, the players' documents) | par. 128-132, 191-192 |
| control_team/role_cards/&lt;role&gt;.docx (5) | par. 132, 198-200 |
| control_team/control_team_pack.docx | par. 139-141, 115 |
| control_team/feedback_checklist.docx; challenge injects; linear or dynamic; SIMULATION label | Annex 8; par. 123, 129, 192; 124-127; 174 |
- `pytest -q` -> `1323 passed`, including "no document without the SIMULATION label" in both exercise types, "every output is mapped to the Handbook" and "what players get in advance gives nothing away".

## Deviations and choices
- The mapping is this project's reading of the Handbook. Annex texts were taken from the PDF in docs/reference/, not from memory; Annex 1 was checked against the page image.
- The dossier gives every player the bank's opening figures, including authorities that receive nothing during the exercise before their stage.
- Session defaults (3 sessions of 3 hours) are ASSUMPTION inputs in `config/script.yaml`. With fewer than 3 sessions one break is dropped; without FoLtF all breaks are planned pauses.
- CLI and app use the neutral bank name in documents; the app's screens keep the archetype name.

## Open questions
- Should the resolution authority relay to the deposit guarantee scheme instead of the competent authority (from G-b)?
- All wording (injects, role cards, rules of play, charter drafts) needs a practitioner's review. Screenshots in docs/screenshots/ are out of date.
