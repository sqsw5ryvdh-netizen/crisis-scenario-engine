"""Set-up documents of an exercise (SPEC_V3 G7, G8, G10): the mock bank dossier, the session
plan, the SimEx Charter draft, the rules of play and the observer sheets.

Pure functions. The texts come from config/script.yaml (`setup`); the figures are added here
from the bank's opening position and from the exercise pack. Everything pre-filled is a draft
for the design team to edit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from engine.resolution import liability_stack
from engine.schema import PLAYERS, RULE_CATEGORIES, Bank, DecisionsConfig, ScriptConfig

if TYPE_CHECKING:  # the pack is built in engine.script, which imports this module
    from engine.script import ExercisePack

DRAFT_MARK = "DRAFT - to edit"
BLANK = ""


@dataclass(frozen=True)
class Dossier:
    """The mock bank as the players know it before the exercise starts (day 0)."""

    bank_name: str
    country: str
    unit: str
    business_model: str
    background: str
    assets: tuple[tuple[str, float], ...]
    liabilities: tuple[tuple[str, float], ...]
    stack: tuple[tuple[str, float], ...]  # loss-absorbing order, then covered deposits
    indicators: tuple[tuple[str, str], ...]


def build_dossier(
    bank: Bank, opening: dict, script: ScriptConfig, background_key: str = "default"
) -> Dossier:
    """The dossier from the bank's opening row (day 0 of a run). No real names."""
    setup = script.setup
    names = {"bank_name": bank.name, "country": setup.country_alias}
    background = setup.background.get(background_key, setup.background["default"])
    unit = bank.unit
    assets = (
        ("Cash and central bank reserves", opening["cash"]),
        ("Liquid securities, level 1", opening["hqla_level1"]),
        ("Liquid securities, level 2", opening["hqla_level2"]),
        ("Other assets eligible at the central bank", opening["eligible_collateral"]),
        ("Loans and other illiquid assets", opening["illiquid_assets"]),
    )
    liabilities = (
        ("Retail deposits, covered", opening["dep_insured_retail"]),
        ("Retail deposits, not covered", opening["dep_uninsured_retail"]),
        ("Corporate deposits", opening["dep_corporate"]),
        ("Deposits of financial institutions", opening["dep_financial"]),
        ("Market funding (debt issued)", opening["wholesale_funding"]),
        ("Central bank funding", opening["central_bank_funding"]),
        ("Other liabilities", opening["other_liabilities"]),
        ("CET1 capital", opening["cet1"]),
    )
    stack = (
        ("CET1 (shareholders)", opening["cet1"]),
        *((label, amount) for _, label, amount in liability_stack(bank, opening)),
        ("Covered deposits (protected)", opening["dep_insured_retail"]),
    )
    rwa = opening["rwa"]
    indicators = (
        (f"Total assets ({unit})", f"{opening['total_assets']:.2f}"),
        (f"Risk-weighted assets ({unit})", f"{rwa:.2f}"),
        ("CET1 ratio", f"{100 * opening['cet1_ratio']:.1f}%"),
        ("CET1 requirement", f"{100 * bank.capital.capital_requirement_ratio:.1f}%"),
        ("LCR proxy", f"{100 * opening['lcr_proxy']:.1f}%"),
        (f"Liquidity buffer ({unit})", f"{opening['liquidity_buffer']:.2f}"),
        (f"MREL requirement ({unit})",
         f"{bank.resolution.mrel_requirement_ratio * rwa:.2f} "
         f"({100 * bank.resolution.mrel_requirement_ratio:.1f}% of risk-weighted assets)"),
        (f"Available MREL ({unit})", f"{bank.mrel_available:.2f}"),
        ("Deposits covered by the guarantee scheme",
         f"{100 * opening['dep_insured_retail'] / opening['deposits_total']:.0f}% of deposits"),
    )
    return Dossier(
        bank_name=bank.name,
        country=setup.country_alias,
        unit=unit,
        business_model=bank.description,
        background=background.format(**names),
        assets=assets,
        liabilities=liabilities,
        stack=stack,
        indicators=indicators,
    )


# --------------------------------------------------------------------------- session plan


@dataclass(frozen=True)
class Session:
    number: int
    first_day: int
    last_day: int
    injects: int
    ends_with: str  # what the break after this session coincides with
    minutes_per_day: float  # real minutes per simulated day: the acceleration of time
    injects_text: str = ""  # where one plan stands for several branches: injects per branch


FOLTF_BREAK = "Break at the FoLtF determination"
TOOL_BREAK = "Break at the resolution decision"
PAUSE = "Planned pause"
END = "End of the exercise"


