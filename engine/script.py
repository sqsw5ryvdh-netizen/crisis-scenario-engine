"""Exercise script (SPEC_V2 phase D, SPEC_V3 phase G): injects, branches, the main events
list and the role cards of the stakeholders the control team plays.

Pure functions. These rules hold for every player document:
- an actor receives nothing before it is informed (`information.informed_from`);
- its regular report holds figures from its own set, as old as its reporting delay;
- a stage notification carries the figures that triggered it, of the same day, as passed on
  along the chain bank -> competent authority -> other authorities;
- the texts carry no figures, so the figure lists are the only numbers a player sees.
Notifications that reach an actor at the same time are one inject. Ambient injects are
background noise from config and change no figure.

Players are the four authorities. Bank management is played by the control team: its injects
are still worked out, and go on its role card. Every inject has a purpose (advance, challenge,
control), a trigger, an expected response and the role that delivers it (EBA Handbook,
par. 128, 132, 133). In a dynamic exercise, what one player should tell another is not handed
out by the control team (par. 191): it is an expected response, with a fallback inject.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from engine.decisions import (
    OPEN_OUTCOMES,
    OUTCOME_LABELS,
    Choice,
    FIGURE_LABELS,
    format_figure,
    intraday_allocation,
    lag_days,
)
from engine.model import SimulationResult, simulate
from engine.resolution import (
    LEVELS,
    ResolutionSummary,
    hostile_liquidity_need,
    indicative_bid,
    resolution_levels,
    summary_rows,
)
from engine.setup import Dossier, build_dossier
from engine.schema import (
    ACTORS,
    CONTROL_TEAM,
    DECISION_STAGES,
    PLAYERS,
    ROLE_STAGES,
    ROLES,
    Bank,
    DecisionsConfig,
    ModelParams,
    NewsEvent,
    Scenario,
    ScriptConfig,
)

CONTROL = "Control team"
# Prefix of the sheet number of each recipient's injects.
ACTOR_CODES = {
    "competent_authority": "CA",
    "resolution_authority": "RA",
    "central_bank": "CB",
    "deposit_guarantee_scheme": "DGS",
    "bank_management": "BM",
}

_STAGE_OF = {name: index for index, name in enumerate(DECISION_STAGES, start=1)}
_STATUS_STAGE = {"normal": 0, "recovery": 1, "early_intervention": 2, "likely_to_fail": 3, "failed": 4}
# Order of injects that fall on the same day and time.
_KIND_ORDER = (
    "opening", "foltf", "decision_taken", "course_correction", "news", "ambient", "challenge",
    "recovery", "early_intervention", "likely_to_fail", "failure", "resolution_step",
    "resolution", "decision_due", "end_open",
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
    "decision_taken": "Decision by another party",
    "course_correction": "Course correction",
    "challenge": "Challenge",
    "decision_due": "Decision open to you",
    "end_open": "End of the exercise",
    "ambient": "Ambient",
    "resolution_step": "Resolution period",
}


# Linear or dynamic exercise (EBA Handbook, par. 124-126). Players and observers should
# always know which it is, so it is stated on every document.
MODES = ("dynamic", "linear")
MODE_LABELS = {"dynamic": "Dynamic", "linear": "Linear"}
MODE_TEXT = {
    "dynamic": "This exercise is dynamic: the players' decisions change the course of events.",
    "linear": "This exercise is linear: it follows a path fixed in advance. Decisions are "
              "recorded but do not change the course of events.",
}


@dataclass(frozen=True)
class Figure:
    name: str
    label: str
    value: float
    as_of_day: int  # the day the figure refers to
    text: str  # as printed, e.g. "Liquidity buffer 150.86 EUR bn"
    threshold_text: str = ""  # for a figure that triggered a stage: the threshold it crossed
    block: str = ""  # for an intraday line: the time block (an allocation of the daily figure)


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
    deadline_day: int  # decide by the close of this day, on the branch being read
    consult: tuple[str, ...]  # actor display names
    inform: tuple[str, ...]
    # In the shared part of a document: the branches have different deadlines and this is
    # the earlier one.
    deadline_shared: bool = False


@dataclass(frozen=True)
class Inject:
    day: int
    weekday: str  # empty for day 0
    time: str
    recipient: str  # actor key
    sender: str
    kind: str
    title: str
    text: str  # one line per notification
    figures: tuple[Figure, ...] = ()  # the actor's regular report, at its usual age
    decision_due: Optional[DecisionDue] = None
    # Stage notifications: the figures that triggered them, of the same day, from the sender.
    headline: tuple[Figure, ...] = ()
    # Decision injects: the triggering figures the actor has been told so far.
    notified: tuple[Figure, ...] = ()
    stages: tuple[str, ...] = ()  # the stage changes this notification covers
    # Injects of the resolution period: estimates from the resolution block.
    resolution: tuple[Figure, ...] = ()
    # For the main events list (control team only; never printed on a player's sheet).
    purpose: str = "advance"  # advance | challenge | control
    trigger: str = ""  # a time, or a milestone
    expected: str = ""  # the response the control team looks for
    delivered_by: str = CONTROL  # the control team, or the stakeholder it plays
    # Handed out only if the player who should have passed the information on has not.
    fallback: bool = False


@dataclass(frozen=True)
class DecisionPoint:
    """control team's view of one option of one decision: where that branch leads.

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
    # The resolution figures at each write-down level; None if the run has no resolution.
    resolution: Optional[dict[str, ResolutionSummary]] = None


