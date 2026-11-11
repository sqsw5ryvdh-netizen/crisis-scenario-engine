# STATUS

Last completed: **SPEC_V2 phase D - Exercise script, rewritten** (2026-11-11). Done before: M0-M6, phases A, B and B2. Next by agreement: phase C, then E.

## What was built in phase D
- **Information sets** (`config/decisions.yaml`, `information`): for each actor, the figures it receives, how old they are, from which stage it is informed, and who tells it. Bank management and central bank: same day. Competent authority: one day old, same day once "require daily reporting" is in effect. Resolution authority: nothing before early intervention, then one day old. Deposit guarantee scheme: only once the bank is likely to fail.
- **Injects per actor** (`engine/script.py`): day, weekday, time, sender, text, the actor's own figures with their date, and for a decision the question, the options with branch codes, the deadline, who to consult and who to inform.
- **Two branches in one pack**: each actor's document has a shared part, then one section per branch. Participant documents show branch codes only, not outcomes or path names.
- **Facilitator pack**: scorecards side by side, who sees what and how late, full-information timeline per branch, decisions taken, every decision point with where each option leads, coordination steps, debrief questions.
- **Export** (`engine/export.py`): one zip with five participant documents and the facilitator pack (.docx) and five data files (.csv). App: the Exercise script tab previews one actor and has one download. CLI: `--export-dir`, `--compare-path`.
- The decision log now holds the figures the actor had (delayed); an actor cannot decide before it is informed.
- Removed: the old single script, `config/injects.yaml`, `engine/injects.py`, their tests. New: `config/script.yaml`. `tests/`: 898 tests.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q`
- Pack: `.venv/bin/python -m engine.run --bank universal --scenario fast_run --path early_foltf --export-dir exports`

## Acceptance output (phase D)
- Full export for the universal bank in the fast run, branch A "authorities act late" (payments default day 5) against branch B "early FOLTF, bail-in" (resolution day 4): 11 files in `exports/exercise_pack_universal_fast_run/` (not in git).
- Resolution authority's document: no inject about the recovery breach; on day 1 at 18:00 it is told early intervention conditions are met and holds figures as of day 0 (deposits 620.60, nothing lost) while the bank has already lost 15.2%. It never sees the liquidity buffer or the LCR proxy.
- The test that enforces this checks every figure of every actor's injects against its set and delay, and every number printed in the Word document against those figures, for the acceptance pack and for 2 banks in 4 scenarios on a second pair of paths.
- `pytest -q` -> `898 passed`. Script tab checked in the browser.

## Deviations and choices (approved 2026-11-11)
- Weekdays and times are labels only (day 1 is a Monday); no weekend rules or intraday figures before phase C.
- Delays apply to figures. Stage changes, payments default, FOLTF and resolution are notified the same day, so the one-day decision rule of phase B is unchanged.
- Deadline of a decision: the last close before the payments default of the path without decisions; without such a default, 2 days after it opens.
- For an option not followed on a branch, "where it leads" is a run with that option taken at the first possible close and nothing decided afterwards.

## Open questions
- All wording, coordination steps and debrief questions are my draft, without legal references; a practitioner should review them.
- Information sets are not `value/unit/source` parameters: they are listed, flagged ASSUMPTION, on the Assumptions page.
- In the fast run three stage notifications arrive together on day 1, each with the same figures.
- A deadline is relative to the no-decision path, so it can be later than useful on a branch where another decision already changed the course.
- Screenshots in docs/screenshots/ predate phases A to D. The contagion tab ignores decisions (phase F).