def _split(days: list[int], counts: Counter, parts: int) -> list[list[int]]:
    """Cut consecutive days into `parts` runs with about the same number of injects each."""
    parts = max(1, min(parts, len(days)))
    total = sum(counts[day] for day in days)
    runs, current, done = [], [], 0
    for position, day in enumerate(days):
        current.append(day)
        done += counts[day]
        left = len(days) - position - 1
        need = parts - len(runs) - 1  # runs still to open after this one
        if need > 0 and (done >= total * (len(runs) + 1) / parts or left == need):
            runs.append(current)
            current = []
    runs.append(current)
    return [run for run in runs if run]


def session_plan(
    pack: "ExercisePack", decisions: DecisionsConfig, branch_label: str,
    sessions: int, hours: float,
) -> tuple[Session, ...]:
    """Map the simulated days of one branch to real sessions (EBA Handbook, par. 139-143).

    Breaks coincide with the FoLtF determination and with the resolution decision where the
    branch has them and there are enough sessions; the other breaks are planned pauses.
    Within a phase the days are shared out so that each session has about as many injects.
    """
    if sessions < 1 or hours <= 0:
        raise ValueError("a session plan needs at least one session of positive length")
    branch = next(b for b in pack.branches if b.label == branch_label)
    result = branch.result
    counts = Counter(
        event.day for event in pack.events if event.section in ("", branch_label)
    )
    decided = {}
    for record in result.decision_log:
        effect = decisions.decisions[record.decision].options[record.option].effect
        decided.setdefault(effect, record.decided_day)
    cuts = []  # (last day of the phase, what the break coincides with)
    if "declare_foltf" in decided and decided["declare_foltf"] < result.end_day:
        cuts.append((decided["declare_foltf"], FOLTF_BREAK))
    if "resolution_tool" in decided and decided["resolution_tool"] < result.end_day:
        if not cuts or decided["resolution_tool"] > cuts[-1][0]:
            cuts.append((decided["resolution_tool"], TOOL_BREAK))
    cuts.append((result.end_day, END))
    phases, first = [], 0
    for last, label in cuts:
        phases.append((list(range(first, last + 1)), label))
        first = last + 1
    # Too few sessions for every break: join the two neighbouring phases with the fewest injects.
    weight = lambda days: sum(counts[day] for day in days)  # noqa: E731
    while len(phases) > sessions:
        position = min(
            range(len(phases) - 1),
            key=lambda i: weight(phases[i][0]) + weight(phases[i + 1][0]),
        )
        joined = (phases[position][0] + phases[position + 1][0], phases[position + 1][1])
        phases[position:position + 2] = [joined]
    # Spare sessions go to the phases with the most injects per session.
    share = [1] * len(phases)
    for _ in range(sessions - len(phases)):
        open_to = [i for i in range(len(phases)) if share[i] < len(phases[i][0])]
        if not open_to:
            break
        share[max(open_to, key=lambda i: weight(phases[i][0]) / share[i])] += 1
    plan = []
    for (days, label), parts in zip(phases, share):
        runs = _split(days, counts, parts)
        for position, run in enumerate(runs):
            plan.append(
                Session(
                    number=len(plan) + 1,
                    first_day=run[0],
                    last_day=run[-1],
                    injects=weight(run),
                    ends_with=label if position == len(runs) - 1 else PAUSE,
                    minutes_per_day=60.0 * hours / len(run),
                )
            )
    return tuple(plan)


# --------------------------------------------------------------------------- charter, rules


def player_decisions(pack: "ExercisePack") -> tuple[str, ...]:
    """Names of the decisions open to a player somewhere in the pack, in order."""
    names = []
    for branch in pack.branches:
        for point in branch.decision_points:
            if point.actor in PLAYERS and point.decision_name not in names:
                names.append(point.decision_name)
    return tuple(names)


def charter_rows(pack: "ExercisePack", script: ScriptConfig) -> list[tuple[str, str, str]]:
    """The SimEx Charter (Annex 1): element, description, and a draft of the concrete elements.

    A pre-filled cell starts with the draft mark; an empty one is left to the design team.
    """
    from engine.script import MODE_LABELS  # late import: engine.script imports this module

    values = {
        "bank_name": pack.bank_name,
        "taxonomy": pack.taxonomy or "not classified",
        "mode": MODE_LABELS[pack.mode].lower(),
        "decisions": "; ".join(player_decisions(pack)) or "none",
        "challenges": "; ".join(
            script.challenges[key].title.lower() for key in pack.challenges) or "none",
    }
    return [
        (row.element, row.description,
         f"[{DRAFT_MARK}] {row.draft.format(**values)}" if row.draft else BLANK)
        for row in script.setup.charter
    ]


