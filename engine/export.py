"""Export of the exercise pack (SPEC_V2 phase D, SPEC_V3 phase G): the inject sheets of each
player, the main events list, the role cards, the control team pack and the data files, as
bytes and as one zip.

Nothing here reads or writes files; the caller (CLI or app) does.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT

from engine.decisions import OUTCOME_LABELS, actor_label, decision_log_rows, scorecard_rows
from engine.resolution import breakpoint_text, summary_rows
from engine.schema import PLAYERS, ROLES, WEEKDAYS, DecisionsConfig, ScriptConfig
from engine.script import MODE_TEXT, ExercisePack, Inject, MainEvent, sheet_id
from engine.setup import (
    DRAFT_MARK,
    charter_rows,
    observer_sheet,
    resolve_sessions,
    rules_of_play,
    session_plans,
)

KNOWN_LIMITS = (
    "Legal loss-absorption thresholds are not modelled: the model does not test any minimum "
    "amount of losses that shareholders and creditors must bear before other funds are used.",
    "The resolution figures rest on an assumed write-down of the loan book (low, central or "
    "high) and on the order of loss absorption used in this model, which is not a statement "
    "of law. Sale of business is computed like bail-in. The price in the \"interested "
    "buyer\" inject is an assumption and changes no other figure.",
    "Challenge injects change no figure of the run, except the rating downgrade, which adds "
    "a news shock. Hostile assumptions raise the liquidity need at reopening by an assumed "
    "share; they do not change the run either.",
    "The model stops when the tool is applied: there is no run after the bank reopens.",
    "Days are whole days. Times of day are labels, and the outflow by time block is an "
    "allocation of the daily figure, not a simulation of the day.",
    "On weekends there are no market sales and no central bank drawing; withdrawals are paid "
    "from cash only and the rest waits for Monday. Nobody defaults on a weekend.",
    "All parameters behind the figures are assumptions unless the Assumptions page says "
    "otherwise. Ambient injects change no figure.",
    "Role cards and expected responses describe a plausible behaviour for the exercise, not "
    "what any real institution would do.",
    "The wording, coordination steps and deadlines are a draft without legal references.",
)

TIMELINE_COLUMNS = {
    "day": "Day",
    "status": "Stage",
    "cum_deposit_loss_pct": "Deposits lost (%)",
    "outflow_total": "Outflow",
    "queued_withdrawals": "Requested, not settled",
    "liquidity_buffer": "Liquidity buffer",
    "survival_days": "Survival (days)",
    "cet1_ratio": "CET1 ratio (%)",
    "ela_funding": "ELA outstanding",
}


def weekday_of(pack: ExercisePack, day: int) -> str:
    if day < 1:
        return ""
    return WEEKDAYS[(WEEKDAYS.index(pack.start_weekday) + day - 1) % 7]


def day_label(pack: ExercisePack, day: int) -> str:
    weekday = weekday_of(pack, day)
    return f"Day {day}, {weekday}" if weekday else f"Day {day} (before the start)"


def _table(document, header: list[str], rows: list[list[str]]) -> None:
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
    for row in rows:
        for cell, text in zip(table.add_row().cells, row):
            cell.text = str(text)


def _labelled(document, label: str, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.add_run(f"{label}: ").bold = True
    paragraph.add_run(text)


# Every exported document says it is a simulation (EBA Handbook, par. 174): Word files in
# the header and footer of every page and under the title, data files in their name.
SIMULATION_LABEL = "SIMULATION"
SIMULATION_NOTICE = "SIMULATION - exercise material, not a real event"


def _start(title: str, subtitle: str, script: ScriptConfig, mode: str = "",
           landscape: bool = False):
    document = Document()
    if landscape:
        for section in document.sections:
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = section.page_height, section.page_width
    document.core_properties.title = f"{SIMULATION_LABEL} - {title}"
    document.core_properties.author = "Crisis Scenario Engine"
    for section in document.sections:
        for part in (section.header, section.footer):
            paragraph = part.paragraphs[0]
            paragraph.text = SIMULATION_NOTICE
            paragraph.runs[0].bold = True
    document.add_heading(title, level=0)
    document.add_paragraph().add_run(SIMULATION_NOTICE).bold = True
    if mode:
        document.add_paragraph(MODE_TEXT[mode])
    document.add_paragraph(subtitle)
    document.add_paragraph().add_run(script.disclaimer).italic = True
    document.add_paragraph().add_run(script.draft_notice).italic = True
    return document


def _save(document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _glossary(document, script: ScriptConfig) -> None:
    document.add_page_break()
    document.add_heading("Glossary", level=1)
    for entry in script.glossary:
        _labelled(document, entry.term, entry.definition)


def _write_inject(document, pack: ExercisePack, inject: Inject, sheet: str = "") -> None:
    if sheet:
        document.add_paragraph().add_run(f"Sheet {sheet}").bold = True
    document.add_heading(f"{day_label(pack, inject.day)}, {inject.time} - {inject.title}", level=3)
    _labelled(document, "From", inject.sender)
    for line in inject.text.split("\n"):
        document.add_paragraph(line)
    value = lambda figure: figure.text[len(figure.label) + 1:]  # noqa: E731
    if inject.headline:
        as_of = inject.headline[0].as_of_day
        document.add_paragraph(
            f"Figures behind this notification, as of the close of day {as_of}, as passed on "
            f"by the sender:"
        )
        _table(
            document,
            ["Figure", "Value", "Threshold crossed"],
            [[figure.label, value(figure), figure.threshold_text] for figure in inject.headline],
        )
    if inject.notified:
        document.add_paragraph("What you have been told in notifications so far:")
        _table(
            document,
            ["Figure", "Value", "As of the close of"],
            [[figure.label, value(figure), f"day {figure.as_of_day}"] for figure in inject.notified],
        )
    if inject.resolution:
        document.add_paragraph(
            "Estimates. They rest on an assumed write-down of the loan book and may be revised:"
        )
        _table(
            document,
            ["Figure", "Estimate"],
            [[figure.label, value(figure)] for figure in inject.resolution],
        )
    if inject.figures:
        as_of = inject.figures[0].as_of_day
        document.add_paragraph(f"Your latest regular report, as of the close of day {as_of}:")
        _table(
            document,
            ["Figure", "Value"],
            [[figure.label, value(figure)] for figure in inject.figures],
        )
    due = inject.decision_due
    if due is not None:
        _labelled(document, "Question", due.question)
        if pack.mode == "linear":
            _table(document, ["Option"], [[option.name] for option in due.options])
        else:
            _table(
                document,
                ["Option", "Branch code"],
                [[option.name, option.branch_code] for option in due.options],
            )
        weekday = weekday_of(pack, due.deadline_day)
        _labelled(
            document, "Decide by",
            f"the close of day {due.deadline_day}" + (f" ({weekday})" if weekday else "")
            + (". The branches have different deadlines; this is the earlier one."
               if due.deadline_shared else ""),
        )
        _labelled(document, "Consult first", ", ".join(due.consult) or "nobody")
        _labelled(document, "Inform afterwards", ", ".join(due.inform) or "nobody")
        document.add_paragraph(
            "Tell the control team your choice. It is recorded, but the exercise is linear "
            "and continues on its fixed path."
            if pack.mode == "linear" else
            "Tell the control team the branch code of your choice. A decision is in effect "
            "the day after it is taken."
        )


PURPOSE_LABELS = {"advance": "Advance", "challenge": "Challenge", "control": "Control"}


def _section_label(pack: ExercisePack, section: str) -> str:
    if section:
        return f"Branch {section}"
    return "All branches" if len(pack.branches) > 1 else ""


def _recipients(script: ScriptConfig, event: MainEvent) -> str:
    return ", ".join(script.actors[actor] for actor in event.recipients)


def inject_sheets_docx(pack: ExercisePack, script: ScriptConfig, actor: str) -> bytes:
    """The inject sheets of one recipient: one inject per page, only what it knows by then.

    Kept by the control team, which hands each sheet out when its trigger is met. The first
    page is for the control team; nothing on the other pages goes beyond the recipient's own
    information.
    """
    document_data = next(d for d in pack.documents if d.actor == actor)
    document = _start(
        f"Inject sheets: {document_data.name}", pack.bank_name, script, pack.mode
    )
    document.add_heading("For the control team", level=1)
    document.add_paragraph(
        "Do not hand out this page. Each page after it is one inject for this recipient. Hand "
        "a sheet out only when its trigger on the main events list is met. A fallback sheet "
        "is handed out only if the player who should have passed the information on has not."
    )
    numbers = {
        sheet: event for event in pack.events for sheet in event.sheets
    }
    sections = [("", document_data.shared)] + [
        (title.split()[1], injects) for title, injects in document_data.branches
    ]
    index_rows = []
    for section, injects in sections:
        for position, inject in enumerate(injects):
            sheet = sheet_id(actor, section, position)
            event = numbers.get(sheet)
            index_rows.append([
                sheet,
                _section_label(pack, section),
                f"{day_label(pack, inject.day)}, {inject.time}",
                inject.title,
                "" if event is None else str(event.number),
                PURPOSE_LABELS[inject.purpose] + (" (fallback)" if inject.fallback else ""),
            ])
    _table(document, ["Sheet", "Branch", "When", "Inject", "No. on the main events list",
                      "Purpose"], index_rows)

    document.add_page_break()
    document.add_heading("Briefing sheet: what you see", level=1)
    for sentence in document_data.information:
        document.add_paragraph(sentence, style="List Bullet")
    document.add_paragraph(
        "Times of day are labels. The figures you are given are the only figures you have: "
        "other players may hold more recent ones."
    )
    for section, injects in sections:
        if section and not injects:
            document.add_page_break()
            document.add_heading(f"Branch {section}", level=1)
            document.add_paragraph("You receive nothing further on this branch.")
        for position, inject in enumerate(injects):
            document.add_page_break()
            if section:
                document.add_paragraph(f"Branch {section}")
            _write_inject(document, pack, inject, sheet_id(actor, section, position))
    _glossary(document, script)
    return _save(document)


def main_events_rows(pack: ExercisePack, script: ScriptConfig) -> list[dict[str, str]]:
    """The main events list as rows: one inject per line, in order."""
    return [
        {
            "No.": str(event.number),
            "Branch": _section_label(pack, event.section),
            "Day": day_label(pack, event.day),
            "Time": event.time,
            "Trigger": event.trigger,
            "Inject": event.title,
            "To": _recipients(script, event),
            "Delivered by": event.delivered_by,
            "Purpose": PURPOSE_LABELS[event.purpose] + (" (fallback)" if event.fallback else ""),
            "Expected response": event.expected,
            "Sheets": ", ".join(event.sheets),
        }
        for event in pack.events
    ]


def main_events_list_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """The control team's main events list (EBA Handbook, par. 133 and 177)."""
    document = _start(
        "Main events list", f"{pack.bank_name} - {pack.scenario_name}", script, pack.mode,
        landscape=True,
    )
    document.add_paragraph(
        "For the control team and the observers only. Every inject to a player is one line: "
        "when it is triggered, who delivers it, what it is for and the response to look for. "
        "Purpose: an inject advances the simulation, challenges the players, or controls the "
        "course of the exercise."
    )
    if pack.mode == "dynamic":
        document.add_paragraph(
            "What one player should tell another is not handed out by the control team. It is "
            "listed as the expected response of the player who holds the information. The "
            "lines marked \"fallback\" are used only if that player has not passed it on in time."
        )
    else:
        document.add_paragraph(
            "The exercise is linear. A \"course correction\" line is used only if the players "
            "decide otherwise than the fixed path: the control team then tells them on which "
            "basis to continue and notes the deviation for the debrief."
        )
    rows = main_events_rows(pack, script)
    header = [name for name in rows[0] if name != "Branch" or len(pack.branches) > 1]
    _table(document, header, [[row[name] for name in header] for row in rows])
    document.add_heading("Counts", level=1)
    counts = {
        purpose: sum(1 for event in pack.events if event.purpose == purpose)
        for purpose in PURPOSE_LABELS
    }
    _table(document, ["Purpose", "Injects"],
           [[PURPOSE_LABELS[purpose], str(count)] for purpose, count in counts.items()])
    return _save(document)


