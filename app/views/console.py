"""Control console (SPEC_V3 phase H): the control team runs a live exercise from here.

The page only calls engine.live. After every entry the exercise is saved to a file
(app/store.py), which the players' pages read and from which it can be reloaded and replayed.
"""

from datetime import datetime

import json

import pandas as pd
import streamlit as st

from app import store
from engine import live
from engine.config import load_config
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.export import TIMELINE_COLUMNS, report_to_zip
from engine.report import live_report
from engine.schema import ACTORS, PLAYERS
from engine.script import MODE_LABELS, MODE_TEXT, MODES


@st.cache_resource
def get_config():
    return load_config()


config = get_config()
script, decisions = config.script, config.decisions
# A reloaded exercise runs on the configuration saved with it, not on today's files.
context = st.session_state.get("live_context") or live.Context(config.model, decisions, script)
script, decisions = context.script, context.decisions


def save() -> None:
    state = st.session_state
    store.save(state["live_id"], state["live"], context, state["live_codes"])


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def act(kind: str, **data) -> None:
    """Add one entry to the log; an entry the engine refuses is shown, not applied."""
    try:
        st.session_state["live"] = live.apply(st.session_state["live"], kind, now(), context, **data)
        st.session_state["live_error"] = ""
        save()
    except live.LiveError as error:
        st.session_state["live_error"] = str(error)


def start_exercise() -> None:
    state = st.session_state
    setup = live.make_setup(
        state["live_name"].strip() or "Exercise",
        config.banks[state["live_bank"]],
        config.model,
        config.scenarios[state["live_scenario"]],
        decisions,
        script,
        choices_of(decisions.paths[state["live_path"]]),
        mode=state["live_mode"],
        ambient=state["live_ambient"],
        hostile=state["live_assumptions"] == "Hostile",
    )
    state["live"] = live.start(setup, now(), context)
    state["live_error"] = ""
    state["live_id"], state["live_codes"] = store.new_id(), store.new_codes()
    save()


def reload_exercise() -> None:
    state = st.session_state
    loaded = store.load(state.get("live_reload_id") or "", state.get("live_reload_code", "").strip())
    if loaded is None:
        state["live_error"] = "The exercise could not be reloaded: check the control team's code."
        return
    state["live"], state["live_context"], state["live_codes"] = loaded
    state["live_id"], state["live_error"] = state["live_reload_id"], ""


def close_exercise() -> None:
    for key in ("live", "live_context", "live_id", "live_codes"):
        st.session_state.pop(key, None)


def record_decision(decision: str) -> None:
    act("decision", decision=decision, option=st.session_state[f"live_option_{decision}"],
        by_control=st.session_state.get(f"live_by_control_{decision}", False),
        consulted=list(st.session_state.get(f"live_consulted_{decision}", [])))


def send_adhoc() -> None:
    state = st.session_state
    if state["live_adhoc_source"] == "Free text":
        act("adhoc", recipients=state["live_adhoc_to"], title=state["live_adhoc_title"],
            text=state["live_adhoc_text"], sender=state["live_adhoc_sender"])
    else:
        act("adhoc", recipients=state["live_adhoc_to"], library=state["live_adhoc_item"])


def when(day: int, time: str, weekday: str = "") -> str:
    return f"Day {day}" + (f", {weekday}" if weekday else "") + f", {time}"


def show_inject(inject, caption: str = "") -> None:
    with st.expander(f"{when(inject.day, inject.time, inject.weekday)} - {inject.title}"):
        if caption:
            st.caption(caption)
        st.markdown(f"*From: {inject.sender}*")
        for line in inject.text.split("\n"):
            st.write(line)
        for heading, figures in (
            ("Figures behind this notification", inject.headline),
            ("Estimates", inject.resolution),
            ("Latest regular report", inject.figures),
        ):
            if figures:
                st.caption(heading)
                for figure in figures:
                    st.markdown(f"- {figure.text}")


st.title("Control console")
st.caption(
    "SIMULATION - exercise material, not a real event. For the control team: run a live "
    "exercise, record the players' decisions and release the injects."
)