def rules_of_play(pack: "ExercisePack", script: ScriptConfig) -> list[tuple[str, list[str]]]:
    """Rules of play by category (EBA Handbook, par. 149-155): config rules plus those that
    depend on the pack. Nothing here gives away an inject or an outcome."""
    from engine.script import MODE_TEXT

    names = {"bank_name": pack.bank_name, "country": script.setup.country_alias}
    rules = {
        key: [rule.format(**names) for rule in script.setup.rules[key]] for key in RULE_CATEGORIES
    }
    rules["scenario"].insert(
        0, f"Day 1 of the simulated crisis is a {pack.start_weekday}. Weekends count as days: "
           "markets are closed.")
    rules["play"].insert(0, MODE_TEXT[pack.mode])
    if pack.mode == "dynamic":
        rules["play"].insert(
            1, "Each option of a decision has a branch code. Give the control team the code "
               "of your choice.")
    played = ", ".join(card.name.lower() for card in pack.roles)
    rules["play"].append(
        f"The control team plays these stakeholders: {played}. Tell the control team before "
        "the start if you expect to need another counterparty.")
    return [(RULE_CATEGORIES[key], rules[key]) for key in RULE_CATEGORIES]


# --------------------------------------------------------------------------- observers


@dataclass(frozen=True)
class ObserverSheet:
    """What the observer of one team is asked to follow (EBA Handbook, par. 101)."""

    player: str
    name: str
    decisions: tuple[tuple[str, ...], ...]  # branch, opens, decision, decide by, consult, inform
    relays: tuple[tuple[str, ...], ...]  # branch, by when, what, to whom
    tracks: tuple[str, ...]


def _section(pack: "ExercisePack", section: str) -> str:
    if section:
        return f"Branch {section}"
    return "All branches" if len(pack.branches) > 1 else ""


def _day(pack: "ExercisePack", day: int, weekday: str) -> str:
    return f"Day {day}, {weekday}" if weekday else f"Day {day}"


def observer_sheet(pack: "ExercisePack", script: ScriptConfig, player: str) -> ObserverSheet:
    document = next(d for d in pack.documents if d.actor == player)
    sections = [("", document.shared)] + [
        (title.split()[1], injects) for title, injects in document.branches
    ]
    weekday = {event.day: event.weekday for event in pack.events}
    decisions = []
    for section, injects in sections:
        for inject in injects:
            due = inject.decision_due
            if due is None:
                continue
            decisions.append((
                _section(pack, section),
                f"{_day(pack, inject.day, inject.weekday)}, {inject.time}",
                due.name,
                _day(pack, due.deadline_day, weekday.get(due.deadline_day, "")) + ", close",
                ", ".join(due.consult) or "nobody",
                ", ".join(due.inform) or "nobody",
            ))
    name = script.actors[player]
    relays = tuple(
        (
            _section(pack, event.section),
            f"{_day(pack, event.day, event.weekday)}, {event.time}",
            event.title,
            ", ".join(script.actors[actor] for actor in event.recipients),
        )
        for event in pack.events
        if event.fallback and event.delivered_by.endswith(f"in place of the {name.lower()}")
    )
    return ObserverSheet(
        player=player,
        name=name,
        decisions=tuple(decisions),
        relays=relays,
        tracks=tuple(script.setup.observer_tracks),
    )


def resolve_sessions(
    script: ScriptConfig, sessions: Optional[int] = None, hours: Optional[float] = None
) -> tuple[int, float]:
    """The session inputs: those given, or the defaults of the config."""
    return (
        int(script.setup.sessions.value) if sessions is None else int(sessions),
        float(script.setup.session_hours.value) if hours is None else float(hours),
    )


def session_plans(
    pack: "ExercisePack", decisions: DecisionsConfig, sessions: int, hours: float
) -> list[tuple[str, tuple[Session, ...]]]:
    """One plan per branch; a single one, without a heading, if the branches agree."""
    plans = [
        (branch.label, session_plan(pack, decisions, branch.label, sessions, hours))
        for branch in pack.branches
    ]
    if len(plans) == 1:
        return [("", plans[0][1])]
    shape = lambda plan: [(s.first_day, s.last_day, s.ends_with) for s in plan]  # noqa: E731
    if all(shape(plan) == shape(plans[0][1]) for _, plan in plans):
        # The same days in the same sessions: one plan, with the injects of each branch.
        merged = tuple(
            replace(session, injects_text=", ".join(
                f"{plan[index].injects} (branch {label})" for label, plan in plans))
            for index, session in enumerate(plans[0][1])
        )
        return [("All branches", merged)]
    return [(f"Branch {label}", plan) for label, plan in plans]
