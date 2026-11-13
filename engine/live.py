"""Live (dynamic) mode, SPEC_V3 phase H: an exercise run by the control team in real time.

Pure functions. A live exercise is a set-up plus an ordered log; the state is what replaying
the log gives, so a saved log always reproduces the same exercise. Nothing here reads the
clock: every entry carries the real time the caller gives it.

How it works:
- The control team steps a simulated clock (next inject time, or the close of the day).
- The path is the pre-planned one, changed by what the players decide. Decisions recorded so
  far replace the planned ones; a planned decision the players have not taken by the close of
  its day drops out. A linear exercise keeps to the planned path whatever is recorded.
- An inject is released to its recipient when the clock reaches its day and time, as it
  stands on the path known at that moment. What one player should tell another (a fallback
  inject) and a course correction are released only by the control team.
- A decision recorded during simulated day N is taken at the close of day N and is in effect
  the next day, as everywhere in the model. Recording the option that means "no decision
  yet" is logged and leaves the decision open.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional, Sequence

from engine.decisions import OPEN_OUTCOMES, Choice
from engine.schema import (
    ACTORS,
    DECISION_STAGES,
    PLAYERS,
    Bank,
    DecisionsConfig,
    ModelParams,
    Scenario,
    ScriptConfig,
)
from engine.script import (
    CONTROL,
    BranchScript,
    Inject,
    active_challenges,
    build_branch,
    scenario_with_challenges,
)

CLOSE = "24:00"  # the close of a simulated day, after every inject of that day
ENTRY_KINDS = (
    "start", "step", "jump", "decision", "adhoc", "fallback", "relay_done", "pause", "resume",
    "undo",
)
# Injects whose arrival marks a new phase: where "jump" goes.
MILESTONES = DECISION_STAGES + ("decision_due", "foltf", "resolution", "end_open")
# Injects the clock never releases by itself.
MANUAL = ("course_correction",)


class LiveError(ValueError):
    """An entry that cannot be applied to the exercise as it stands."""


@dataclass(frozen=True)
class LiveSetup:
    """Everything that fixes the exercise before it starts."""

    name: str
    bank: Bank  # as used in the documents (neutral name applied)
    scenario: Scenario  # with the challenge injects that are news events already added
    planned: tuple[Choice, ...]  # the pre-planned path
    mode: str = "dynamic"
    ambient: bool = True
    writedown_level: str = "central"
    challenges: tuple[str, ...] = ()
    hostile: bool = False


def make_setup(
    name: str,
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    planned: Sequence[Choice],
    mode: str = "dynamic",
    ambient: bool = True,
    writedown_level: str = "central",
    challenges: Optional[Sequence[str]] = None,
    hostile: bool = False,
) -> LiveSetup:
    """The set-up of a live exercise, with the same options as an exercise pack."""
    active = active_challenges(script, challenges, hostile)
    return LiveSetup(
        name=name,
        bank=bank.model_copy(update={"name": script.setup.bank_alias}),
        scenario=scenario_with_challenges(bank, params, scenario, decisions, script, active),
        planned=tuple(planned),
        mode=mode,
        ambient=ambient,
        writedown_level=writedown_level,
        challenges=active,
        hostile=hostile,
    )


@dataclass(frozen=True)
class Entry:
    """One line of the log: what the control team did, and when."""

    kind: str
    real_time: str  # ISO date and time, given by the caller
    data: dict = field(default_factory=dict)
    # Filled in when the entry is applied: the simulated moment it happened at.
    day: int = 0
    time: str = ""


@dataclass(frozen=True)
class Released:
    """An inject as a recipient received it."""

    inject: Inject
    real_time: str
    day: int  # simulated moment of release (later than the inject's own if it was held)
    time: str
    how: str = "clock"  # clock | jump | fallback | course_correction | adhoc
    note: str = ""  # control team only


@dataclass(frozen=True)
class RecordedDecision:
    decision: str
    option: str
    actor: str
    day: int  # taken at the close of this simulated day
    real_time: str
    by_control: bool  # the control team took it for the players
    applied: bool  # False: "no decision yet", or a linear exercise
    planned_option: str
    deviates: bool  # differs from the pre-planned path
    seconds_taken: Optional[float]  # real time since the decision opened, pauses excluded
    # Whom the deciding player consulted first, as the control team recorded it.
    consulted: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveState:
    setup: LiveSetup
    entries: tuple[Entry, ...] = ()  # the whole log, undone entries included
    day: int = 0
    time: str = ""
    started: bool = False
    paused: bool = False
    ended: bool = False
    decisions: tuple[RecordedDecision, ...] = ()
    released: tuple[Released, ...] = ()
    passed_on: tuple[str, ...] = ()  # keys of fallback injects the players made unnecessary
    pauses: tuple[tuple[str, str], ...] = ()  # (from, to) in real time; to = "" while paused
    branch: Optional[BranchScript] = None  # the path as known now

    @property
    def clock(self) -> tuple[int, str]:
        return (self.day, self.time)


@dataclass(frozen=True)
class Context:
    """The configuration a live exercise runs on."""

    params: ModelParams
    decisions: DecisionsConfig
    script: ScriptConfig


# --------------------------------------------------------------------------- the path


def inject_key(inject: Inject) -> str:
    """Identifies an inject across recomputations of the path."""
    return "|".join((inject.recipient, str(inject.day), inject.time, inject.kind,
                     inject.title, inject.text))


def _planned_days(setup: LiveSetup, context: Context) -> dict[str, tuple[str, int]]:
    """decision -> (option, day it is taken) on the pre-planned path."""
    branch = _branch(setup, context, setup.planned)
    return {r.decision: (r.option, r.decided_day) for r in branch.result.decision_log}


def _branch(setup: LiveSetup, context: Context, choices: Sequence[Choice]) -> BranchScript:
    return build_branch(
        "A", setup.name, setup.bank, context.params, setup.scenario, context.decisions,
        context.script, choices, setup.ambient, setup.writedown_level,
        setup.mode == "linear", setup.challenges, setup.hostile, with_points=False,
    )


def effective_choices(state: LiveState, context: Context) -> tuple[Choice, ...]:
    """The decisions the path rests on now: those recorded, plus the planned ones still ahead."""
    if state.setup.mode == "linear":
        return state.setup.planned
    taken = {d.decision: d for d in state.decisions if d.applied}
    choices = [Choice(d.decision, d.option, d.day) for d in taken.values()]
    for decision, (option, planned_day) in _planned_days(state.setup, context).items():
        if decision not in taken and planned_day >= state.day:
            choices.append(Choice(decision, option, planned_day))
    return tuple(choices)


def _is_manual(inject: Inject) -> bool:
    return inject.fallback or inject.kind in MANUAL


def _all_injects(branch: BranchScript) -> list[Inject]:
    return [inject for actor in ACTORS for inject in branch.injects[actor]]


def _moment(inject: Inject) -> tuple[int, str]:
    return (inject.day, inject.time)


def _last_moment(branch: BranchScript) -> tuple[int, str]:
    return (branch.result.end_day, CLOSE)


def next_moment(state: LiveState) -> Optional[tuple[int, str]]:
    """Where "next step" takes the clock: the next inject time or close of day; None at the end."""
    last = _last_moment(state.branch)
    moments = {_moment(i) for i in _all_injects(state.branch)}
    moments |= {(day, CLOSE) for day in range(state.day, last[0] + 1)}
    later = sorted(m for m in moments if state.clock < m <= last)
    return later[0] if later else None


def next_milestone(state: LiveState) -> Optional[tuple[int, str]]:
    """Where "jump" takes the clock: the next stage change or decision opening, or the end."""
    last = _last_moment(state.branch)
    later = sorted(
        _moment(i) for i in _all_injects(state.branch)
        if i.kind in MILESTONES and state.clock < _moment(i) <= last
    )
    if later:
        return later[0]
    return last if state.clock < last else None


# --------------------------------------------------------------------------- what is open


def released_to(state: LiveState, actor: str) -> tuple[Released, ...]:
    """Everything one recipient has received so far, in the order it arrived."""
    return tuple(r for r in state.released if r.inject.recipient == actor)


def open_decisions(state: LiveState) -> tuple[Inject, ...]:
    """Decision injects already released whose decision can still be recorded."""
    done = {d.decision for d in state.decisions if d.applied or state.setup.mode == "linear"}
    seen, out = set(), []
    for item in state.released:
        due = item.inject.decision_due
        if due is None or due.decision in done or due.decision in seen:
            continue
        seen.add(due.decision)
        if state.day <= due.deadline_day:
            out.append(item.inject)
    return tuple(out)


def pending_fallbacks(state: LiveState) -> tuple[Inject, ...]:
    """What a player should have passed on by now, and has neither been released by the
    control team nor marked as passed on."""
    handled = {inject_key(r.inject) for r in state.released} | set(state.passed_on)
    return tuple(
        inject for inject in _all_injects(state.branch)
        if inject.fallback and inject.recipient in PLAYERS
        and _moment(inject) <= state.clock and inject_key(inject) not in handled
    )


def paused_seconds(state: LiveState, start: str, end: str) -> float:
    """Real seconds of pause between two real times."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    total = 0.0
    for begin, finish in state.pauses:
        p0 = datetime.fromisoformat(begin)
        p1 = datetime.fromisoformat(finish) if finish else b
        total += max(0.0, (min(b, p1) - max(a, p0)).total_seconds())
    return total