@dataclass(frozen=True)
class ActorDocument:
    actor: str
    name: str
    information: tuple[str, ...]  # what this actor sees, in plain sentences
    shared: tuple[Inject, ...]  # before the branches diverge
    branches: tuple[tuple[str, tuple[Inject, ...]], ...]  # (heading, injects) per branch


@dataclass(frozen=True)
class MainEvent:
    """One line of the main events list: an inject, to one or several players."""

    number: int
    section: str  # "" (one path, or before the branches diverge), "A" or "B"
    day: int
    weekday: str
    time: str
    kind: str
    title: str
    text: str
    recipients: tuple[str, ...]  # player keys
    sheets: tuple[str, ...]  # the sheet number of each recipient's copy
    delivered_by: str
    purpose: str
    trigger: str
    expected: str
    fallback: bool


@dataclass(frozen=True)
class RoleCard:
    """What the control team member playing a stakeholder needs (SPEC_V3 G6)."""

    role: str
    name: str
    knows: tuple[str, ...]
    wants: tuple[str, ...]
    lines: tuple[tuple[str, str, str], ...]  # (moment, when on this pack's paths, what to say)
    figures: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]  # (heading, (label, value))
    delivers: tuple[MainEvent, ...]


@dataclass(frozen=True)
class ExercisePack:
    bank_name: str
    scenario_name: str
    unit: str
    start_weekday: str
    horizon: int
    branches: tuple[BranchScript, ...]
    documents: tuple[ActorDocument, ...]
    information_rows: tuple[dict[str, str], ...]  # control team: who sees what, how late
    ambient_on: bool = True
    writedown_level: str = "central"  # the level the players' documents use
    mode: str = "dynamic"  # dynamic | linear
    taxonomy: str = ""  # the scenario's place in the Handbook's taxonomy, in words
    challenges: tuple[str, ...] = ()  # the challenge injects switched on
    hostile: bool = False  # hostile assumptions (EBA Handbook, par. 123)
    events: tuple[MainEvent, ...] = ()  # the main events list
    roles: tuple[RoleCard, ...] = ()
    dossier: Optional[Dossier] = None  # the mock bank at day 0, shared with the players


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
    if info.intraday:
        lines.append(
            "You also see each day's outflow by time of day. This is an allocation of the "
            "daily figure, not a count of the hour's payments."
        )
    if info.informed_from is not None:
        stage = _TITLES[info.informed_from].lower()
        lines.append(f"You receive nothing about the bank before this stage: {stage}.")
    lines.append(
        "A stage change reaches you the same day, with the figures that triggered it as "
        "passed on by the sender. Your regular report is separate and may be older."
    )
    return tuple(lines)


# Which lines of the resolution summary belong to which group of figures.
_GROUP_PREFIXES = {
    "capital": ("Losses booked", "CET1 at FoLtF", "Valuation write-down", "CET1 after", "Need:"),
    "mrel": ("MREL requirement", "Available MREL", "Write-down at which"),
    "losses": ("AT1 and Tier 2", "Senior non-preferred", "Senior preferred", "Uncovered deposits",
               "Covered deposits", "Shortfall", "Liquidation", "DGS", "Time to pay"),
    "liquidity": ("Liquidity need", "Of which requested"),
}


def _resolution_figures(
    summary: ResolutionSummary, groups: Sequence[str], unit: str, day: int,
    hostile_need: Optional[float] = None,
) -> tuple[Figure, ...]:
    """The lines of the resolution summary that belong to the given groups."""
    prefixes = tuple(prefix for group in groups for prefix in _GROUP_PREFIXES[group])
    return tuple(
        Figure(f"resolution_{index}", label, 0.0, day, f"{label} {value}")
        for index, (label, value) in enumerate(summary_rows(summary, unit, hostile_need))
        if prefixes and label.startswith(prefixes)
    )


def sheet_id(actor: str, section: str, index: int) -> str:
    """Number of an inject sheet, e.g. 'RA-A03': third sheet of branch A for that player."""
    return f"{ACTOR_CODES[actor]}-{section}{index + 1:02d}"


def active_challenges(
    script: ScriptConfig, selected: Optional[Sequence[str]] = None, hostile: bool = False
) -> tuple[str, ...]:
    """The challenge injects in play: those selected, or the defaults for the assumptions."""
    if selected is not None:
        unknown = set(selected) - set(script.challenges)
        if unknown:
            raise ValueError(f"unknown challenge injects: {sorted(unknown)}")
        return tuple(key for key in script.challenges if key in selected)
    return tuple(
        key for key, item in script.challenges.items()
        if item.default or (hostile and item.hostile)
    )


