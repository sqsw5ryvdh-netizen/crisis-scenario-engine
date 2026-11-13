"""Saved exercises (SPEC_V3 phase H): one JSON file per live exercise, in `exercises/`.

The control console writes the file after every entry; the players' pages only read it.
The file holds the exercise (set-up, configuration, log), the access codes, and each player's
view as plain data, so that a player's page never has to touch another player's injects.

The codes are a deterrent against opening the wrong page by mistake. They are not security:
anyone with access to this folder or to the control console can read everything.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from engine import live
from engine.schema import PLAYERS

FOLDER = Path(os.environ.get("CRISIS_EXERCISES_DIR", Path(__file__).resolve().parent.parent / "exercises"))
PREFIX = "SIMULATION_"
CONTROL = "control_team"


def new_id() -> str:
    return secrets.token_hex(4)


def new_codes() -> dict[str, str]:
    """One access code per player page, and one for the control team to reload the exercise."""
    return {who: secrets.token_urlsafe(6) for who in (*PLAYERS, CONTROL)}


def path_of(exercise_id: str, folder: Optional[Path] = None) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8}", exercise_id or ""):
        raise ValueError("not an exercise id")
    return (folder or FOLDER) / f"{PREFIX}exercise_{exercise_id}.json"


def save(exercise_id: str, state: live.LiveState, context: live.Context, codes: dict[str, str],
         folder: Optional[Path] = None) -> Path:
    data = live.to_saved(state, context)
    data["id"] = exercise_id
    data["codes"] = codes
    data["views"] = {player: live.player_view(state, context, player) for player in PLAYERS}
    target = path_of(exercise_id, folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written to the side and moved into place, so a page never reads half a file.
    partial = target.with_suffix(".tmp")
    partial.write_text(json.dumps(data), encoding="utf-8")
    partial.replace(target)
    return target


def read(exercise_id: str, folder: Optional[Path] = None) -> Optional[dict]:
    try:
        return json.loads(path_of(exercise_id, folder).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def saved_exercises(folder: Optional[Path] = None) -> list[tuple[str, str]]:
    """(id, description) of every saved exercise, newest first."""
    out = []
    files = sorted((folder or FOLDER).glob(f"{PREFIX}exercise_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            started = data["log"][0]["real_time"].replace("T", " ")
            out.append((data["id"], f"{data['setup']['name']} (started {started}, "
                                    f"{len(data['log'])} entries)"))
        except (ValueError, KeyError, IndexError, OSError):
            continue
    return out


def player_view(exercise_id: str, player: str, code: str,
                folder: Optional[Path] = None) -> Optional[dict]:
    """A player's view, if the exercise exists and the code is that player's; else None."""
    data = read(exercise_id, folder)
    if data is None or player not in PLAYERS:
        return None
    expected = data.get("codes", {}).get(player, "")
    if not expected or not secrets.compare_digest(expected, code or ""):
        return None
    return data["views"][player]


def load(exercise_id: str, code: str, folder: Optional[Path] = None):
    """(state, context, codes) for the control team, or None if the code is wrong."""
    data = read(exercise_id, folder)
    if data is None:
        return None
    expected = data.get("codes", {}).get(CONTROL, "")
    if not expected or not secrets.compare_digest(expected, code or ""):
        return None
    state, context = live.from_saved(data)
    return state, context, data["codes"]