# --------------------------------------------------------------------------- applying entries


def _release(
    state: LiveState, real_time: str, how: str, since: Optional[tuple[int, str]]
) -> LiveState:
    """Release the injects the clock has reached since `since` (None: from the beginning).

    When a decision changes the path, earlier injects are not rewritten: of those before
    `since`, only a decision that the new path opens is released.
    """
    have = {inject_key(r.inject) for r in state.released}
    new = sorted(
        (i for i in _all_injects(state.branch)
         if _moment(i) <= state.clock and not _is_manual(i) and inject_key(i) not in have
         and (since is None or _moment(i) > since or i.kind == "decision_due")),
        key=_moment,
    )
    if not new:
        return state
    return replace(state, released=state.released + tuple(
        Released(inject, real_time, state.day, state.time, how) for inject in new))


def _refresh(
    state: LiveState, context: Context, real_time: str, how: str = "clock",
    since: Optional[tuple[int, str]] = None,
) -> LiveState:
    state = replace(state, branch=_branch(state.setup, context, effective_choices(state, context)))
    state = _release(state, real_time, how, since)
    result = state.branch.result
    ended = state.clock >= _last_moment(state.branch) and (
        result.outcome not in OPEN_OUTCOMES or result.end_day >= int(context.params.horizon_days.value)
    )
    return replace(state, ended=ended)