def scenario_with_challenges(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    challenges: Sequence[str],
) -> Scenario:
    """The scenario with the challenge injects that are public news events added to it.

    Such an inject is placed after its stage on the path without decisions, so that it falls
    on the same day whatever the players decide. All other challenge injects change no figure
    and leave the scenario as it is.
    """
    items = [
        script.challenges[key] for key in challenges
        if script.challenges[key].effect == "panic_shock"
    ]
    if not items:
        return scenario
    base = simulate(bank, params, scenario, decisions, ())
    stage_days = {e.kind: e.day for e in base.events if e.kind in DECISION_STAGES}
    resolved = params.with_overrides(scenario.overrides)
    keys = {id(item): key for key, item in script.challenges.items()}
    extra = []
    for item in items:
        anchor = stage_days.get(item.anchor)
        headline = item.text.format(bank_name=bank.name)
        if anchor is None or anchor + item.offset_days > int(params.horizon_days.value):
            continue
        if any(event.headline == headline for event in scenario.news_events):
            continue  # already added: calling this twice changes nothing
        key = keys[id(item)]  # its two effects are the model parameters named after it
        extra.append(
            NewsEvent(day=anchor + item.offset_days, headline=headline,
                      panic_shock=getattr(resolved, f"{key}_panic_shock").value,
                      rollover_cut=getattr(resolved, f"{key}_rollover_cut").value)
        )
    events = sorted([*scenario.news_events, *extra], key=lambda event: event.day)
    return scenario.model_copy(update={"news_events": events})


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
    ambient: bool = True,
    writedown_level: str = "central",
    linear: bool = False,
    challenges: Sequence[str] = (),
    hostile: bool = False,
    with_points: bool = True,
) -> BranchScript:
    """Simulate one decision path and write every actor's injects for it.

    `scenario` is used as given: a challenge inject that is a news event must already be in
    it (see scenario_with_challenges).
    """
    result = simulate(bank, params, scenario, decisions, choices)
    resolved = params.with_overrides(scenario.overrides)
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
        report = [
            Figure(
                name=figure,
                label=FIGURE_LABELS[figure][0],
                value=float(row[figure]),
                as_of_day=as_of,
                text=format_figure(figure, float(row[figure]), bank.unit),
            )
            for figure in info.figures
        ]
        if info.intraday and row["outflow_total"] > 0:
            for block, amount, _ in intraday_allocation(float(row["outflow_total"]), resolved):
                label = f"Outflow {block}"
                report.append(
                    Figure("outflow_total", label, amount, as_of,
                           f"{label} {amount:.2f} {bank.unit}", block=block)
                )
        return tuple(report)

    def trigger_figures(event) -> list[Figure]:
        """The figures that triggered a stage change, as the bank reports them that day."""
        out = []
        for figure, value, threshold in event.figures:
            text = format_figure(figure, value, bank.unit)
            label = FIGURE_LABELS[figure][0]
            limit = "" if figure == "unmet_outflow" else format_figure(
                figure, threshold, bank.unit)[len(label) + 1:]
            out.append(Figure(figure, label, float(value), event.day, text, threshold_text=limit))
        return out

    def make(actor: str, day: int, kind: str, clock: str, sender: str, text: str,
             with_figures: bool = True, due: Optional[DecisionDue] = None, **extra) -> Inject:
        return Inject(
            day=day,
            weekday=scenario.weekday(day) if day >= 1 else "",
            time=script.clock.get(clock, clock),
            recipient=actor,
            sender=sender,
            kind=kind,
            title=extra.pop("title", _TITLES[kind]),
            text=text,
            figures=figures(actor, day) if with_figures else (),
            decision_due=due,
            **extra,
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
        """On this branch: the last close before its payments default or resolution day,
        or a fixed window if the branch has neither."""
        if ended:
            return max(open_day, end_day - int(decisions.lead_days.value))
        return min(horizon, open_day + int(script.decision_window_days.value))

    def timed(day: int, clock: str) -> str:
        if day < 1:
            return "Time: the start of the exercise"
        return f"Time: day {day} ({scenario.weekday(day)}), {script.clock.get(clock, clock)}"

    def role_name(role: str) -> str:
        return CONTROL if role == CONTROL_TEAM else script.roles[role].name

    def after_anchor(anchor: str, offset: int) -> str:
        what = "FoLtF in effect" if anchor == "foltf" else _TITLES[anchor].lower()
        if offset == 0:
            return f"Time: the day of: {what}"
        return f"Time: {offset} day{'s' if offset > 1 else ''} after: {what}"

    def passed_on(by: str, day: int, clock: str) -> dict:
        """Delivery of what the player `by` should tell the recipient itself."""
        if by not in PLAYERS:  # a stakeholder played by the control team
            return {"delivered_by": script.actors[by], "trigger": timed(day, clock)}
        if linear:
            return {"trigger": timed(day, clock)}
        sender = script.actors[by].lower()
        return {
            "purpose": "control",
            "fallback": True,
            "delivered_by": f"{CONTROL}, in place of the {sender}",
            "trigger": f"Fallback: only if the {sender} has not passed this on by "
                       f"{script.clock.get(clock, clock)} on day {day}",
        }

    def first_stage_of(actor: str) -> int:
        needed = decisions.information[actor].informed_from
        return _STAGE_OF[needed] if needed else 0

    open_days = {key: condition_day(key) for key in decisions.decisions}
    levels = resolution_levels(bank, params, result)
    hostile_need = (
        hostile_liquidity_need(levels[writedown_level], resolved)
        if hostile and levels is not None else None
    )
    names = script.actors
    # Challenges on the bank's own notification: day of the stage -> the challenge played.
    tampered: dict[int, object] = {}
    for key in challenges:
        item = script.challenges[key]
        if item.effect in ("incomplete", "misdirected") and item.anchor in stage_days:
            tampered.setdefault(stage_days[item.anchor], item)
    media = script.roles["media"].name
    # Challenge injects that are public news: headline -> expected response.
    public_challenges = {
        script.challenges[key].text.format(bank_name=bank.name): script.challenges[key].expected
        for key in challenges if script.challenges[key].effect == "panic_shock"
    }
    injects: dict[str, list[Inject]] = {actor: [] for actor in ACTORS}

    for actor in ACTORS:
        info = decisions.information[actor]
        if actor == "bank_management":
            sender, clock = _capital(info.source), "stage"
        elif actor == "competent_authority":
            sender, clock = names["bank_management"], "stage_relay"
        else:
            sender, clock = names["competent_authority"], "stage_relay_others"
        out = injects[actor]
        told: dict[int, list] = {}  # day -> stage events this actor is notified of
        out.append(
            make(actor, 0, "opening", "opening", "Exercise control",
                 script.texts["opening"].format(bank_name=bank.name, scenario_name=scenario.name),
                 with_figures=is_informed(actor, 0),
                 trigger=timed(0, "opening"), expected=script.expected["opening"])
        )
        first_stage = first_stage_of(actor)
        for event in result.events:
            if event.kind == "news":
                challenge = event.detail in public_challenges
                out.append(
                    make(actor, event.day, "news", "news", "Public media",
                         script.texts["news"].format(headline=event.detail), with_figures=False,
                         purpose="challenge" if challenge else "advance",
                         delivered_by=media, trigger=timed(event.day, "news"),
                         expected=public_challenges.get(event.detail, script.expected["news"]))
                )
            elif event.kind in DECISION_STAGES:
                if _STAGE_OF[event.kind] >= first_stage and is_informed(actor, event.day):
                    told.setdefault(event.day, []).append(event)
            elif event.kind == "foltf" and is_informed(actor, event.day):
                delivery = (
                    {"purpose": "control", "trigger": "Milestone: FoLtF is declared; in effect "
                                                      f"on day {event.day}"}
                    if actor == "competent_authority"
                    else passed_on("competent_authority", event.day, "decision_taken")
                )
                out.append(
                    make(actor, event.day, "foltf", "decision_taken", names["competent_authority"],
                         script.texts["foltf"].format(bank_name=bank.name),
                         expected=script.expected["foltf"], **delivery)
                )
            elif event.kind == "resolution" and is_informed(actor, event.day):
                out.append(
                    make(actor, event.day, "resolution", "end", names["resolution_authority"],
                         script.texts["resolution"].format(
                             bank_name=bank.name, outcome=OUTCOME_LABELS[result.outcome].lower()),
                         purpose="control", expected=script.expected["resolution"],
                         trigger=f"Milestone: the tool is applied or the bank is wound up "
                                 f"(day {event.day})")
                )
        for day, stage_events in told.items():
            # One inject for everything that reaches the actor at that time.
            merged: dict[str, Figure] = {}
            for event in stage_events:
                for figure in trigger_figures(event):
                    merged[figure.name] = figure
            highest = stage_events[-1].kind
            if actor == "bank_management":
                delivery = {"trigger": f"Milestone: {_TITLES[highest].lower()} (close of day {day})"}
            elif actor == "competent_authority":
                relay_to = [
                    names[other] for other in PLAYERS
                    if other != actor and _STAGE_OF[highest] >= first_stage_of(other)
                    and is_informed(other, day)
                ]
                delivery = {
                    "delivered_by": names["bank_management"],
                    "trigger": f"Milestone: {_TITLES[highest].lower()} (close of day {day})",
                    "expected": script.expected["stage"].format(
                        inform=", ".join(relay_to) or "nobody yet"),
                }
            else:
                delivery = {"expected": script.expected["stage_relayed"],
                            **passed_on("competent_authority", day, clock)}
            text = "\n".join(
                script.texts[event.kind].format(bank_name=bank.name, sender=sender)
                for event in stage_events)
            shown = tuple(merged.values())
            challenge = tampered.get(day) if actor == "competent_authority" else None
            if challenge is not None and challenge.effect == "incomplete":
                # Only the first figure arrives; the rest has to be asked for.
                shown = shown[:1]
                text += "\n" + challenge.text.format(bank_name=bank.name)
                delivery.update(purpose="challenge", expected=challenge.expected)
            elif challenge is not None:  # misdirected: it went to another player
                holder = challenge.recipients[0]
                clock = "stage_relay_others"
                delivery = {"expected": delivery["expected"], **passed_on(holder, day, clock)}
                if linear:
                    delivery["purpose"] = "control"
            out.append(
                make(actor, day, highest, clock, sender, text,
                     with_figures=False,
                     headline=shown,
                     stages=tuple(event.kind for event in stage_events),
                     **delivery)
            )
        for day, challenge in tampered.items():
            if challenge.effect != "misdirected" or actor not in challenge.recipients:
                continue
            # The bank's notification, with its figures, reaches this player instead.
            bank_name = names["bank_management"]
            stage_events = [e for e in result.events if e.kind in DECISION_STAGES and e.day == day]
            merged = {f.name: f for event in stage_events for f in trigger_figures(event)}
            out.append(
                make(actor, day, "challenge", "stage_relay", bank_name,
                     "\n".join([
                         challenge.text.format(bank_name=bank.name),
                         *(script.texts[event.kind].format(bank_name=bank.name, sender=bank_name)
                           for event in stage_events)]),
                     with_figures=False, title=challenge.title,
                     headline=tuple(merged.values()),
                     stages=tuple(event.kind for event in stage_events),
                     purpose="challenge", delivered_by=role_name(challenge.role),
                     expected=challenge.expected,
                     trigger=f"Milestone: {_TITLES[stage_events[-1].kind].lower()} "
                             f"(close of day {day})")
            )
        for record in result.decision_log:
            option = decisions.decisions[record.decision].options[record.option]
            if option.effect in ("declare_foltf", "resolution_tool"):
                continue  # announced to everyone informed by the FoLtF and end-of-run injects
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
                     with_figures=False, expected=script.expected["decision_taken"],
                     **passed_on(record.actor, record.effect_day, "decision_taken"))
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
                    # A linear exercise has no branches to refer to.
                    OptionLine(option_key, option.name,
                               "" if linear else branch_code(decisions, key, option_key),
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
            known: dict[str, Figure] = {}
            for earlier_inject in sorted(out, key=lambda i: i.day):
                if earlier_inject.day <= open_day:
                    known.update({f.name: f for f in earlier_inject.headline})
            out.append(
                make(actor, open_day, "decision_due", "decision_due", "Exercise control",
                     text, due=due, notified=tuple(known.values()),
                     trigger=f"Milestone: the decision opens (close of day {open_day})",
                     expected=script.expected["decision_due"].format(
                         consult=", ".join(due.consult) or "nobody",
                         inform=", ".join(due.inform) or "nobody"))
            )
            if linear and actor in PLAYERS:
                # A linear exercise keeps to its path: if the players decide otherwise, the
                # control team corrects course (EBA Handbook, par. 126-127).
                record = decided.get(key)
                option = definition.options[record.option if record else definition.default_option]
                # At the earliest moment it can be needed: the day after the decision.
                day = record.effect_day if record else open_day + int(decisions.lead_days.value)
                if day <= end_day:
                    out.append(
                        make(actor, day, "course_correction", "decision_taken", "Exercise control",
                             script.texts["course_correction"].format(
                                 decision_name=definition.name, option_name=option.name),
                             with_figures=False, purpose="control",
                             trigger=f'Milestone: only if, and as soon as, the decision on '
                                     f'"{definition.name}" differs from the fixed path: '
                                     f"{option.name}",
                             expected=script.expected["course_correction"])
                    )
        if levels is not None and result.foltf_day is not None:
            summary = levels[writedown_level]
            tool = OUTCOME_LABELS[result.outcome].lower()
            for item in script.resolution.values():
                day = min(result.foltf_day + item.offset_days, end_day)
                if actor not in item.recipients or not is_informed(actor, day):
                    continue
                out.append(
                    make(actor, day, "resolution_step", item.time, item.sender,
                         item.text.format(bank_name=bank.name, tool=tool), with_figures=False,
                         title=item.title,
                         resolution=_resolution_figures(
                             summary, item.figures, bank.unit, day, hostile_need),
                         delivered_by=role_name(item.role), expected=item.expected,
                         trigger=after_anchor("foltf", day - result.foltf_day)
                                 + f", {item.time}")
                )
        if ambient:
            for item in script.ambient.values():
                anchor = stage_days.get(item.anchor) if item.anchor in DECISION_STAGES else (
                    result.foltf_day
                )
                if anchor is None or actor not in item.recipients:
                    continue
                day = anchor + item.offset_days
                if day > end_day or not is_informed(actor, day):
                    continue
                out.append(
                    make(actor, day, "ambient", item.time, item.sender,
                         item.text.format(bank_name=bank.name), with_figures=False,
                         title=item.title, purpose="challenge",
                         delivered_by=role_name(item.role), expected=item.expected,
                         trigger=after_anchor(item.anchor, item.offset_days) + f", {item.time}")
                )
        for key in challenges:
            item = script.challenges[key]
            if item.effect in ("panic_shock", "incomplete", "misdirected") \
                    or actor not in item.recipients:
                continue  # a news event is in the scenario; the other two are handled above
            anchor = stage_days.get(item.anchor) if item.anchor in DECISION_STAGES else (
                result.foltf_day
            )
            if anchor is None or anchor + item.offset_days > end_day:
                continue
            day = anchor + item.offset_days
            if not item.public and not is_informed(actor, day):
                continue
            estimates: tuple[Figure, ...] = ()
            if item.effect == "second_valuation":
                # The level above the one the players were first given; none above "high".
                position = LEVELS.index(writedown_level)
                if levels is None or position == len(LEVELS) - 1:
                    continue
                worse = levels[LEVELS[position + 1]]
                estimates = _resolution_figures(
                    worse, item.figures, bank.unit, day,
                    hostile_liquidity_need(worse, resolved) if hostile else None)
            elif item.effect == "indicative_bid":
                if levels is None:
                    continue
                bid = indicative_bid(levels[writedown_level], resolved)
                bid_label = f"Indicative price, non-binding ({bank.unit})"
                estimates = (
                    Figure("indicative_bid", bid_label, bid, day, f"{bid_label} {bid:.2f}"),
                )
            out.append(
                make(actor, day, "challenge", item.time, item.sender,
                     item.text.format(bank_name=bank.name), with_figures=False,
                     title=item.title, resolution=estimates, purpose="challenge",
                     delivered_by=role_name(item.role), expected=item.expected,
                     trigger=after_anchor(item.anchor, item.offset_days) + f", {item.time}")
            )
        if not ended and is_informed(actor, end_day):
            out.append(
                make(actor, end_day, "end_open", "end", "Exercise control",
                     script.texts["end_open"].format(bank_name=bank.name, horizon_days=horizon),
                     purpose="control", trigger=timed(end_day, "end"),
                     expected=script.expected["end_open"])
            )
        out.sort(key=lambda i: (i.day, i.time, _KIND_ORDER.index(i.kind)))

    # Control team: where each option of each open decision leads on this path.
    points = []
    for key, definition in decisions.decisions.items():
        open_day = open_days[key]
        if open_day is None or not with_points:  # live mode does not need the other runs
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
        resolution=levels,
    )


