"""Exercise script (SPEC_V2 phase D): injects per actor, branches and the facilitator's view.

Pure functions. Three rules hold for every participant document:
- an actor receives nothing before it is informed (`information.informed_from`);
- every figure comes from the actor's own set and is as old as its reporting delay;
- the texts carry no figures, so the figure lists are the only numbers a participant sees.
Stage changes, payments default, FOLTF and resolution are notified the same day; only the
figures are delayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from engine.decisions import (
    OPEN_OUTCOMES,
    OUTCOME_LABELS,
    Choice,
    FIGURE_LABELS,
    format_figure,
    lag_days,
)
from engine.model import SimulationResult, simulate
from engine.schema import (
    ACTORS,
    DECISION_STAGES,
    Bank,
    DecisionsConfig,
    ModelParams,
    Scenario,
    ScriptConfig,
)

_STAGE_OF = {name: index for index, name in enumerate(DECISION_STAGES, start=1)}
_STATUS_STAGE = {"normal": 0, "recovery": 1, "early_intervention": 2, "likely_to_fail": 3, "failed": 4}
# Order of injects that fall on the same day and time.
_KIND_ORDER = (
    "opening", "foltf", "decision_taken", "news", "recovery", "early_intervention",
    "likely_to_fail", "failure", "resolution", "decision_due", "end_open",
)
_TITLES = {
    "opening": "Opening",
    "news": "News",
    "recovery": "Recovery indicator breached",
    "early_intervention": "Early intervention conditions met",
    "likely_to_fail": "The bank is likely to fail",
    "failure": "Payments default",
    "foltf": "Failing or likely to fail declared",
    "resolution": "End of the run",
    "decision_taken": "Decision by another actor",
    "decision_due": "Decision open to you",
    "end_open": "End of the exercise",
}


@dataclass(frozen=True)
class Figure:
    name: str
    label: str
    value: float
    as_of_day: int  # the day the figure refers to
    text: str  # as printed, e.g. "Liquidity buffer 150.86 EUR bn"


@dataclass(frozen=True)
class OptionLine:
    option: str
    name: str
    branch_code: str
    is_default: bool


@dataclass(frozen=True)
class DecisionDue:
    decision: str
    name: str
    question: str
    options: tuple[OptionLine, ...]
    deadline_day: int  # decide by the close of this day
    consult: tuple[str, ...]  # actor display names
    inform: tuple[str, ...]


@dataclass(frozen=True)
class Inject:
    day: int
    weekday: str  # empty for day 0
    time: str
    recipient: str  # actor key
    sender: str
    kind: str
    title: str
    text: str
    figures: tuple[Figure, ...] = ()
    decision_due: Optional[DecisionDue] = None


@dataclass(frozen=True)
class DecisionPoint:
    """Facilitator's view of one option of one decision: where that branch leads.

    For the option the exported path follows, this is the path itself. For any other option
    it is a run in which the option is taken at the first possible close, the decisions
    already taken by then stand, and nothing is decided afterwards.
    """

    code: str
    actor: str
    decision: str
    decision_name: str
    option: str
    option_name: str
    open_day: int
    deadline_day: int
    taken: bool  # the option the exported path follows
    outcome: str
    end_day: int
    deposits_lost_pct: float
    ela_outstanding: float


@dataclass(frozen=True)
class BranchScript:
    label: str  # "A" or "B"
    name: str  # name of the decision path
    result: SimulationResult
    injects: dict[str, tuple[Inject, ...]]  # per actor, in order
    decision_points: tuple[DecisionPoint, ...]


@dataclass(frozen=True)
class ActorDocument:
    actor: str
    name: str
    information: tuple[str, ...]  # what this actor sees, in plain sentences
    shared: tuple[Inject, ...]  # before the branches diverge
    branches: tuple[tuple[str, tuple[Inject, ...]], ...]  # (heading, injects) per branch


@dataclass(frozen=True)
class ExercisePack:
    bank_name: str
    scenario_name: str
    unit: str
    start_weekday: str
    horizon: int
    branches: tuple[BranchScript, ...]
    documents: tuple[ActorDocument, ...]
    information_rows: tuple[dict[str, str], ...]  # facilitator: who sees what, how late


# --------------------------------------------------------------------------- helpers


def branch_code(decisions: DecisionsConfig, decision: str, option: str) -> str:
    """Stable code of one option, e.g. 'D4-b': fourth decision of the config, second option."""
    number = list(decisions.decisions).index(decision) + 1
    letter = "abcdefgh"[list(decisions.decisions[decision].options).index(option)]
    return f"D{number}-{letter}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def information_sentences(decisions: DecisionsConfig, actor: str) -> tuple[str, ...]:
    info = decisions.information[actor]
    figures = "; ".join(FIGURE_LABELS[name][0] for name in info.figures)
    age = "of the same day" if info.lag_days == 0 else (
        "one day old" if info.lag_days == 1 else f"{info.lag_days} days old"
    )
    lines = [f"Your figures come from {info.source}.", f"You receive: {figures}."]
    lines.append(f"Your figures are {age}. Delays are counted in whole days.")
    if info.faster is not None:
        option = decisions.decisions[info.faster.decision].options[info.faster.option]
        lines.append(
            f'After the decision "{option.name}" is in effect, your figures are of the same day.'
            if info.faster.lag_days == 0
            else f'After the decision "{option.name}" the delay is {info.faster.lag_days} days.'
        )
    if info.informed_from is not None:
        stage = _TITLES[info.informed_from].lower()
        lines.append(f"You receive nothing about the bank before this stage: {stage}.")
    lines.append(
        "Stage changes, a payments default and formal declarations reach you the same day; "
        "only the figures are delayed."
    )
    return tuple(lines)


# --------------------------------------------------------------------------- one branch


def build_branch(
    label: str,
    name: str,
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    choices: Sequence[Choice],
    late_default_day: Optional[int],
) -> BranchScript:
    """Simulate one decision path and write every actor's injects for it."""
    result = simulate(bank, params, scenario, decisions, choices)
    rows = result.table.set_index("day")
    end_day = result.end_day
    horizon = int(params.horizon_days.value)
    ended = result.outcome not in OPEN_OUTCOMES
    stage_days = {
        event.kind: event.day for event in result.events if event.kind in DECISION_STAGES
    }
    stage_by_day = [_STATUS_STAGE.get(status) for status in result.table["status"]]
    for index, value in enumerate(stage_by_day):  # resolution days keep the last stage
        if value is None:
            stage_by_day[index] = stage_by_day[index - 1]
    faster = dict(result.faster_reporting_from)
    decided = {record.decision: record for record in result.decision_log}

    def informed_day(actor: str) -> Optional[int]:
        needed = decisions.information[actor].informed_from
        return 0 if needed is None else stage_days.get(needed)

    def is_informed(actor: str, day: int) -> bool:
        first = informed_day(actor)
        return first is not None and first <= day

    def figures(actor: str, day: int) -> tuple[Figure, ...]:
        info = decisions.information[actor]
        as_of = max(0, min(day, end_day) - lag_days(info, day, faster.get(actor)))
        row = rows.loc[as_of]
        return tuple(
            Figure(
                name=figure,
                label=FIGURE_LABELS[figure][0],
                value=float(row[figure]),
                as_of_day=as_of,
                text=format_figure(figure, float(row[figure]), bank.unit),
            )
            for figure in info.figures
        )

    def make(actor: str, day: int, kind: str, clock: str, sender: str, text: str,
             with_figures: bool = True, due: Optional[DecisionDue] = None) -> Inject:
        return Inject(
            day=day,
            weekday=scenario.weekday(day) if day >= 1 else "",
            time=script.clock[clock],
            recipient=actor,
            sender=sender,
            kind=kind,
            title=_TITLES[kind],
            text=text,
            figures=figures(actor, day) if with_figures else (),
            decision_due=due,
        )

    def condition_day(decision_key: str) -> Optional[int]:
        """First close at which the decision is open to its actor on this path."""
        definition = decisions.decisions[decision_key]
        condition = definition.condition
        for day in range(0, end_day + 1):
            if ended and day == end_day:
                return None  # the run ends that day: nothing is left to decide
            if day < condition.min_day or not is_informed(definition.actor, day):
                continue
            if condition.stage is not None and stage_by_day[day] < _STAGE_OF[condition.stage]:
                continue
            if condition.after is not None:
                earlier = decided.get(condition.after)
                if earlier is None or earlier.decided_day > day:
                    continue
                if condition.after_option is not None and earlier.option != condition.after_option:
                    continue
            return day
        return None

    def deadline(open_day: int) -> int:
        if late_default_day is not None:
            # The last close at which the decision is in effect before the default day.
            return max(open_day, late_default_day - int(decisions.lead_days.value))
        return min(horizon, open_day + int(script.decision_window_days.value))

    open_days = {key: condition_day(key) for key in decisions.decisions}
    names = script.actors
    injects: dict[str, list[Inject]] = {actor: [] for actor in ACTORS}

    for actor in ACTORS:
        info = decisions.information[actor]
        sender = _capital(info.source)
        out = injects[actor]
        out.append(
            make(actor, 0, "opening", "opening", "Exercise control",
                 script.texts["opening"].format(bank_name=bank.name, scenario_name=scenario.name),
                 with_figures=is_informed(actor, 0))
        )
        first_stage = _STAGE_OF[info.informed_from] if info.informed_from else 0
        for event in result.events:
            if event.kind == "news":
                out.append(
                    make(actor, event.day, "news", "news", "Public media",
                         script.texts["news"].format(headline=event.detail), with_figures=False)
                )
            elif event.kind in DECISION_STAGES:
                if _STAGE_OF[event.kind] >= first_stage and is_informed(actor, event.day):
                    out.append(
                        make(actor, event.day, event.kind, "stage", sender,
                             script.texts[event.kind].format(bank_name=bank.name, sender=info.source))
                    )
            elif event.kind == "foltf" and is_informed(actor, event.day):
                out.append(
                    make(actor, event.day, "foltf", "decision_taken", names["competent_authority"],
                         script.texts["foltf"].format(bank_name=bank.name))
                )
            elif event.kind == "resolution" and is_informed(actor, event.day):
                out.append(
                    make(actor, event.day, "resolution", "end", names["resolution_authority"],
                         script.texts["resolution"].format(
                             bank_name=bank.name, outcome=OUTCOME_LABELS[result.outcome].lower()))
                )
        for record in result.decision_log:
            option = decisions.decisions[record.decision].options[record.option]
            if option.effect in ("declare_foltf", "resolution_tool"):
                continue  # announced to everyone informed by the FOLTF and end-of-run injects
            if actor not in script.coordination[record.decision].inform:
                continue
            if record.effect_day > end_day or not is_informed(actor, record.decided_day):
                continue
            out.append(
                make(actor, record.effect_day, "decision_taken", "decision_taken",
                     names[record.actor],
                     script.texts["decision_taken"].format(
                         decider=names[record.actor], decision_name=record.decision_name,
                         option_name=record.option_name, effect_day=record.effect_day),
                     with_figures=False)
            )
        for key, definition in decisions.decisions.items():
            open_day = open_days[key]
            if definition.actor != actor or open_day is None:
                continue
            coordination = script.coordination[key]
            due = DecisionDue(
                decision=key,
                name=definition.name,
                question=script.questions[key],
                options=tuple(
                    OptionLine(option_key, option.name, branch_code(decisions, key, option_key),
                               option_key == definition.default_option)
                    for option_key, option in definition.options.items()
                ),
                deadline_day=deadline(open_day),
                consult=tuple(names[a] for a in coordination.consult),
                inform=tuple(names[a] for a in coordination.inform),
            )
            text = script.texts["decision_due"].format(decision_name=definition.name)
            earlier = decided.get(definition.condition.after or "")
            if earlier is not None and earlier.decided_day == open_day:
                # The decision that opens this one was taken the same evening: say so first.
                text = script.texts["decision_taken"].format(
                    decider=names[earlier.actor], decision_name=earlier.decision_name,
                    option_name=earlier.option_name, effect_day=earlier.effect_day,
                ) + " " + text
            out.append(
                make(actor, open_day, "decision_due", "decision_due", "Exercise control",
                     text, due=due)
            )
        if not ended and is_informed(actor, end_day):
            out.append(
                make(actor, end_day, "end_open", "end", "Exercise control",
                     script.texts["end_open"].format(bank_name=bank.name, horizon_days=horizon))
            )
        out.sort(key=lambda i: (i.day, i.time, _KIND_ORDER.index(i.kind)))

    # Facilitator: where each option of each open decision leads on this path.
    points = []
    for key, definition in decisions.decisions.items():
        open_day = open_days[key]
        if open_day is None:
            continue
        earlier = [
            Choice(record.decision, record.option, record.decided_day)
            for record in result.decision_log
            if record.decision != key and record.decided_day <= open_day
        ]
        taken = decided[key].option if key in decided else definition.default_option
        for option_key, option in definition.options.items():
            if option_key == taken:
                outcome = result  # the exported path itself, with its later decisions
            else:
                extra = [] if option_key == definition.default_option else [
                    Choice(key, option_key, open_day)
                ]
                outcome = simulate(bank, params, scenario, decisions, earlier + extra)
            points.append(
                DecisionPoint(
                    code=branch_code(decisions, key, option_key),
                    actor=definition.actor,
                    decision=key,
                    decision_name=definition.name,
                    option=option_key,
                    option_name=option.name,
                    open_day=open_day,
                    deadline_day=deadline(open_day),
                    taken=option_key == taken,
                    outcome=outcome.outcome,
                    end_day=outcome.end_day,
                    deposits_lost_pct=outcome.scorecard.deposits_lost_pct,
                    ela_outstanding=outcome.scorecard.ela_outstanding,
                )
            )
    return BranchScript(
        label=label,
        name=name,
        result=result,
        injects={actor: tuple(items) for actor, items in injects.items()},
        decision_points=tuple(points),
    )