if "live" not in st.session_state:
    st.subheader("Start an exercise")
    showcase = script.showcase
    left, right = st.columns(2)
    left.text_input("Name of the exercise", value="Exercise", key="live_name")
    left.selectbox("Bank archetype", list(config.banks), key="live_bank",
                   index=list(config.banks).index(showcase.bank),
                   format_func=lambda k: config.banks[k].name)
    crisis = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    left.selectbox("Trigger scenario", crisis, key="live_scenario",
                   index=crisis.index(showcase.scenario),
                   format_func=lambda k: config.scenarios[k].name)
    right.selectbox(
        "Pre-planned path", list(decisions.paths), key="live_path",
        index=list(decisions.paths).index(showcase.path),
        format_func=lambda k: decisions.paths[k].name,
        help="What the control team expects the players to decide. The exercise follows it "
        "until the players decide otherwise.",
    )
    right.radio("Type of exercise", MODES, key="live_mode", horizontal=True,
                format_func=lambda m: MODE_LABELS[m])
    right.radio("Assumptions", ["Friendly", "Hostile"], key="live_assumptions", horizontal=True)
    right.checkbox("Include ambient injects", value=True, key="live_ambient")
    st.button("Start the exercise", type="primary", on_click=start_exercise, key="live_start")
    st.caption(
        f"In the exercise the bank is called {script.setup.bank_alias}. Simulated time moves "
        "only when you step the clock."
    )
    st.subheader("Reload a saved exercise")
    saved = dict(store.saved_exercises())
    if not saved:
        st.caption("No exercise has been saved yet.")
    else:
        left, right = st.columns(2)
        left.selectbox("Saved exercise", list(saved), key="live_reload_id",
                       format_func=lambda k: saved[k])
        right.text_input("Control team's code", key="live_reload_code", type="password",
                         help="Shown on the console of the exercise, under \"Players' pages and saving\".")
        st.button("Reload", on_click=reload_exercise, key="live_reload")
        st.caption("A reloaded exercise continues where it stopped, on the configuration it was started with.")
    if st.session_state.get("live_error"):
        st.error(st.session_state["live_error"])
    st.stop()

state = st.session_state["live"]
setup, branch = state.setup, state.branch
result = branch.result
weekday = setup.scenario.weekday(state.day) if state.day >= 1 else ""
status = "Ended" if state.ended else "Paused" if state.paused else "Running"
row = result.table.set_index("day").loc[min(state.day, result.end_day)]
started = datetime.fromisoformat(state.entries[0].real_time)

st.subheader(f"{setup.name}: {setup.bank.name}, {setup.scenario.name}")
st.caption(MODE_TEXT[setup.mode])
metrics = st.columns([2.2, 1, 1.3, 1.5, 1.3])
metrics[0].metric("Simulated time", when(state.day, state.time, weekday))
metrics[1].metric("Status", status)
metrics[2].metric("Stage of the bank", str(row["status"]).replace("_", " ").replace("failed", "payments default"))
metrics[3].metric("Injects released to players",
                  sum(1 for r in state.released if r.inject.recipient in PLAYERS))
metrics[4].metric("Started (real time)", started.strftime("%H:%M:%S"))

buttons = st.columns(5)
buttons[0].button("Next step", type="primary", on_click=act, args=("step",), key="live_step",
                  disabled=state.ended or state.paused,
                  help="Move the simulated clock to the next inject time or to the close of the day.")
buttons[1].button("Jump to the next milestone", on_click=act, args=("jump",), key="live_jump",
                  disabled=state.ended or state.paused,
                  help="Next stage change or decision opening. Injects on the way are released together.")
if state.paused:
    buttons[2].button("Resume", on_click=act, args=("resume",), key="live_resume")
else:
    buttons[2].button("Pause", on_click=act, args=("pause",), key="live_pause")
buttons[3].button("Undo the last entry", on_click=act, args=("undo",), key="live_undo",
                  disabled=len(state.entries) < 2 or state.paused,
                  help="Takes back the last entry. Both stay in the log.")
buttons[4].button("Close the exercise", on_click=close_exercise, key="live_close",
                  help="Leaves this exercise. It stays saved and can be reloaded with the control team's code.")
if st.session_state.get("live_error"):
    st.error(st.session_state["live_error"])
if state.ended:
    st.success(f"The exercise has ended. Outcome: {OUTCOME_LABELS[result.outcome]}.")