# --------------------------------------------------------------------------- the pack


def _shared_inject(first: Inject, second: Inject) -> Inject:
    """An inject of the shared part: where the branches' deadlines differ, the earlier one."""
    if first.decision_due is None or first.decision_due.deadline_day == second.decision_due.deadline_day:
        return first
    earlier = min(first.decision_due.deadline_day, second.decision_due.deadline_day)
    return replace(
        first, decision_due=replace(first.decision_due, deadline_day=earlier, deadline_shared=True)
    )


def _main_events(documents: Sequence[ActorDocument]) -> tuple[MainEvent, ...]:
    """The main events list: every inject to a player, in order, one line per inject.

    The same inject to several players is one line. Injects of the shared part come first,
    then each branch.
    """
    merged: dict[tuple, dict] = {}
    for document in documents:
        if document.actor not in PLAYERS:
            continue
        sections = [("", document.shared)] + [
(title.split()[1], injects) for title, injects in document.branches
        ]
        for section, injects in sections:
            for index, inject in enumerate(injects):
                key = (section, inject.day, inject.time, inject.kind, inject.title, inject.sender,
                       inject.text, inject.purpose, inject.delivered_by, inject.trigger,
                       inject.expected)
                entry = merged.setdefault(key, {"inject": inject, "to": [], "sheets": []})
                entry["to"].append(document.actor)
                entry["sheets"].append(sheet_id(document.actor, section, index))
    ordered = sorted(
        merged.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2], _KIND_ORDER.index(item[0][3])),
    )
    return tuple(
        MainEvent(
            number=number,
            section=key[0],
            day=entry["inject"].day,
            weekday=entry["inject"].weekday,
            time=entry["inject"].time,
            kind=entry["inject"].kind,
            title=entry["inject"].title,
            text=entry["inject"].text,
            recipients=tuple(entry["to"]),
            sheets=tuple(entry["sheets"]),
            delivered_by=entry["inject"].delivered_by,
            purpose=entry["inject"].purpose,
            trigger=entry["inject"].trigger,
            expected=entry["inject"].expected,
            fallback=entry["inject"].fallback,
        )
        for number, (key, entry) in enumerate(ordered, start=1)
    )