# --------------------------------------------------------------------------- the pack


def build_pack(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    paths: Sequence[tuple[str, Sequence[Choice]]],
) -> ExercisePack:
    """Build the exercise pack for one or two decision paths: (name, choices) each.

    With two paths every actor's document has a shared part, up to the point where the two
    paths differ for that actor, and then one section per branch.
    """
    if not 1 <= len(paths) <= 2:
        raise ValueError("an exercise pack takes one or two decision paths")
    late = simulate(bank, params, scenario, decisions, ())
    branches = tuple(
        build_branch("AB"[index], name, bank, params, scenario, decisions, script, choices,
                     late.failure_day)
        for index, (name, choices) in enumerate(paths)
    )
    documents = []
    for actor in ACTORS:
        streams = [branch.injects[actor] for branch in branches]
        common = 0
        if len(streams) == 2:
            while (common < min(len(streams[0]), len(streams[1]))
                   and streams[0][common] == streams[1][common]):
                common += 1
            rest = tuple(
                (f"Branch {branch.label}", stream[common:])  # no name: it may give the end away
                for branch, stream in zip(branches, streams)
            )
        else:
            common, rest = len(streams[0]), ()
        documents.append(
            ActorDocument(
                actor=actor,
                name=script.actors[actor],
                information=information_sentences(decisions, actor),
                shared=streams[0][:common],
                branches=rest,
            )
        )
    information_rows = tuple(
        {
            "Actor": script.actors[actor],
            "Figures from": info.source,
            "Delay (days)": str(info.lag_days),
            "Informed from": "the start" if info.informed_from is None
            else _TITLES[info.informed_from].lower(),
            "Figures": ", ".join(FIGURE_LABELS[name][0] for name in info.figures),
        }
        for actor, info in ((a, decisions.information[a]) for a in ACTORS)
    )
    return ExercisePack(
        bank_name=bank.name,
        scenario_name=scenario.name,
        unit=bank.unit,
        start_weekday=scenario.start_weekday,
        horizon=int(params.horizon_days.value),
        branches=branches,
        documents=tuple(documents),
        information_rows=information_rows,
    )