def role_card_docx(pack: ExercisePack, script: ScriptConfig, role: str) -> bytes:
    """The card of one stakeholder the control team plays: knows, wants, says, with figures."""
    card = next(c for c in pack.roles if c.role == role)
    document = _start(f"Role card: {card.name}", pack.bank_name, script, pack.mode)
    document.add_paragraph(
        "For the member of the control team who plays this role. Stay in the role when a "
        "player contacts you. Give no figure that is not on this card."
    )
    _labelled(document, "Played by", "_______________________")
    document.add_heading("What you know", level=1)
    for line in card.knows:
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("What you want", level=1)
    for line in card.wants:
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("What you say, and when", level=1)
    _table(document, ["Moment", "When in this exercise", "What you say"],
           [list(line) for line in card.lines])
    document.add_heading("Figures you hold", level=1)
    if not card.figures:
        document.add_paragraph(
            "None. You hold no figure of the bank. If a player gives you one, you may repeat it."
        )
    for heading, rows in card.figures:
        document.add_heading(heading, level=2)
        _table(document, ["Figure", "Value"], [list(row) for row in rows])
    document.add_heading("Injects you deliver", level=1)
    if not card.delivers:
        document.add_paragraph("None in this pack. You answer if a player contacts you.")
    else:
        _table(
            document,
            ["No.", "Branch", "When", "Inject", "To", "Sheets"],
            [[str(event.number), _section_label(pack, event.section),
              f"{day_label(pack, event.day)}, {event.time}", event.title,
              _recipients(script, event), ", ".join(event.sheets)]
             for event in card.delivers],
        )
    if role == "bank_management":
        bank = next(d for d in pack.documents if d.actor == role)
        document.add_page_break()
        document.add_heading("What you see, day by day", level=1)
        for sentence in bank.information:
            document.add_paragraph(sentence, style="List Bullet")
        for inject in bank.shared:
            _write_inject(document, pack, inject)
        for title, injects in bank.branches:
            document.add_page_break()
            document.add_heading(title, level=1)
            if not injects:
                document.add_paragraph("You see nothing further on this branch.")
            for inject in injects:
                _write_inject(document, pack, inject)
    return _save(document)


