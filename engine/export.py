"""Export of the exercise pack (SPEC_V2 phase D): one Word document per actor, the
facilitator pack and the data files, as bytes and as one zip.

Nothing here reads or writes files; the caller (CLI or app) does.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
from docx import Document

from engine.decisions import OUTCOME_LABELS, actor_label, decision_log_rows, scorecard_rows
from engine.schema import WEEKDAYS, DecisionsConfig, ScriptConfig
from engine.script import ExercisePack, Inject

TIMELINE_COLUMNS = {
    "day": "Day",
    "status": "Stage",
    "cum_deposit_loss_pct": "Deposits lost (%)",
    "outflow_total": "Outflow",
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


def _start(title: str, subtitle: str, script: ScriptConfig):
    document = Document()
    document.core_properties.title = title
    document.core_properties.author = "Crisis Scenario Engine"
    document.add_heading(title, level=0)
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


def _write_inject(document, pack: ExercisePack, inject: Inject) -> None:
    document.add_heading(f"{day_label(pack, inject.day)}, {inject.time} - {inject.title}", level=3)
    _labelled(document, "From", inject.sender)
    document.add_paragraph(inject.text)
    if inject.figures:
        as_of = inject.figures[0].as_of_day
        document.add_paragraph(f"Your figures, as of the close of day {as_of}:")
        _table(
            document,
            ["Figure", "Value"],
            [[figure.label, figure.text[len(figure.label) + 1:]] for figure in inject.figures],
        )
    due = inject.decision_due
    if due is not None:
        _labelled(document, "Question", due.question)
        _table(
            document,
            ["Option", "Branch code"],
            [[option.name, option.branch_code] for option in due.options],
        )
        weekday = weekday_of(pack, due.deadline_day)
        _labelled(
            document, "Decide by",
            f"the close of day {due.deadline_day}" + (f" ({weekday})" if weekday else ""),
        )
        _labelled(document, "Consult first", ", ".join(due.consult) or "nobody")
        _labelled(document, "Inform afterwards", ", ".join(due.inform) or "nobody")
        document.add_paragraph(
            "Tell the facilitator the branch code of your choice. A decision is in effect "
            "the day after it is taken."
        )


def actor_docx(pack: ExercisePack, script: ScriptConfig, actor: str) -> bytes:
    """The document handed to one actor: only what that actor knows, when it knows it."""
    document_data = next(d for d in pack.documents if d.actor == actor)
    document = _start(f"Exercise script: {document_data.name}", pack.bank_name, script)

    document.add_heading("What you see", level=1)
    for sentence in document_data.information:
        document.add_paragraph(sentence, style="List Bullet")
    document.add_paragraph(
        "Times of day are labels. The figures you are given are the only figures you have: "
        "other participants may hold more recent ones."
    )

    heading = "Shared part" if document_data.branches else "Injects"
    document.add_heading(heading, level=1)
    if document_data.branches:
        document.add_paragraph(
            "These injects are the same whichever way the exercise continues. The facilitator "
            "tells you which branch to read afterwards."
        )
    for inject in document_data.shared:
        _write_inject(document, pack, inject)
    for title, injects in document_data.branches:
        document.add_page_break()
        document.add_heading(title, level=1)
        if not injects:
            document.add_paragraph("You receive nothing further on this branch.")
        for inject in injects:
            _write_inject(document, pack, inject)
    _glossary(document, script)
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
            f"{row['liquidity_buffer']:.2f}",
            "no outflow" if survival == float("inf") else f"{survival:.1f}",
            f"{100 * row['cet1_ratio']:.1f}",
            f"{row['ela_funding']:.2f}",
            " | ".join(events.get(day, [])),
        ])
    return rows


def facilitator_docx(pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig) -> bytes:
    """Full information: both branches, every decision point and where each option leads."""
    document = _start(
        "Facilitator pack", f"{pack.bank_name} - {pack.scenario_name}", script
    )
    document.add_paragraph(
        "For the facilitator only. Participants' documents show each actor's own figures, "
        "delayed as set out below, and branch codes without outcomes."
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
        "Delays are in whole days. Stage changes, a payments default, the FOLTF declaration "
        "and resolution reach every informed actor the same day; only the figures are delayed."
    )

    for branch in pack.branches:
        document.add_page_break()
        document.add_heading(f"Branch {branch.label}: {branch.name}", level=1)
        document.add_paragraph(f"Outcome: {OUTCOME_LABELS[branch.result.outcome]}.")
        document.add_heading("Full-information timeline", level=2)
        _table(
            document,
            [*TIMELINE_COLUMNS.values(), "Events"],
            _timeline_rows(pack, branch),
        )
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
    document.add_heading("Questions for the debrief", level=1)
    for question in script.debrief:
        document.add_paragraph(question, style="List Number")
    _glossary(document, script)
    return _save(document)


# --------------------------------------------------------------------------- data


def injects_frame(pack: ExercisePack) -> pd.DataFrame:
    """Every inject of every actor on every branch (facilitator data)."""
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
                        "from": inject.sender,
                        "text": inject.text,
                        "figures_as_of_day": inject.figures[0].as_of_day if inject.figures else "",
                        "figures": "; ".join(figure.text for figure in inject.figures),
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
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig
) -> dict[str, bytes]:
    """All files of the pack: path inside the zip -> content."""
    files = {
        f"participants/{document.actor}.docx": actor_docx(pack, script, document.actor)
        for document in pack.documents
    }
    files["facilitator/facilitator_pack.docx"] = facilitator_docx(pack, script, decisions)
    files["facilitator/data/injects.csv"] = _csv(injects_frame(pack))
    files["facilitator/data/decision_points.csv"] = _csv(decision_points_frame(pack))
    log_rows = [
        {"branch": branch.label, **row}
        for branch in pack.branches
        for row in decision_log_rows(branch.result.decision_log, pack.unit)
    ]
    files["facilitator/data/decision_log.csv"] = _csv(pd.DataFrame(log_rows))
    for branch in pack.branches:
        files[f"facilitator/data/daily_table_branch_{branch.label}.csv"] = _csv(branch.result.table)
    return files


def pack_to_zip(pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in pack_files(pack, script, decisions).items():
            # A fixed timestamp keeps the zip identical for identical inputs.
            archive.writestr(zipfile.ZipInfo(name, date_time=(2028, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def daily_table_to_csv(result) -> bytes:
    return result.table.to_csv(index=False).encode("utf-8")
