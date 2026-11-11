"""Export of the exercise script (.docx) and of the data (.csv).

Every function returns bytes; writing them to disk is left to the caller (CLI or app).
"""

from __future__ import annotations

import io

import pandas as pd
from docx import Document

from engine.injects import ExerciseScript
from engine.model import SimulationResult


def _two_column_table(document, rows) -> None:
    table = document.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value


def _labelled(document, label: str, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.add_run(f"{label}: ").bold = True
    paragraph.add_run(text)


def script_to_docx(script: ExerciseScript) -> bytes:
    document = Document()
    document.core_properties.title = "Crisis simulation exercise script"
    document.core_properties.author = "Crisis Scenario Engine"

    document.add_heading("Crisis simulation exercise script", level=0)
    document.add_paragraph(f"{script.bank_name} - {script.scenario_name}")
    document.add_paragraph().add_run(script.disclaimer).italic = True

    document.add_heading("Summary", level=1)
    document.add_paragraph(script.summary)

    document.add_heading("Run settings", level=1)
    _two_column_table(document, script.settings)

    document.add_heading("Timeline of key days", level=1)
    timeline = [
        (f"Day {key_day.day}", "; ".join(event.title for event in key_day.events))
        for key_day in script.key_days
    ]
    _two_column_table(document, timeline)

    for key_day in script.key_days:
        document.add_page_break()
        titles = "; ".join(event.title for event in key_day.events)
        document.add_heading(f"Day {key_day.day}: {titles}", level=1)
        document.add_paragraph("Key figures at the end of the day")
        _two_column_table(document, key_day.figures)
        for event in key_day.events:
            document.add_heading(event.title, level=2)
            document.add_paragraph(event.situation)
            for inject in event.injects:
                document.add_heading(inject.actor_name, level=3)
                _labelled(document, "What you see", inject.sees)
                _labelled(document, "Decision due", inject.decision)

    document.add_page_break()
    document.add_heading("Glossary", level=1)
    for term, definition in script.glossary:
        _labelled(document, term, definition)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def injects_to_frame(script: ExerciseScript) -> pd.DataFrame:
    rows = [
        {
            "day": key_day.day,
            "event": event.kind,
            "event_title": event.title,
            "situation": event.situation,
            "actor": inject.actor_name,
            "what_you_see": inject.sees,
            "decision_due": inject.decision,
        }
        for key_day in script.key_days
        for event in key_day.events
        for inject in event.injects
    ]
    return pd.DataFrame(rows)


def injects_to_csv(script: ExerciseScript) -> bytes:
    return injects_to_frame(script).to_csv(index=False).encode("utf-8")


def daily_table_to_csv(result: SimulationResult) -> bytes:
    return result.table.to_csv(index=False).encode("utf-8")