def _require_running(state: LiveState, kind: str) -> None:
    if not state.started:
        raise LiveError("the exercise has not started")
    if state.ended and kind in ("step", "jump", "decision"):
        raise LiveError("the exercise has ended")
    if state.paused and kind not in ("resume",):
        raise LiveError("the exercise is paused: resume it first")


def _apply(state: LiveState, entry: Entry, context: Context) -> LiveState:
    kind, data, now = entry.kind, entry.data, entry.real_time
    if kind == "start":
        if state.started:
            raise LiveError("the exercise has already started")
        state = replace(state, started=True, day=0, time=context.script.clock["opening"])
        return _refresh(state, context, now)
    _require_running(state, kind)
    if kind in ("step", "jump"):
        target = next_moment(state) if kind == "step" else next_milestone(state)
        if target is None:
            raise LiveError("the exercise is at its end")
        since = state.clock
        state = replace(state, day=target[0], time=target[1])
        return _refresh(state, context, now, "clock" if kind == "step" else "jump", since)
    if kind == "decision":
        return _record(state, entry, context)
    if kind == "adhoc":
        return _adhoc(state, entry, context)
    if kind in ("fallback", "relay_done"):
        pending = {inject_key(i): i for i in pending_fallbacks(state)}
        inject = pending.get(data.get("key", ""))
        if inject is None:
            raise LiveError("no such notification is waiting to be passed on")
        if kind == "relay_done":
            return replace(state, passed_on=state.passed_on + (data["key"],))
        return replace(state, released=state.released + (
            Released(inject, now, state.day, state.time, "fallback"),))
    if kind == "pause":
        return replace(state, paused=True, pauses=state.pauses + ((now, ""),))
    if kind == "resume":
        if not state.paused:
            raise LiveError("the exercise is not paused")
        return replace(state, paused=False, pauses=state.pauses[:-1] + ((state.pauses[-1][0], now),))
    raise LiveError(f"unknown entry '{kind}'")