with st.expander("Players' pages and saving"):
    codes, exercise_id = st.session_state["live_codes"], st.session_state["live_id"]
    st.caption(
        "Give each team its own link. A page shows only that team's injects so far and "
        "updates by itself every few seconds. On another computer, replace the start of "
        "the link by this computer's network address and port. The codes keep a team from "
        "opening another team's page by mistake; they are not security."
    )
    for player in PLAYERS:
        link = f"/?exercise={exercise_id}&player={player}&code={codes[player]}"
        st.markdown(f"- {script.actors[player]}: [{link}]({link})")
    st.markdown(
        f"Saved after every entry as `{store.path_of(exercise_id).name}` in the folder "
        f"`exercises/`. Control team's code, to reload it: `{codes[store.CONTROL]}`"
    )
    st.download_button(
        "Download the draft closing report (.zip)",
        data=report_to_zip(live_report(st.session_state["live"], context), script),
        file_name=f"SIMULATION_closing_report_{exercise_id}.zip", mime="application/zip",
        key="live_download_report",
        help="Decisions with their timing and the figures each player held, coordination "
        "steps recorded, and what the model gives with one decision changed. Lessons and "
        "recommendations are left blank for you. Can be drawn up at any time.",
    )
    left, right = st.columns(2)
    left.download_button(
        "Download the log (.csv)",
        data=pd.DataFrame(live.log_rows(st.session_state["live"], context)).to_csv(index=False),
        file_name=f"SIMULATION_log_{exercise_id}.csv", mime="text/csv", key="live_download_log",
    )
    right.download_button(
        "Download the exercise (.json, to reload or replay)",
        data=json.dumps(live.to_saved(st.session_state["live"], context)),
        file_name=f"SIMULATION_exercise_{exercise_id}.json", mime="application/json",
        key="live_download_exercise",
    )

st.markdown("### Decisions open to the players")
opened = live.open_decisions(state)
if not opened:
    st.caption("No decision is open.")
for inject in opened:
    due = inject.decision_due
    definition = decisions.decisions[due.decision]
    columns = st.columns([3, 2, 2, 2, 1])
    played = "" if definition.actor in PLAYERS else " (played by the control team)"
    columns[0].markdown(
        f"**{script.actors[definition.actor]}{played}**: {due.name}  \n"
        f"Open since day {inject.day}; decide by the close of day {due.deadline_day}."
    )
    columns[1].selectbox(
        "Option", list(definition.options), key=f"live_option_{due.decision}",
        format_func=lambda k, d=definition: d.options[k].name, label_visibility="collapsed",
    )
    columns[2].multiselect(
        "Consulted first", [a for a in ACTORS if a != definition.actor],
        key=f"live_consulted_{due.decision}", format_func=lambda k: script.actors[k],
        placeholder="Consulted first", label_visibility="collapsed",
        help="Whom the deciding team consulted before deciding, as you observed it. The "
        f"script expects: {', '.join(due.consult) or 'nobody'}.",
    )
    columns[3].checkbox("Taken by the control team", key=f"live_by_control_{due.decision}",
                        help="Tick when the players could not decide and the control team did.")
    columns[4].button("Record", on_click=record_decision, args=(due.decision,),
                      key=f"live_record_{due.decision}", disabled=state.paused or state.ended)
st.caption(
    "A decision recorded now is taken at the close of this simulated day and is in effect "
    "the next day. The option that means \"no decision yet\" is logged and leaves the decision open."
)

pending = live.pending_fallbacks(state)
if setup.mode == "dynamic":
    st.markdown("### To be passed on by the players")
    if not pending:
        st.caption("Nothing is waiting.")
    for inject in pending:
        key = live.inject_key(inject)
        columns = st.columns([5, 2, 2])
        columns[0].markdown(
            f"**{inject.sender} -> {script.actors[inject.recipient]}**: {inject.title} "
            f"({when(inject.day, inject.time)})"
        )
        columns[1].button("Passed on by the player", on_click=act, args=("relay_done",),
                          kwargs={"key": key}, key=f"live_done_{key}", disabled=state.paused)
        columns[2].button("Release the fallback inject", on_click=act, args=("fallback",),
                          kwargs={"key": key}, key=f"live_fallback_{key}", disabled=state.paused)