# --------------------------------------------------------------------------- set-up documents

DRAFT_NOTE = (
    "Draft generated for the design team. Everything pre-filled here is a proposal to edit, "
    "not a decision."
)


def dossier_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """The mock bank dossier, shared with the players in advance (EBA Handbook, par. 167-168,
    177): the bank as it stands before the exercise starts. No real names."""
    dossier = pack.dossier
    document = _start(f"Mock bank dossier: {dossier.bank_name}", dossier.country, script, pack.mode)
    document.add_paragraph(
        f"For the players, before the exercise. {dossier.bank_name} and {dossier.country} are "
        "fictitious; the figures are the bank's position before the first simulated day. "
        f"Amounts in {dossier.unit}."
    )
    document.add_heading("Business model", level=1)
    document.add_paragraph(f"{dossier.bank_name}: {dossier.business_model}")
    document.add_heading("Economic background", level=1)
    document.add_paragraph(dossier.background)
    document.add_heading("Simplified balance sheet", level=1)
    total = sum(amount for _, amount in dossier.assets)
    _table(document, ["Assets", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.assets]
           + [["Total assets", f"{total:.2f}"]])
    document.add_paragraph()
    _table(document, ["Liabilities and capital", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.liabilities]
           + [["Total liabilities and capital",
               f"{sum(amount for _, amount in dossier.liabilities):.2f}"]])
    document.add_heading("Liability stack", level=1)
    document.add_paragraph(
        "The layers that would absorb losses, in the order used in this exercise, which is "
        "not a statement of law. Covered deposits are protected."
    )
    _table(document, ["Layer", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.stack])
    document.add_heading("Prudential indicators", level=1)
    _table(document, ["Indicator", "Value"], [list(row) for row in dossier.indicators])
    _glossary(document, script)
    return _save(document)