def _lower_first(text: str) -> str:
    return text if text.startswith("FoLtF") else text[:1].lower() + text[1:]


def _per_branch(blocks: Sequence[tuple[str, tuple]]) -> list[tuple[str, tuple]]:
    """(branch label, rows) per branch; one block for all if the branches agree."""
    if len(blocks) > 1 and all(rows == blocks[0][1] for _, rows in blocks):
        return [("All branches", blocks[0][1])]
    return [(f"Branch {label}" if len(blocks) > 1 else "", rows) for label, rows in blocks]


def _role_cards(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    branches: Sequence[BranchScript],
    events: Sequence[MainEvent],
    writedown_level: str,
    challenges: Sequence[str],
) -> tuple[RoleCard, ...]:
    """One card per stakeholder the control team plays: texts from config, figures added here."""
    resolved = params.with_overrides(scenario.overrides)
    unit = bank.unit

    def day_text(day: Optional[int]) -> str:
        if day is None:
            return "not reached"
        return f"day {day} ({scenario.weekday(day)})" if day >= 1 else "from the start"

    def moment_day(branch: BranchScript, moment: str) -> Optional[int]:
        result = branch.result
        if moment == "normal":
            return 0
        if moment == "foltf":
            return result.foltf_day
        if moment == "resolution":
            return result.end_day if branch.resolution is not None else None
        return next((e.day for e in result.events if e.kind == moment), None)

    def when(moment: str) -> str:
        blocks = _per_branch([(b.label, (day_text(moment_day(b, moment)),)) for b in branches])
        return "; ".join(f"{heading}: {rows[0]}" if heading else rows[0] for heading, rows in blocks)

    def headed(title: str, heading: str) -> str:
        return f"{title} ({heading})" if heading else title

    def bank_figures() -> list[tuple[str, tuple]]:
        info = decisions.information["bank_management"]
        moments = [m for m in ROLE_STAGES if m not in ("normal", "resolution")]
        found: dict[tuple, list[str]] = {}  # (heading, rows) -> the branches it holds on
        for branch in branches:
            table = branch.result.table.set_index("day")
            by_day: dict[int, list[str]] = {}
            for moment in moments:
                day = moment_day(branch, moment)
                if day is not None and day <= branch.result.end_day:
                    by_day.setdefault(day, []).append(_lower_first(ROLE_STAGES[moment]))
            for day, reached in sorted(by_day.items()):
                rows = tuple(
                    (FIGURE_LABELS[name][0],
                     format_figure(name, float(table.loc[day, name]), unit)[
                         len(FIGURE_LABELS[name][0]) + 1:])
                    for name in info.figures
                )
                heading = f"Your figures at the close of {day_text(day)}: {'; '.join(reached)}"
                found.setdefault((heading, rows), []).append(branch.label)
        unique = [
            (heading if len(on) == len(branches) else f"{heading} (Branch {', '.join(on)})", rows)
            for (heading, rows), on in found.items()
        ]
        mine = [key for key, d in decisions.decisions.items() if d.actor == "bank_management"]
        scripted = _per_branch([
            (branch.label, tuple(
                (decisions.decisions[key].name,
                 next((f"{r.option_name}, decided at the close of day {r.decided_day}"
                       for r in branch.result.decision_log if r.decision == key),
                      "no decision: "
                      + decisions.decisions[key].options[decisions.decisions[key].default_option].name))
                for key in mine))
            for branch in branches
        ])
        unique += [(headed("Your decisions, as scripted", heading), rows) for heading, rows in scripted]
        return unique

    def valuer_figures() -> list[tuple[str, tuple]]:
        blocks = []
        for branch in branches:
            if branch.resolution is None:
                blocks.append((branch.label, ()))
                continue
            rows = {level: dict(_resolution_rows(branch.resolution[level], unit)) for level in LEVELS}
            position = LEVELS.index(writedown_level)
            second = (LEVELS[position + 1] if "second_valuation" in challenges
                      and position < len(LEVELS) - 1 else None)
            blocks.append((branch.label, (
                ("Provisional valuation uses", writedown_level),
                ("Updated valuation uses", second or "not played"),
                *((label, " | ".join(rows[level][label] for level in LEVELS))
                  for label in rows["central"]),
            )))
        return [
            (headed("Valuation figures: low | central | high", heading), rows)
            for heading, rows in _per_branch(blocks) if rows
        ]

    def buyer_figures() -> list[tuple[str, tuple]]:
        share = resolved.buyer_indicative_bid_share.value
        blocks = [
            (branch.label, () if branch.resolution is None else (
                (f"Indicative price, non-binding ({unit})",
                 f"{indicative_bid(branch.resolution[writedown_level], resolved):.2f}"),
                ("How it is set (assumption)",
                 f"{100 * share:.0f}% of the CET1 the bank holds once it is back at its "
                 f"requirement after the {writedown_level} write-down"),
            ))
            for branch in branches
        ]
        return [(headed("Your price", heading), rows)
                for heading, rows in _per_branch(blocks) if rows]

    def media_figures() -> list[tuple[str, tuple]]:
        blocks = [
            (branch.label, tuple(
                (f"Day {event.day} ({scenario.weekday(event.day)})", event.detail)
                for event in branch.result.events if event.kind == "news"))
            for branch in branches
        ]
        return [(headed("The news you report", heading), rows)
                for heading, rows in _per_branch(blocks) if rows]

    figures = {
        "bank_management": bank_figures,
        "independent_valuer": valuer_figures,
        "media": media_figures,
        "finance_minister": lambda: [],
        "potential_buyers": buyer_figures,
    }
    cards = []
    for role in ROLES:
        text = script.roles[role]
        fill = lambda line: line.format(bank_name=bank.name)  # noqa: E731
        cards.append(
            RoleCard(
                role=role,
                name=text.name,
                knows=tuple(fill(line) for line in text.knows),
                wants=tuple(fill(line) for line in text.wants),
                lines=tuple(
                    (ROLE_STAGES[moment], when(moment), fill(text.lines[moment]))
                    for moment in ROLE_STAGES if moment in text.lines
                ),
                figures=tuple(figures[role]()),
                delivers=tuple(event for event in events if event.delivered_by == text.name),
            )
        )
    return tuple(cards)