with st.expander("Send an ad hoc inject"):
    st.radio("Source", ["Free text", "From the library"], key="live_adhoc_source", horizontal=True)
    st.multiselect("To", list(PLAYERS), key="live_adhoc_to",
                   format_func=lambda k: script.actors[k])
    if st.session_state["live_adhoc_source"] == "Free text":
        st.text_input("Title", key="live_adhoc_title")
        st.text_input("From", key="live_adhoc_sender", placeholder="Exercise control")
        st.text_area("Text", key="live_adhoc_text")
        st.caption("Figures you type here are not checked against the players' information sets.")
    else:
        library = {f"ambient:{key}": item.title for key, item in script.ambient.items()}
        library |= {f"challenge:{key}": item.title for key, item in script.challenges.items()
                    if item.effect != "panic_shock"}
        st.selectbox("Inject", list(library), key="live_adhoc_item",
                     format_func=lambda k: library[k])
        st.caption("An inject that carries resolution figures can be sent once FoLtF is in effect.")
    st.button("Send", on_click=send_adhoc, key="live_adhoc_send",
              disabled=state.paused or state.ended)

st.markdown("### State of the bank (full information)")
table = result.table[result.table["day"] <= state.day]
shown = table[list(TIMELINE_COLUMNS)].rename(columns=TIMELINE_COLUMNS).copy()
shown["Stage"] = shown["Stage"].str.replace("_", " ").str.replace("failed", "payments default")
shown["CET1 ratio (%)"] = 100 * shown["CET1 ratio (%)"]
st.dataframe(shown.round(2), hide_index=True, width="stretch")
st.caption(
    f"Amounts in {setup.bank.unit}. Expected from here, if the players decide as planned: "
    f"the path ends on day {result.end_day}, {OUTCOME_LABELS[result.outcome].lower()}."
)

st.markdown("### Coming next")
coming = sorted(
    (i for actor in PLAYERS for i in branch.injects[actor] if (i.day, i.time) > state.clock),
    key=lambda i: (i.day, i.time),
)[:8]
if coming:
    st.dataframe(
        pd.DataFrame([
            {"When": when(i.day, i.time, i.weekday), "Inject": i.title,
             "To": script.actors[i.recipient], "Delivered by": i.delivered_by,
             "Purpose": i.purpose + (" (fallback)" if i.fallback else "")}
            for i in coming
        ]),
        hide_index=True, width="stretch",
    )
else:
    st.caption("No inject is left on this path.")

st.markdown("### Released so far")
recipient = st.selectbox("Recipient", list(ACTORS), key="live_recipient",
                         format_func=lambda k: script.actors[k])
for item in live.released_to(state, recipient):
    held = "" if (item.inject.day, item.inject.time) == (item.day, item.time) else (
        f" Released at day {item.day}, {item.time}.")
    show_inject(item.inject, f"Released {item.real_time} ({item.how}).{held} {item.note}".strip())

with st.expander(f"Decisions recorded ({len(state.decisions)})"):
    if state.decisions:
        st.dataframe(
            pd.DataFrame([
                {"Simulated day": d.day,
                 "By": script.actors[d.actor],
                 "Decision": decisions.decisions[d.decision].name,
                 "Option": decisions.decisions[d.decision].options[d.option].name,
                 "Taken by the control team": "yes" if d.by_control else "",
                 "Different from the plan": "yes" if d.deviates else "",
                 "Real minutes taken": round(d.seconds_taken / 60, 1),
                 "Real time": d.real_time}
                for d in state.decisions
            ]),
            hide_index=True, width="stretch",
        )
with st.expander(f"Log ({len(state.entries)} entries)"):
    st.dataframe(pd.DataFrame(live.log_rows(state, context)), hide_index=True, width="stretch")

with st.expander("Replay"):
    st.caption(
        "Go back through the exercise entry by entry. This only looks at the log: it "
        "changes nothing."
    )
    if len(state.entries) < 2:
        st.caption("There is nothing to replay yet.")
    else:
        upto = st.slider("Entries replayed", 1, len(state.entries), len(state.entries),
                         key="live_replay_upto")
        past = live.replay(
            setup, [live.Entry(e.kind, e.real_time, e.data) for e in state.entries[:upto]], context)
        entry = past.entries[-1]
        st.markdown(
            f"After entry {upto} ({entry.kind.replace('_', ' ')}, {entry.real_time}): simulated "
            f"time day {past.day}, {past.time}; "
            f"{'paused' if past.paused else 'ended' if past.ended else 'running'}; expected "
            f"outcome then: {OUTCOME_LABELS[past.branch.result.outcome].lower()}."
        )
        st.dataframe(
            pd.DataFrame([
                {"Recipient": script.actors[actor],
                 "Injects received": len(live.released_to(past, actor)),
                 "Last received": (live.released_to(past, actor)[-1].inject.title
                                   if live.released_to(past, actor) else "")}
                for actor in ACTORS
            ]),
            hide_index=True, width="stretch",
        )
