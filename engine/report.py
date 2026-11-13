"""Closing report (SPEC_V3 phase I; EBA Handbook, par. 221-225): what was decided, when, on
which figures, which coordination steps were recorded, and what the model gives had one
decision been different.

Pure functions. The report states facts and differences in outcomes only: nothing here
assesses the players. Lessons and recommendations are left to people.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from engine import live
from engine.decisions import (
    FIGURE_LABELS,
    OUTCOME_LABELS,
    Choice,
    Scorecard,
    format_figure,
    lag_days,
)
from engine.model import SimulationResult, simulate
from engine.schema import (
    DECISION_STAGES,
    PLAYERS,
    Bank,
    DecisionsConfig,
    ModelParams,
    Scenario,
    ScriptConfig,
)
from engine.script import MODE_LABELS

NOT_RECORDED = "not recorded"
CONSULT = "Consult before deciding"
CONSULT_EXTRA = "Consult before deciding (not in the script)"
PASS_ON = "Pass on to another player"


@dataclass(frozen=True)
class DecisionLine:
    day: int
    weekday: str
    actor: str  # display name
    decision: str  # key
    decision_name: str
    option_name: str
    planned_name: str
    as_planned: bool
    applied: bool  # False: "no decision yet", or a linear exercise
    by_control: bool
    consulted: tuple[str, ...]
    figures: tuple[str, ...]  # what that player held then, at its own reporting delay
    figures_day: int
    real_time: str
    minutes: Optional[float]  # real minutes from the opening of the decision, pauses excluded


@dataclass(frozen=True)
class Step:
    """One coordination step the script expected, and whether it was recorded as done."""

    about: str
    step: str
    by: str
    to: str
    recorded: bool
    how: str


@dataclass(frozen=True)
class Counterfactual:
    decision_name: str
    actor: str
    change: str
    possible: bool
    scorecard: Optional[Scorecard]
    differences: str


@dataclass(frozen=True)
class ClosingReport:
    title: str
    bank_name: str
    unit: str
    mode: str
    live: bool  # from a live exercise; False: from a pre-planned path
    overview: tuple[tuple[str, str], ...]
    decisions: tuple[DecisionLine, ...]
    steps: tuple[Step, ...]
    conduct: tuple[tuple[str, str], ...]
    actual: Scorecard
    counterfactuals: tuple[Counterfactual, ...]


# --------------------------------------------------------------------------- counterfactuals


def differences(
    actual: Scorecard, other: Scorecard, unit: str,
    foltf: Optional[tuple[Optional[int], Optional[int]]] = None,
) -> str:
    """How another run's scorecard differs from what happened, in plain neutral words.

    foltf: the day FoLtF is in effect in what happened and in the other run, if to be compared.
    """
    parts = []
    if foltf is not None and foltf[0] != foltf[1]:
        say = lambda day: "no FoLtF" if day is None else f"FoLtF in effect on day {day}"  # noqa: E731
        parts.append(f"{say(foltf[1])} instead of {say(foltf[0]).replace('FoLtF in effect on ', '')}"
                     if None not in foltf else f"{say(foltf[1])} instead of {say(foltf[0])}")
    if other.outcome != actual.outcome:
        parts.append(f"outcome \"{OUTCOME_LABELS[other.outcome]}\" instead of "
                     f"\"{OUTCOME_LABELS[actual.outcome]}\"")
    if other.end_day != actual.end_day:
        parts.append(f"last simulated day {other.end_day} instead of {actual.end_day}")
    for label, name, digits, suffix in (
        ("deposits lost", "deposits_lost_pct", 1, "%"),
        ("ELA outstanding at the end", "ela_outstanding", 2, f" {unit}"),
        ("losses booked", "losses_booked", 2, f" {unit}"),
        ("DGS payout", "dgs_payout", 2, f" {unit}"),
    ):
        mine, theirs = getattr(actual, name), getattr(other, name)
        if round(mine, digits) != round(theirs, digits):
            parts.append(f"{label} {theirs:.{digits}f}{suffix} instead of {mine:.{digits}f}{suffix}")
    return "; ".join(parts) if parts else "no difference in the scorecard"


def counterfactuals(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    actual_choices: Sequence[Choice],
    keys: Sequence[tuple[str, int]],
) -> tuple[Counterfactual, ...]:
    """For each key decision (decision, day): every other option on that day, and the option
    taken one day earlier and one day later. Everything else stays as it was; a decision that
    depends on the changed one is taken at the first close at which its condition holds."""
    actual = simulate(bank, params, scenario, decisions, actual_choices)
    taken = {r.decision: r for r in actual.decision_log}
    out = []

    def run(choices: Sequence[Choice]) -> SimulationResult:
        return simulate(bank, params, scenario, decisions, choices)

    def add(definition, change: str, result: Optional[SimulationResult], reason: str = "") -> None:
        out.append(Counterfactual(
            decision_name=definition.name,
            actor=script.actors[definition.actor],
            change=change,
            possible=result is not None,
            scorecard=result.scorecard if result is not None else None,
            differences=differences(actual.scorecard, result.scorecard, bank.unit,
                                    (actual.foltf_day, result.foltf_day))
            if result is not None else reason,
        ))

    for decision, day in keys:
        definition = decisions.decisions[decision]
        others = [choice for choice in actual_choices if choice.decision != decision]
        record = taken.get(decision)
        current = record.option if record else definition.default_option
        at = record.decided_day if record else day
        for option, item in definition.options.items():
            if option == current:
                continue
            choices = others + ([] if option == definition.default_option
                                else [Choice(decision, option, at)])
            add(definition, f"\"{item.name}\" instead of \"{definition.options[current].name}\", "
                            f"at the close of day {at}", run(choices))
        if record is None:
            continue
        name = definition.options[current].name
        for shift, word, reason in (
            (-1, "earlier", "the decision was not open one day earlier"),
            (1, "later", "the run ends before one day later"),
        ):
            result = run(others + [Choice(decision, current, at + shift)])
            moved = next((r for r in result.decision_log if r.decision == decision), None)
            ok = moved is not None and moved.option == current and moved.decided_day == at + shift
            add(definition, f"\"{name}\" one day {word}, at the close of day {at + shift}",
                result if ok else None, reason)
    return tuple(out)


# --------------------------------------------------------------------------- helpers


def _held(result: SimulationResult, decisions: DecisionsConfig, actor: str, day: int,
          unit: str) -> tuple[tuple[str, ...], int]:
    """The figures an actor held at the close of `day`: its own set, at its reporting delay."""
    info = decisions.information[actor]
    faster = dict(result.faster_reporting_from).get(actor)
    as_of = max(0, min(day, result.end_day) - lag_days(info, day, faster))
    row = result.table.set_index("day").loc[as_of]
    return tuple(format_figure(name, float(row[name]), unit) for name in info.figures), as_of


def _minutes(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60


def _weekday(scenario: Scenario, day: int) -> str:
    return scenario.weekday(day) if day >= 1 else ""


# --------------------------------------------------------------------------- live exercise


def live_report(state: live.LiveState, context: live.Context) -> ClosingReport:
    """The draft closing report of a live exercise, from its log."""
    setup, decisions, script = state.setup, context.decisions, context.script
    result, unit = state.branch.result, setup.bank.unit
    definitions = decisions.decisions

    lines = []
    for d in state.decisions:
        definition = definitions[d.decision]
        figures, as_of = _held(result, decisions, d.actor, d.day, unit)
        lines.append(DecisionLine(
            day=d.day,
            weekday=_weekday(setup.scenario, d.day),
            actor=script.actors[d.actor],
            decision=d.decision,
            decision_name=definition.name,
            option_name=definition.options[d.option].name,
            planned_name=definition.options[d.planned_option].name,
            as_planned=not d.deviates,
            applied=d.applied,
            by_control=d.by_control,
            consulted=tuple(script.actors[a] for a in d.consulted),
            figures=figures,
            figures_day=as_of,
            real_time=d.real_time,
            minutes=None if d.seconds_taken is None else d.seconds_taken / 60,
        ))

    steps = []
    for d in state.decisions:
        if d.actor not in PLAYERS:
            continue
        about = f"{definitions[d.decision].name} (day {d.day})"
        expected = script.coordination[d.decision].consult
        for other in expected:
            done = other in d.consulted
            steps.append(Step(about, CONSULT, script.actors[d.actor], script.actors[other], done,
                              "recorded by the control team" if done else "no record"))
        for other in d.consulted:
            if other not in expected:
                steps.append(Step(about, CONSULT_EXTRA, script.actors[d.actor],
                                  script.actors[other], True, "recorded by the control team"))
    if setup.mode == "dynamic":
        released = {live.inject_key(r.inject): r for r in state.released if r.how == "fallback"}
        relays = {live.inject_key(i): i for actor in PLAYERS for i in state.branch.injects[actor]
                  if i.fallback and (i.day, i.time) <= state.clock}
        relays.update({key: r.inject for key, r in released.items()})
        for key, inject in sorted(relays.items(), key=lambda kv: (kv[1].day, kv[1].time)):
            if key in state.passed_on:
                done, how = True, "passed on by the player"
            elif key in released:
                done, how = False, "the control team released the fallback inject"
            else:
                done, how = False, "no record"
            what = inject.title if inject.kind in DECISION_STAGES + ("foltf",) else (
                inject.text.split(". In effect")[0])
            steps.append(Step(f"{what} (day {inject.day})", PASS_ON, inject.sender,
                              script.actors[inject.recipient], done, how))

    actual_choices = live.effective_choices(state, context)
    recorded: dict[str, live.RecordedDecision] = {}
    for d in state.decisions:
        if d.decision not in recorded or d.applied or not recorded[d.decision].applied:
            recorded[d.decision] = d
    keys = [(d.decision, d.day) for d in recorded.values()]
    cases = list(counterfactuals(setup.bank, context.params, setup.scenario, decisions, script,
                                 actual_choices, keys))
    planned = simulate(setup.bank, context.params, setup.scenario, decisions, setup.planned)
    cases.append(Counterfactual(
        decision_name="All decisions",
        actor="All players",
        change="every decision as on the pre-planned path",
        possible=True,
        scorecard=planned.scorecard,
        differences=differences(result.scorecard, planned.scorecard, unit),
    ))

    entries = state.entries
    start, end = entries[0].real_time, entries[-1].real_time
    paused = live.paused_seconds(state, start, end) / 60
    to_players = [r for r in state.released if r.inject.recipient in PLAYERS]
    how = lambda kind: sum(1 for r in to_players if r.how == kind)  # noqa: E731
    adhoc = sorted({r.inject.title for r in to_players if r.how == "adhoc"})
    taxonomy = setup.scenario.taxonomy.label() if setup.scenario.taxonomy else "not classified"
    overview = (
        ("Exercise", setup.name),
        ("Bank", setup.bank.name),
        ("Scenario", f"{setup.scenario.name} ({taxonomy})"),
        ("Type of exercise", MODE_LABELS[setup.mode]),
        ("Assumptions", "Hostile" if setup.hostile else "Friendly"),
        ("Started (real time)", start.replace("T", " ")),
        ("Last entry (real time)", end.replace("T", " ")),
        ("Real duration", f"{_minutes(start, end):.0f} minutes, of which {paused:.0f} paused"),
        ("Simulated time reached", f"Day {state.day}, {state.time}"),
        ("Status", "Ended" if state.ended else "Not finished when this report was drawn up"),
        ("Outcome" if state.ended else "Outcome expected on the path at that point",
         f"{OUTCOME_LABELS[result.outcome]} (day {result.end_day})"),
        ("Decisions recorded", f"{len(state.decisions)}, of which "
                               f"{sum(d.deviates for d in state.decisions)} different from the "
                               f"pre-planned path"),
    )
    conduct = (
        ("Injects delivered to the players", str(len(to_players))),
        *((f"  to the {script.actors[player].lower()}",
           str(sum(1 for r in to_players if r.inject.recipient == player))) for player in PLAYERS),
        ("Released by the clock", str(how("clock"))),
        ("Released together by a jump", str(how("jump"))),
        ("Fallback injects released by the control team", str(how("fallback"))),
        ("Notifications passed on by a player", str(len(state.passed_on))),
        ("Course corrections", str(how("course_correction"))),
        ("Ad hoc injects, counted per recipient",
         f"{how('adhoc')}" + (f": {'; '.join(adhoc)}" if adhoc else "")),
        ("Decisions taken by the control team", str(sum(d.by_control for d in state.decisions))),
        ("Pauses", f"{len(state.pauses)}, {paused:.0f} real minutes in all"),
        ("Jumps", str(sum(1 for e in entries if e.kind == "jump"))),
        ("Entries taken back (undo)", str(sum(1 for e in entries if e.kind == "undo"))),
        ("Log entries", str(len(entries))),
    )
    return ClosingReport(
        title=setup.name,
        bank_name=setup.bank.name,
        unit=unit,
        mode=setup.mode,
        live=True,
        overview=overview,
        decisions=tuple(lines),
        steps=tuple(steps),
        conduct=conduct,
        actual=result.scorecard,
        counterfactuals=tuple(cases),
    )


# --------------------------------------------------------------------------- pre-planned path


def planned_report(
    name: str,
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    choices: Sequence[Choice],
    mode: str = "dynamic",
) -> ClosingReport:
    """The same report for a pre-planned path. Nothing was played, so real times and
    coordination steps are not recorded."""
    bank = bank.model_copy(update={"name": script.setup.bank_alias})
    result = simulate(bank, params, scenario, decisions, choices)
    lines = []
    for record in result.decision_log:
        definition = decisions.decisions[record.decision]
        lines.append(DecisionLine(
            day=record.decided_day,
            weekday=_weekday(scenario, record.decided_day),
            actor=script.actors[record.actor],
            decision=record.decision,
            decision_name=definition.name,
            option_name=record.option_name,
            planned_name=record.option_name,
            as_planned=True,
            applied=True,
            by_control=False,
            consulted=(),
            figures=tuple(format_figure(figure, value, bank.unit) for figure, value in record.figures),
            figures_day=record.figures_as_of,
            real_time=NOT_RECORDED,
            minutes=None,
        ))
    keys = [(record.decision, record.decided_day) for record in result.decision_log]
    taxonomy = scenario.taxonomy.label() if scenario.taxonomy else "not classified"
    overview = (
        ("Exercise", name),
        ("Bank", bank.name),
        ("Scenario", f"{scenario.name} ({taxonomy})"),
        ("Type of exercise", MODE_LABELS[mode]),
        ("Source", "Pre-planned path: nothing was played, so there are no real times"),
        ("Outcome", f"{OUTCOME_LABELS[result.outcome]} (day {result.end_day})"),
        ("Decisions on the path", str(len(lines))),
    )
    return ClosingReport(
        title=name,
        bank_name=bank.name,
        unit=bank.unit,
        mode=mode,
        live=False,
        overview=overview,
        decisions=tuple(lines),
        steps=(),
        conduct=(),
        actual=result.scorecard,
        counterfactuals=counterfactuals(bank, params, scenario, decisions, script, choices, keys),
    )


def figure_names() -> tuple[str, ...]:
    return tuple(label for label, _ in FIGURE_LABELS.values())