def _record(state: LiveState, entry: Entry, context: Context) -> LiveState:
    data, now = entry.data, entry.real_time
    decision, option = data.get("decision"), data.get("option")
    opened = {i.decision_due.decision: i for i in open_decisions(state)}
    if decision not in opened:
        raise LiveError(f"the decision '{decision}' is not open")
    definition = context.decisions.decisions[decision]
    if option not in definition.options:
        raise LiveError(f"'{option}' is not an option of '{decision}'")
    planned = _planned_days(state.setup, context)
    planned_option = planned.get(decision, (definition.default_option, 0))[0]
    since = next(r.real_time for r in state.released if r.inject is opened[decision])
    taken = (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds()
    linear = state.setup.mode == "linear"
    consulted = tuple(data.get("consulted", ()))
    if set(consulted) - set(ACTORS) or definition.actor in consulted:
        raise LiveError("'consulted' must name other players or bank management")
    record = RecordedDecision(
        decision=decision,
        option=option,
        actor=definition.actor,
        day=state.day,
        real_time=now,
        by_control=bool(data.get("by_control", False)),
        applied=not linear and option != definition.default_option,
        planned_option=planned_option,
        deviates=option != planned_option,
        seconds_taken=taken - paused_seconds(state, since, now),
        consulted=consulted,
    )
    state = replace(state, decisions=state.decisions + (record,))
    state = _refresh(state, context, now, since=state.clock)
    if linear and record.deviates and definition.actor in PLAYERS:
        # The path does not change: the control team corrects course (Handbook, par. 126-127).
        name = f'"{definition.name}"'
        correction = next(
            (i for i in state.branch.injects[definition.actor]
             if i.kind == "course_correction" and name in i.text), None)
        if correction is not None and inject_key(correction) not in {
                inject_key(r.inject) for r in state.released}:
            state = replace(state, released=state.released + (
                Released(correction, now, state.day, state.time, "course_correction"),))
    return state


def _adhoc(state: LiveState, entry: Entry, context: Context) -> LiveState:
    """An inject outside the plan (Handbook, par. 131): free text, or one from the library."""
    data, now = entry.data, entry.real_time
    recipients = [r for r in data.get("recipients", ()) if r in PLAYERS]
    if not recipients or set(data.get("recipients", ())) - set(PLAYERS):
        raise LiveError("an ad hoc inject needs recipients among the players")
    library = data.get("library", "")
    figures: tuple = ()
    if library:
        group, _, key = library.partition(":")
        items = {"ambient": context.script.ambient, "challenge": context.script.challenges}.get(group, {})
        if key not in items:
            raise LiveError(f"unknown library inject '{library}'")
        item = items[key]
        if getattr(item, "effect", None) == "panic_shock":
            raise LiveError("an inject that changes the run cannot be sent ad hoc")
        title, sender = item.title, item.sender
        text = item.text.format(bank_name=state.setup.bank.name)
        if getattr(item, "effect", None) or getattr(item, "figures", None):
            # Its figures exist only once FoLtF is in effect.
            with_item = _branch(
                replace(state.setup, challenges=tuple({*state.setup.challenges, key})),
                context, effective_choices(state, context))
            planned = next(
                (i for player in PLAYERS for i in with_item.injects[player]
                 if i.kind == "challenge" and i.title == item.title), None)
            foltf_day = state.branch.result.foltf_day
            declared = foltf_day is not None and foltf_day <= state.day
            if planned is None or not planned.resolution or not declared:
                raise LiveError(f'"{item.title}" has no figures yet on this path')
            figures = planned.resolution
        note = f"ad hoc, from the library ({library})"
    else:
        title, text = str(data.get("title", "")).strip(), str(data.get("text", "")).strip()
        sender = str(data.get("sender", "")).strip() or "Exercise control"
        if not title or not text:
            raise LiveError("a free-text inject needs a title and a text")
        note = "ad hoc, free text: figures not checked"
    released = tuple(
        Released(
            Inject(
                day=state.day,
                weekday=state.setup.scenario.weekday(state.day) if state.day >= 1 else "",
                time=state.time,
                recipient=recipient,
                sender=sender,
                kind="adhoc",
                title=title,
                text=text,
                resolution=figures,
                purpose="challenge" if library else "control",
                trigger="Ad hoc, decided by the control team",
                delivered_by=CONTROL,
            ),
            now, state.day, state.time, "adhoc", note,
        )
        for recipient in recipients
    )
    return replace(state, released=state.released + released)


def replay(setup: LiveSetup, entries: Sequence[Entry], context: Context) -> LiveState:
    """The state after a log. An "undo" entry takes back the last entry not yet undone."""
    states = [LiveState(setup=setup)]
    log = []
    for entry in entries:
        if entry.kind not in ENTRY_KINDS:
            raise LiveError(f"unknown entry '{entry.kind}'")
        if entry.kind == "undo":
            if len(states) <= 2:  # nothing, or only the start
                raise LiveError("there is nothing to undo")
            states.pop()
        else:
            states.append(_apply(states[-1], entry, context))
        # Each entry keeps the simulated moment it led to.
        log.append(Entry(entry.kind, entry.real_time, entry.data, *states[-1].clock))
    return replace(states[-1], entries=tuple(log))


def apply(state: LiveState, kind: str, real_time: str, context: Context, **data: Any) -> LiveState:
    """Add one entry to the log and return the new state. Raises LiveError if it cannot be."""
    entry = Entry(kind, real_time, dict(data))
    if kind == "undo" or kind not in ENTRY_KINDS:
        return replay(state.setup, [*_bare(state.entries), entry], context)
    after = _apply(state, entry, context)  # the same as replaying the longer log
    return replace(after, entries=state.entries + (replace(entry, day=after.day, time=after.time),))


def _bare(entries: Sequence[Entry]) -> list[Entry]:
    return [Entry(e.kind, e.real_time, e.data) for e in entries]


def start(setup: LiveSetup, real_time: str, context: Context) -> LiveState:
    return replay(setup, [Entry("start", real_time)], context)


# --------------------------------------------------------------------------- display


def log_rows(state: LiveState, context: Context) -> list[dict[str, str]]:
    """The log with real and simulated time stamps (Handbook, par. 209)."""
    decisions, script = context.decisions.decisions, context.script
    rows = []
    for number, entry in enumerate(state.entries, start=1):
        data = entry.data
        if entry.kind == "decision" and data.get("decision") in decisions:
            definition = decisions[data["decision"]]
            option = definition.options.get(data.get("option"))
            what = (f"{script.actors[definition.actor]}: {definition.name} -> "
                    f"{option.name if option else data.get('option')}"
                    + (" (taken by the control team)" if data.get("by_control") else ""))
        elif entry.kind == "adhoc":
            to = ", ".join(script.actors.get(r, r) for r in data.get("recipients", ()))
            what = f"{data.get('library') or data.get('title', '')} -> {to}"
        elif entry.kind in ("fallback", "relay_done"):
            what = " | ".join(str(data.get("key", "")).split("|")[:5])
        else:
            what = ""
        rows.append({
            "No.": str(number),
            "Real time": entry.real_time,
            "Simulated time": f"Day {entry.day}, {entry.time}",
            "Entry": entry.kind.replace("_", " "),
            "What": what,
        })
    return rows


# --------------------------------------------------------------------------- saving


SAVE_FORMAT = 1


def to_saved(state: LiveState, context: Context) -> dict:
    """The exercise as plain data: set-up, the configuration it ran on, and the log.

    Enough to reproduce it exactly with from_saved, whatever the config files say later.
    """
    setup = state.setup
    return {
        "format": SAVE_FORMAT,
        "label": "SIMULATION - exercise material, not a real event",
        "setup": {
            "name": setup.name,
            "bank": setup.bank.model_dump(),
            "scenario": setup.scenario.model_dump(),
            "planned": [[c.decision, c.option, c.day] for c in setup.planned],
            "mode": setup.mode,
            "ambient": setup.ambient,
            "writedown_level": setup.writedown_level,
            "challenges": list(setup.challenges),
            "hostile": setup.hostile,
        },
        "config": {
            "model": context.params.model_dump(),
            "decisions": context.decisions.model_dump(),
            "script": context.script.model_dump(),
        },
        "log": [
            {"kind": e.kind, "real_time": e.real_time, "data": e.data, "day": e.day, "time": e.time}
            for e in state.entries
        ],
    }


def from_saved(data: dict) -> tuple[LiveState, Context]:
    """Rebuild an exercise from saved data by replaying its log on its own configuration."""
    if data.get("format") != SAVE_FORMAT:
        raise LiveError("this file is not a saved exercise of this version")
    saved, config = data["setup"], data["config"]
    context = Context(
        ModelParams.model_validate(config["model"]),
        DecisionsConfig.model_validate(config["decisions"]),
        ScriptConfig.model_validate(config["script"]),
    )
    setup = LiveSetup(
        name=saved["name"],
        bank=Bank.model_validate(saved["bank"]),
        scenario=Scenario.model_validate(saved["scenario"]),
        planned=tuple(Choice(*choice) for choice in saved["planned"]),
        mode=saved["mode"],
        ambient=saved["ambient"],
        writedown_level=saved["writedown_level"],
        challenges=tuple(saved["challenges"]),
        hostile=saved["hostile"],
    )
    entries = [Entry(e["kind"], e["real_time"], dict(e["data"])) for e in data["log"]]
    return replay(setup, entries, context), context


# --------------------------------------------------------------------------- players' view


def _figure_rows(figures: Sequence, with_threshold: bool = False, with_day: bool = False) -> list:
    rows = []
    for figure in figures:
        row = [figure.label, figure.text[len(figure.label) + 1:]]
        if with_threshold:
            row.append(figure.threshold_text)
        if with_day:
            row.append(f"day {figure.as_of_day}")
        rows.append(row)
    return rows


def player_view(state: LiveState, context: Context, player: str) -> dict:
    """What one player's page shows: its own injects so far, as plain data.

    Built from that player's released injects only. Nothing of the control team's notes
    (purpose, trigger, expected response, how it was released) is in it.
    """
    if player not in PLAYERS:
        raise LiveError(f"'{player}' is not a player")
    linear = state.setup.mode == "linear"
    injects = []
    for item in released_to(state, player):
        inject, due = item.inject, item.inject.decision_due
        injects.append({
            "day": inject.day,
            "weekday": inject.weekday,
            "time": inject.time,
            "title": inject.title,
            "sender": inject.sender,
            "text": inject.text.split("\n"),
            "headline": _figure_rows(inject.headline, with_threshold=True),
            "headline_day": inject.headline[0].as_of_day if inject.headline else None,
            "notified": _figure_rows(inject.notified, with_day=True),
            "estimates": _figure_rows(inject.resolution),
            "report": _figure_rows(inject.figures),
            "report_day": inject.figures[0].as_of_day if inject.figures else None,
            "decision": None if due is None else {
                "question": due.question,
                "options": [[o.name, "" if linear else o.branch_code] for o in due.options],
                "deadline_day": due.deadline_day,
                "consult": list(due.consult),
                "inform": list(due.inform),
            },
            "received": item.real_time,
        })
    return {
        "exercise": state.setup.name,
        "bank": state.setup.bank.name,
        "player": context.script.actors[player],
        "mode": state.setup.mode,
        "day": state.day,
        "weekday": state.setup.scenario.weekday(state.day) if state.day >= 1 else "",
        "time": state.time,
        "status": "ended" if state.ended else "paused" if state.paused else "running",
        "injects": injects,
    }
