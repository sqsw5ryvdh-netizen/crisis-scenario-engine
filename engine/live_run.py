"""Scripted live run of the showcase (SPEC_V3 phase H, acceptance).

    python -m engine.live_run

Five decisions are entered live, two of them different from the pre-planned branch, with one
ad hoc inject and one pause. Real times are fixed, so the run is the same every time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from engine import live
from engine.config import load_config
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.schema import PLAYERS, Config

START = datetime(2028, 3, 1, 9, 0, 0)
STEP_SECONDS = 90  # real time between two entries of the script

# What the players decide, by simulated day, at the close of that day.
DECISIONS = {
    2: [("ela", "not_decided", False)],  # the central bank waits: as planned
    6: [("early_intervention_measures", "enhanced_reporting", False),  # not in the plan
        ("prepare_payout", "none", True)],  # players stuck: the control team takes it, as planned
    10: [("declare_foltf", "declare", False)],  # as planned
    12: [("resolution_tool", "liquidation", False)],  # the plan was bail-in
}
AD_HOC_DAY = 7
PAUSE_AFTER_DAY = 10  # the break at the FoLtF determination
PAUSE_MINUTES = 45


def scripted_showcase(config: Config) -> tuple[live.LiveState, live.Context]:
    """Run the script and return the final state."""
    context = live.Context(config.model, config.decisions, config.script)
    showcase = config.script.showcase
    setup = live.make_setup(
        "Showcase, scripted live run", config.banks[showcase.bank], config.model,
        config.scenarios[showcase.scenario], config.decisions, config.script,
        choices_of(config.decisions.paths[showcase.path]),
    )
    clock = {"now": START}

    def now(extra_minutes: float = 0) -> str:
        clock["now"] += timedelta(seconds=STEP_SECONDS + 60 * extra_minutes)
        return clock["now"].isoformat(timespec="seconds")

    state = live.start(setup, now(), context)
    sent = False
    while not state.ended:
        # The control team handles what players should have passed on: the first one is
        # passed on by the player, the others need the fallback inject.
        for position, inject in enumerate(live.pending_fallbacks(state)):
            kind = "relay_done" if position == 0 and not state.passed_on else "fallback"
            state = live.apply(state, kind, now(), context, key=live.inject_key(inject))
        if state.day == AD_HOC_DAY and not sent:
            state = live.apply(
                state, "adhoc", now(), context,
                recipients=["competent_authority", "resolution_authority"],
                title="Call from a large corporate depositor",
                sender="The treasurer of a large company",
                text="The treasurer asks both authorities whether the company should move its "
                     "deposits today. He says he has heard that the bank is about to be closed.",
            )
            sent = True
        if state.time == live.CLOSE:
            for decision, option, by_control in DECISIONS.get(state.day, ()):
                state = live.apply(state, "decision", now(), context, decision=decision,
                                   option=option, by_control=by_control)
            if state.day == PAUSE_AFTER_DAY:
                state = live.apply(state, "pause", now(), context)
                state = live.apply(state, "resume", now(PAUSE_MINUTES), context)
        state = live.apply(state, "step", now(), context)
    return state, context


def main() -> int:
    config = load_config()
    state, context = scripted_showcase(config)
    decisions, script = context.decisions.decisions, context.script
    print("SIMULATION - exercise material, not a real event")
    print(f"{state.setup.name}: {state.setup.bank.name}, {state.setup.scenario.name}")
    print()
    print("Decisions entered live")
    for d in state.decisions:
        definition = decisions[d.decision]
        print(
            f"  day {d.day:>2}  {script.actors[d.actor]:<26} {definition.name}: "
            f"{definition.options[d.option].name}"
            f" | planned: {definition.options[d.planned_option].name}"
            f" | {'DIFFERENT from the plan' if d.deviates else 'as planned'}"
            f"{' | taken by the control team' if d.by_control else ''}"
            f" | {d.seconds_taken / 60:.1f} real minutes"
        )
    counts = {}
    for entry in state.entries:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    print()
    print("Log:", ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in counts.items()))
    pause = state.pauses[0]
    print(f"Pause: {pause[0]} to {pause[1]} (real time)")
    print(f"Outcome: {OUTCOME_LABELS[state.branch.result.outcome]} on day {state.branch.result.end_day}")
    print()
    print("Injects received")
    for player in PLAYERS:
        items = live.released_to(state, player)
        print(f"  {script.actors[player]:<26} {len(items):>3}, of which ad hoc "
              f"{sum(1 for r in items if r.how == 'adhoc')}, fallback "
              f"{sum(1 for r in items if r.how == 'fallback')}")
    print()
    print("Log (real time | simulated time | entry)")
    for row in live.log_rows(state, context):
        if row["Entry"] != "step":
            print(f"  {row['Real time']} | {row['Simulated time']:<14} | {row['Entry']:<10} {row['What'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