def rules_of_play_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Rules of play, shared with players and observers in advance (par. 149-155)."""
    document = _start("Rules of play", pack.bank_name, script, pack.mode)
    document.add_paragraph().add_run(DRAFT_NOTE).bold = True
    document.add_paragraph(
        "For players and observers, before the exercise. The rules say what you may and may "
        "not do in the exercise. They are fixed once it starts."
    )
    for category, rules in rules_of_play(pack, script):
        document.add_heading(category, level=1)
        for rule in rules:
            document.add_paragraph(rule, style="List Number")
    document.add_heading("To be completed by the design team", level=1)
    for line in ("Channel for exchanges between players:", "How to reach the control team:",
                 "Dates and times of the sessions:", "Confidentiality and data protection:"):
        _labelled(document, line[:-1], "_______________________")
    return _save(document)


def questionnaire_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Feedback questionnaire: the questions of Annex 7 of the Handbook, as written."""
    document = _start("Feedback questionnaire", pack.bank_name, script, pack.mode)
    document.add_paragraph().add_run(DRAFT_NOTE).bold = True
    document.add_paragraph(
        "The questions below are quoted from Annex 7 of the EBA Handbook on simulation "
        "exercises for resolution authorities (EBA/REP/2025/20). Select and adapt them."
    )
    document.add_paragraph(script.setup.questionnaire_intro)
    for question in script.setup.questionnaire:
        document.add_paragraph(question, style="List Number")
    return _save(document)


def observer_sheet_docx(pack: ExercisePack, script: ScriptConfig, player: str) -> bytes:
    """What the observer of one team tracks (par. 101). Observers see ahead of the players."""
    sheet = observer_sheet(pack, script, player)
    document = _start(f"Observer sheet: {sheet.name}", pack.bank_name, script, pack.mode)
    document.add_paragraph().add_run(DRAFT_NOTE).bold = True
    document.add_paragraph(
        "For the observer who follows this team. Do not show it to the players: it lists "
        "decisions before they open. Record facts and times; do not judge."
    )
    _labelled(document, "Observer", "_______________________")
    document.add_heading("Decisions of this team", level=1)
    if sheet.decisions:
        header = ["Opens", "Decision", "Decide by", "Should consult", "Should inform",
                  "Real time taken", "Consulted and informed? Notes"]
        rows = [[*row[1:], "", ""] for row in sheet.decisions]
        if len(pack.branches) > 1:
            header, rows = ["Branch", *header], [[row[0], *rest] for row, rest in zip(sheet.decisions, rows)]
        _table(document, header, rows)
    else:
        document.add_paragraph("No decision opens to this team in this pack.")
    document.add_heading("Information this team should pass on", level=1)
    if sheet.relays:
        header = ["By", "What", "To", "Passed on in time?", "Fallback inject used?"]
        rows = [[*row[1:], "", ""] for row in sheet.relays]
        if len(pack.branches) > 1:
            header, rows = ["Branch", *header], [[row[0], *rest] for row, rest in zip(sheet.relays, rows)]
        _table(document, header, rows)
    else:
        document.add_paragraph(
            "Nothing is planned as this team's task to pass on"
            + (": the exercise is linear and the control team delivers every notification."
               if pack.mode == "linear" else ".")
        )
    document.add_heading("Also track", level=1)
    _table(document, ["What to track", "Observations"], [[track, ""] for track in sheet.tracks])
    document.add_heading("Other notes", level=1)
    for _ in range(4):
        document.add_paragraph("_" * 80)
    return _save(document)


def charter_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """SimEx Charter draft, on the template of Annex 1 of the Handbook."""
    document = _start("SimEx Charter (draft)", f"{pack.bank_name} - {pack.scenario_name}",
                      script, pack.mode)
    document.add_paragraph().add_run(DRAFT_NOTE).bold = True
    document.add_paragraph(
        "The elements and their descriptions are those of Annex 1 of the EBA Handbook "
        f"(EBA/REP/2025/20). Cells that start with [{DRAFT_MARK}] were pre-filled from the "
        "scenario; empty cells are for the design team."
    )
    _table(document, ["Element", "Description", "Concrete elements"],
           [list(row) for row in charter_rows(pack, script)])
    return _save(document)