def _resolution_rows(summary: ResolutionSummary, unit: str) -> list[tuple[str, str]]:
    """The valuer's lines of the resolution summary: capital and MREL."""
    prefixes = _GROUP_PREFIXES["capital"] + _GROUP_PREFIXES["mrel"]
    return [(label, value) for label, value in summary_rows(summary, unit)
            if label.startswith(prefixes)]


def build_pack(
    bank: Bank,
    params: ModelParams,
    scenario: Scenario,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    paths: Sequence[tuple[str, Sequence[Choice]]],
    ambient: bool = True,
    writedown_level: str = "central",
    mode: str = "dynamic",
    challenges: Optional[Sequence[str]] = None,
    hostile: bool = False,
    bank_alias: Optional[str] = None,
    background_key: str = "default",
) -> ExercisePack:
    """Build the exercise pack for one or two decision paths: (name, choices) each.

    With two paths every actor's document has a shared part, up to the point where the two
    paths differ for that actor, and then one section per branch. A linear exercise uses
    one path only (the last one given) and its decision injects carry no branch codes.

    challenges: the challenge injects to play; None for the defaults, which depend on
    `hostile` (friendly or hostile assumptions).
    bank_alias: a neutral name used for the bank in every document (EBA Handbook, par. 174).
    background_key: which economic background of the config the dossier uses (a scenario key).
    """
    if bank_alias:
        bank = bank.model_copy(update={"name": bank_alias})
    if not 1 <= len(paths) <= 2:
        raise ValueError("an exercise pack takes one or two decision paths")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "linear":
        paths = paths[-1:]  # one fixed path: the last one given
    active = active_challenges(script, challenges, hostile)
    scenario = scenario_with_challenges(bank, params, scenario, decisions, script, active)
    branches = tuple(
        build_branch("AB"[index], name, bank, params, scenario, decisions, script, choices,
                     ambient, writedown_level, mode == "linear", active, hostile)
        for index, (name, choices) in enumerate(paths)
    )

    def without_deadline(inject: Inject) -> Inject:
        if inject.decision_due is None:
            return inject
        return replace(inject, decision_due=replace(inject.decision_due, deadline_day=0))

    documents = []
    for actor in ACTORS:
        streams = [branch.injects[actor] for branch in branches]
        common = 0
        if len(streams) == 2:
            # Deadlines belong to a branch, so they are left out of the comparison.
            while (common < min(len(streams[0]), len(streams[1]))
                   and without_deadline(streams[0][common]) == without_deadline(streams[1][common])):
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
                shared=tuple(
                    _shared_inject(a, b) for a, b in zip(streams[0][:common], streams[-1][:common])
                ),
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
    events = _main_events(documents)
    return ExercisePack(
        bank_name=bank.name,
        scenario_name=scenario.name,
        unit=bank.unit,
        start_weekday=scenario.start_weekday,
        horizon=int(params.horizon_days.value),
        branches=branches,
        documents=tuple(documents),
        information_rows=information_rows,
        ambient_on=ambient,
        writedown_level=writedown_level,
        mode=mode,
        taxonomy=scenario.taxonomy.label() if scenario.taxonomy else "",
        challenges=active,
        hostile=hostile,
        events=events,
        roles=_role_cards(bank, params, scenario, decisions, script, branches, events,
                          writedown_level, active),
        dossier=build_dossier(bank, branches[0].result.rows[0], script, background_key),
    )