def session_plan_docx(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: int, hours: float,
) -> bytes:
    """Simulated days mapped to real sessions (par. 139-143)."""
    document = _start("Session plan", f"{pack.bank_name} - {pack.scenario_name}", script,
                      pack.mode, landscape=True)
    document.add_paragraph().add_run(DRAFT_NOTE).bold = True
    document.add_paragraph(
        f"Input: {sessions} session{'s' if sessions > 1 else ''} of {hours:g} hours. Simulated "
        "time is accelerated: the last column gives the real time available for one "
        "simulated day, which the players should be told in advance. Breaks are placed at "
        "the FoLtF determination and at the resolution decision where the path has them "
        "and the number of sessions allows; the control team uses them to adjust the "
        "injects of the next session to what the players decided."
    )
    for heading, plan in session_plans(pack, decisions, sessions, hours):
        if heading:
            document.add_heading(heading, level=1)
        _table(
            document,
            ["Session", "Real length", "Simulated days", "Injects", "Real time per simulated day",
             "Ends with", "Date and time (to fill in)"],
            [[str(s.number), f"{hours:g} h",
              day_label(pack, s.first_day) if s.first_day == s.last_day
              else f"{day_label(pack, s.first_day)} to {day_label(pack, s.last_day)}",
              s.injects_text or str(s.injects), f"{s.minutes_per_day:.0f} min", s.ends_with, ""]
             for s in plan],
        )
        if len(plan) < sessions:
            document.add_paragraph(
                f"Only {len(plan)} of the {sessions} sessions are used: the path is too "
                "short for more."
            )
        breaks = {s.ends_with for s in plan}
        result = next(b for b in pack.branches
                      if not heading.startswith("Branch") or b.label == heading[-1]).result
        if result.foltf_day is None:
            document.add_paragraph(
                "No FoLtF is declared on this path, so the breaks are planned pauses only.")
        elif "Break at the resolution decision" not in breaks or \
                "Break at the FoLtF determination" not in breaks:
            document.add_paragraph(
                "There are too few sessions for a break at both the FoLtF determination and "
                "the resolution decision.")
    return _save(document)


# The checklist of Annex 8 of the Handbook, as a blank form.
FEEDBACK_CHECKLIST = (
    ("i) Decision on the timeline of feedback collection", ["Item", "Decision"],
     [["Date(s) and time for debrief", ""],
      ["Date(s) and time for feedback collection at the end of the exercise", ""]]),
    ("ii) Decision on the type of feedback",
     ["Type of feedback", "Description", "Present in the simulation (Yes/No)"],
     [["Hot Debriefing", "Occurs shortly after simulations", ""],
      ["Cold Debriefing", "Occurs after at least 24 hours", ""]]),
    ("iii) Decision on tools/platforms used to collect feedback",
     ["Ref", "Method", "Comments for implementation"],
     [["A", "Surveys/feedback and evaluation forms", ""],
      ["B", "Live session with the controllers", ""],
      ["C", "Automated monitoring tools", ""],
      ["D", "Direct observations from the control team", ""]]),
    ("iv) Decision on person(s) involved in collecting feedback",
     ["Task", "Person(s) responsible for delivery", "Deadline to prepare"],
     [["Preparation of the feedback templates", "", ""],
      ["Feedback collection process", "", ""],
      ["Handling and processing feedback", "", ""]]),
)


def feedback_checklist_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Checklist for feedback collection: Annex 8 of the Handbook, left blank."""
    document = _start("Checklist for feedback collection", pack.bank_name, script, pack.mode)
    document.add_paragraph(
        "Blank form on the template of Annex 8 of the EBA Handbook (EBA/REP/2025/20), for "
        "the design team to fill in. Nothing is pre-filled."
    )
    for heading, header, rows in FEEDBACK_CHECKLIST:
        document.add_heading(heading, level=1)
        _table(document, header, rows)
    return _save(document)


# Each output of the pack and the part of the EBA Handbook (EBA/REP/2025/20) it serves.
HANDBOOK_MAPPING = (
    ("players/mock_bank_dossier.docx", "Mock bank dossier, shared in advance", "par. 167-168, 177; no recognisable institution: par. 174"),
    ("players/rules_of_play.docx", "Rules of play, by the Handbook's three categories", "par. 149-155; stakeholders played: par. 200"),
    ("players/feedback_questionnaire.docx", "Feedback questionnaire", "Annex 7; par. 216-217"),
    ("observers/observer_sheet_<player>.docx", "What each observer tracks, per team", "par. 99-101"),
    ("control_team/simex_charter_draft.docx", "SimEx Charter draft", "Annex 1 (Section 2.4)"),
    ("control_team/session_plan.docx", "Simulated days mapped to sessions, accelerated time, breaks", "par. 139-143, 195-196"),
    ("control_team/main_events_list.docx", "Every inject: trigger, purpose, expected response, deliverer", "par. 128, 133, 177"),
    ("control_team/inject_sheets/<player>.docx", "Injects per player, within its information set", "par. 128-132, 191-192"),
    ("control_team/role_cards/<role>.docx", "Stakeholders played by the control team", "par. 132, 198-200"),
    ("control_team/control_team_pack.docx", "Simulation operational timeline, decision points, debrief questions", "par. 139-141; scenario type: par. 115"),
    ("Challenge injects and friendly or hostile assumptions", "Second valuation, interested buyer, buyers withdrawing", "par. 123, 129, 192"),
    ("Linear or dynamic exercise, course corrections", "Stated on every document", "par. 124-127"),
    ("control_team/feedback_checklist.docx", "Checklist for feedback collection, blank", "Annex 8"),
    ("SIMULATION label on every document and data file", "Header, footer and title; file names of data", "par. 174"),
)


def handbook_mapping_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    document = _start("Outputs and the Handbook", pack.bank_name, script, pack.mode, landscape=True)
    document.add_paragraph(
        "Each output of this pack and the paragraph or annex of the EBA Handbook on "
        "simulation exercises for resolution authorities (EBA/REP/2025/20) it serves. The "
        "mapping is this project's reading of the Handbook."
    )
    _table(document, ["Output", "What it is", "EBA Handbook"],
           [list(row) for row in HANDBOOK_MAPPING])
    return _save(document)


def _timeline_rows(pack: ExercisePack, branch) -> list[list[str]]:
    events: dict[int, list[str]] = {}
    for event in branch.result.events:
        events.setdefault(event.day, []).append(f"{event.kind.replace('_', ' ')}: {event.detail}")
    rows = []
    for _, row in branch.result.table.iterrows():
        day = int(row["day"])
        survival = row["survival_days"]
        rows.append([
            day_label(pack, day),
            str(row["status"]).replace("_", " ").replace("failed", "payments default"),
            f"{row['cum_deposit_loss_pct']:.1f}",
            f"{row['outflow_total']:.2f}",
            f"{row['queued_withdrawals']:.2f}",
            f"{row['liquidity_buffer']:.2f}",
            "no outflow" if survival == float("inf") else f"{survival:.1f}",
            f"{100 * row['cet1_ratio']:.1f}",
            f"{row['ela_funding']:.2f}",
            " | ".join(events.get(day, [])),
        ])
    return rows


def control_team_docx(pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig) -> bytes:
    """Full information: both branches, every decision point and where each option leads."""
    document = _start(
        "Control team pack", f"{pack.bank_name} - {pack.scenario_name}", script, pack.mode
    )
    if pack.taxonomy:
        document.add_paragraph(f"Scenario type (EBA Handbook, par. 115): {pack.taxonomy}.")
    document.add_paragraph(
        "For the control team only. The players are the four authorities. Their inject sheets "
        "show each player's own figures, delayed as set out below, and branch codes without "
        "outcomes. Bank management and the other stakeholders are played by the control team."
    )

    document.add_heading("Overview", level=1)
    _table(
        document,
        ["", *(f"Branch {b.label}: {b.name}" for b in pack.branches)],
        [
            [label, *(dict(scorecard_rows(b.result.scorecard, pack.unit))[label] for b in pack.branches)]
            for label, _ in scorecard_rows(pack.branches[0].result.scorecard, pack.unit)
        ],
    )
    document.add_paragraph(
        f"Day 1 is a {pack.start_weekday}. Amounts in {pack.unit}. The horizon is "
        f"{pack.horizon} days."
    )

    document.add_heading("Who sees what, and how late", level=1)
    header = list(pack.information_rows[0])
    _table(document, header, [[row[name] for name in header] for row in pack.information_rows])
    document.add_paragraph(
        "Delays are in whole days. Stage changes, a payments default, the FoLtF declaration "
        "and resolution reach every informed actor the same day; only the figures are delayed."
    )

    for branch in pack.branches:
        document.add_page_break()
        document.add_heading(f"Branch {branch.label}: {branch.name}", level=1)
        document.add_paragraph(f"Outcome: {OUTCOME_LABELS[branch.result.outcome]}.")
        document.add_heading("Simulation operational timeline (full information)", level=2)
        _table(
            document,
            [*TIMELINE_COLUMNS.values(), "Events"],
            _timeline_rows(pack, branch),
        )
        need = branch.result.funding_need
        if need is not None:
            document.add_paragraph(
                f"Liquidity need over the {need.days} days after the run ends: "
                f"{need.need:.2f} {pack.unit}. This is projected outflows of "
                f"{need.projected_outflows:.2f} plus {need.queued:.2f} requested but not yet "
                f"settled, less {need.remaining_liquidity:.2f} of remaining liquidity."
            )
        if branch.result.resolution_weekend:
            document.add_paragraph(
                "FoLtF is declared after a Friday close: the authorities have a resolution "
                "weekend before the bank would reopen on Monday."
            )
        if branch.resolution is not None:
            document.add_heading("Resolution summary", level=2)
            document.add_paragraph(
                "The valuation write-down is not known: the three columns use the low, central "
                "and high values, all assumptions. Players' documents use the "
                f"{pack.writedown_level} value. Losses fall on CET1 first, then on the layers "
                "below in the order shown, which is the order used in this model."
            )
            rows = {
                level: summary_rows(summary, pack.unit)
                for level, summary in branch.resolution.items()
            }
            _table(
                document,
                ["", "Low", "Central", "High"],
                [
                    [label, rows["low"][index][1], rows["central"][index][1], rows["high"][index][1]]
                    for index, (label, _) in enumerate(rows["central"])
                ],
            )
            document.add_paragraph(breakpoint_text(branch.resolution["central"]))
        document.add_heading("Decisions taken on this branch", level=2)
        log = decision_log_rows(branch.result.decision_log, pack.unit)
        if log:
            header = list(log[0])
            _table(document, header, [[row[name] for name in header] for row in log])
        else:
            document.add_paragraph("No decision is taken on this branch.")
        document.add_heading("Decision points and where each option leads", level=2)
        document.add_paragraph(
            "For the option the branch follows, the line shows the branch itself. For any "
            "other option it shows a run in which that option is taken at the first possible "
            "close and nothing is decided afterwards."
            + (" The exercise is linear, so these other runs are not played: they are for "
               "the debrief." if pack.mode == "linear" else "")
        )
        _table(
            document,
            ["Code", "Opens", "Decide by", "Actor", "Decision", "Option", "Followed here",
             "Leads to", "Deposits lost (%)"],
            [
                [
                    point.code,
                    day_label(pack, point.open_day),
                    day_label(pack, point.deadline_day),
                    actor_label(point.actor),
                    point.decision_name,
                    point.option_name,
                    "yes" if point.taken else "",
                    f"{OUTCOME_LABELS[point.outcome]} (day {point.end_day})",
                    f"{point.deposits_lost_pct:.1f}",
                ]
                for point in branch.decision_points
            ],
        )

    document.add_page_break()
    document.add_heading("Expected coordination steps", level=1)
    _table(
        document,
        ["Decision", "Decided by", "Consult first", "Inform afterwards"],
        [
            [
                definition.name,
                script.actors[definition.actor],
                ", ".join(script.actors[a] for a in script.coordination[key].consult) or "nobody",
                ", ".join(script.actors[a] for a in script.coordination[key].inform) or "nobody",
            ]
            for key, definition in decisions.decisions.items()
        ],
    )
    document.add_heading("Simulated stakeholders", level=1)
    document.add_paragraph(
        "The control team plays these roles; each has a role card. Decide before the "
        "exercise who plays which, and who answers if a player contacts a stakeholder that "
        "is not on this list. Tell the players in advance which stakeholders are played."
    )
    _table(
        document,
        ["Role", "Played by", "Injects it delivers"],
        [[card.name, "", str(len(card.delivers))] for card in pack.roles],
    )
    document.add_heading("Challenge injects and assumptions", level=1)
    document.add_paragraph(
        "Assumptions: " + (
            "hostile. The liquidity need at reopening is shown with a negative market "
            "reaction as well, and the hostile challenge injects are in the pack."
            if pack.hostile else
            "friendly. No challenge from creditors, no buyer withdrawing, no negative market "
            "reaction at reopening."
        )
    )
    _table(
        document,
        ["Challenge inject", "In this pack", "Changes the figures of the run"],
        [[item.title, "yes" if key in pack.challenges else "no",
          "yes: a news shock" if item.effect == "panic_shock" else "no"]
         for key, item in script.challenges.items()],
    )
    if "second_valuation" in script.challenges and pack.writedown_level == "high":
        document.add_paragraph(
            "The players already get the high write-down, so there is no worse valuation to "
            "deliver: the updated valuation is not played."
        )
    document.add_heading("Ambient injects", level=1)
    if not pack.ambient_on:
        document.add_paragraph("Ambient injects are switched off in this pack.")
    else:
        document.add_paragraph(
            "Background noise to add pressure: press, politics, other authorities. They have "
            "no effect on any figure in the model. Leave them out if they distract."
        )
        rows = [
            [f"Branch {branch.label}", day_label(pack, inject.day), inject.time, inject.title,
             script.actors[actor]]
            for branch in pack.branches
            for actor, injects in branch.injects.items() if actor in PLAYERS
            for inject in injects if inject.kind == "ambient"
        ]
        _table(document, ["Branch", "Day", "Time", "Inject", "Recipient"], rows)
    document.add_heading("Known limits", level=1)
    for limit in KNOWN_LIMITS:
        document.add_paragraph(limit, style="List Bullet")
    document.add_heading("Questions for the debrief", level=1)
    for question in script.debrief:
        document.add_paragraph(question, style="List Number")
    _glossary(document, script)
    return _save(document)


# --------------------------------------------------------------------------- data


def injects_frame(pack: ExercisePack) -> pd.DataFrame:
    """Every inject of every actor on every branch (control team data)."""
    rows = []
    for branch in pack.branches:
        for actor, injects in branch.injects.items():
            for inject in injects:
                due = inject.decision_due
                rows.append(
                    {
                        "branch": branch.label,
                        "actor": actor,
                        "day": inject.day,
                        "weekday": inject.weekday,
                        "time": inject.time,
                        "kind": inject.kind,
                        "title": inject.title,
                        "from": inject.sender,
                        "text": inject.text,
                        "notification_figures": "; ".join(
                            f"{figure.text} (threshold {figure.threshold_text}, day {figure.as_of_day})"
                            for figure in inject.headline
                        ),
                        "resolution_estimates": "; ".join(f.text for f in inject.resolution),
                        "report_as_of_day": inject.figures[0].as_of_day if inject.figures else "",
                        "report": "; ".join(figure.text for figure in inject.figures),
                        "purpose": inject.purpose,
                        "fallback": inject.fallback,
                        "trigger": inject.trigger,
                        "delivered_by": inject.delivered_by,
                        "expected_response": inject.expected,
                        "decision": due.name if due else "",
                        "options": "; ".join(f"{o.branch_code} {o.name}" for o in due.options)
                        if due else "",
                        "decide_by_day": due.deadline_day if due else "",
                        "consult": ", ".join(due.consult) if due else "",
                        "inform": ", ".join(due.inform) if due else "",
                    }
                )
    return pd.DataFrame(rows)


def decision_points_frame(pack: ExercisePack) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "branch": branch.label,
                "code": point.code,
                "actor": point.actor,
                "decision": point.decision_name,
                "option": point.option_name,
                "opens_day": point.open_day,
                "decide_by_day": point.deadline_day,
                "followed_on_this_branch": point.taken,
                "leads_to": point.outcome,
                "end_day": point.end_day,
                "deposits_lost_pct": round(point.deposits_lost_pct, 2),
                "ela_outstanding": round(point.ela_outstanding, 2),
            }
            for branch in pack.branches
            for point in branch.decision_points
        ]
    )


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def pack_files(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: Optional[int] = None, session_hours: Optional[float] = None,
) -> dict[str, bytes]:
    """All files of the pack: path inside the zip -> content.

    players/: shared with the players in advance. observers/: for the observers only.
    control_team/: everything else. sessions, session_hours: inputs of the session plan
    (default: config).
    """
    sessions, session_hours = resolve_sessions(script, sessions, session_hours)
    files = {
        "players/mock_bank_dossier.docx": dossier_docx(pack, script),
        "players/rules_of_play.docx": rules_of_play_docx(pack, script),
        "players/feedback_questionnaire.docx": questionnaire_docx(pack, script),
    }
    for player in PLAYERS:
        files[f"observers/observer_sheet_{player}.docx"] = observer_sheet_docx(pack, script, player)
    files["control_team/simex_charter_draft.docx"] = charter_docx(pack, script)
    files["control_team/session_plan.docx"] = session_plan_docx(
        pack, script, decisions, sessions, session_hours)
    files["control_team/feedback_checklist.docx"] = feedback_checklist_docx(pack, script)
    files["control_team/handbook_mapping.docx"] = handbook_mapping_docx(pack, script)
    files |= {
        f"control_team/inject_sheets/{actor}.docx": inject_sheets_docx(pack, script, actor)
        for actor in PLAYERS
    }
    files["control_team/main_events_list.docx"] = main_events_list_docx(pack, script)
    for role in ROLES:
        files[f"control_team/role_cards/{role}.docx"] = role_card_docx(pack, script, role)
    files["control_team/control_team_pack.docx"] = control_team_docx(pack, script, decisions)
    files["control_team/data/SIMULATION_main_events_list.csv"] = _csv(
        pd.DataFrame(main_events_rows(pack, script)))
    files["control_team/data/SIMULATION_injects.csv"] = _csv(injects_frame(pack))
    files["control_team/data/SIMULATION_decision_points.csv"] = _csv(decision_points_frame(pack))
    log_rows = [
        {"branch": branch.label, **row}
        for branch in pack.branches
        for row in decision_log_rows(branch.result.decision_log, pack.unit)
    ]
    files["control_team/data/SIMULATION_decision_log.csv"] = _csv(pd.DataFrame(log_rows))
    for branch in pack.branches:
        files[f"control_team/data/SIMULATION_daily_table_branch_{branch.label}.csv"] = _csv(branch.result.table)
    resolution_rows = [
        {"branch": branch.label, "level": level, "figure": label, "value": value}
        for branch in pack.branches
        if branch.resolution is not None
        for level, summary in branch.resolution.items()
        for label, value in summary_rows(summary, pack.unit)
    ]
    if resolution_rows:
        files["control_team/data/SIMULATION_resolution_summary.csv"] = _csv(pd.DataFrame(resolution_rows))
    return files


def pack_to_zip(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: Optional[int] = None, session_hours: Optional[float] = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        files = pack_files(pack, script, decisions, sessions, session_hours)
        for name, content in files.items():
            # A fixed timestamp keeps the zip identical for identical inputs.
            archive.writestr(zipfile.ZipInfo(name, date_time=(2028, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def daily_table_to_csv(result) -> bytes:
    return result.table.to_csv(index=False).encode("utf-8")
