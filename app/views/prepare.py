"""Prepare page: the exercise pack, the challenge injects and the session plan.

It builds the pack from the design set on the Design page (app/design.py); if none has been
set in this session, from the showcase run.
"""

import pandas as pd
import streamlit as st

from app import demo
from app.design import MARKET_KEY, current_design, get_config, initial
from engine.decisions import choices_of
from engine.export import main_events_rows, pack_to_zip
from engine.schema import PLAYERS
from engine.script import (
    MODE_LABELS,
    MODE_TEXT,
    MODES,
    active_challenges,
    build_pack,
    scenario_with_challenges,
)
from engine.setup import session_plans

config = get_config()
showcase = config.script.showcase
design = current_design(config)
bank_key, scenario_key = design.bank_key, design.scenario_key
bank, scenario, choices = design.bank, design.scenario, design.choices

st.title("Prepare")
kind = f" ({scenario.taxonomy.label()})" if scenario.taxonomy else ""
st.caption(
    f"Exercise materials for the design set on the Design page: {bank.name}, {scenario.name}"
    f"{kind}. Change the bank, the scenario or the decision path there."
)
if st.session_state.get("demo_on") and st.session_state.get("demo_step") == len(demo.DEMO_STEPS) - 1:
    st.html(demo.highlight_css(demo.DEMO_STEPS[-1].control))
    st.info(f"Guided demo, last step: {demo.DEMO_STEPS[-1].instruction}")

st.subheader("Market shocks")
st.caption(
    "Challenge injects that change the run: a public news event with a panic shock and a cut "
    "in wholesale roll-over, placed after its stage on the path without decisions. They also "
    "show on the Design page."
)
news_challenges = []
for key, item in config.script.challenges.items():
    if item.effect == "panic_shock":
        if st.checkbox(f"Challenge inject: {item.title.lower()}", key=f"challenge_{key}"):
            news_challenges.append(key)
st.session_state[MARKET_KEY] = tuple(news_challenges)
scenario = scenario_with_challenges(
    bank, config.model, scenario, config.decisions, config.script, news_challenges
)

paths = config.decisions.paths
st.subheader("Exercise pack")
st.caption(
    "For the control team: the inject sheets of the four players, each with only "
    "what that player knows and when it knows it, the main events list, the role "
    "cards of the stakeholders the control team plays, and the control team pack. "
    "Wording is a draft."
)
other_key = st.selectbox(
    "Second branch of the script",
    list(paths),
    format_func=lambda k: paths[k].name,
    key="script_branch",
    **initial("script_branch", index=list(paths).index(showcase.compare_path)),
    help="The script covers this path and the one selected on the Design page.",
)
mode = st.radio(
    "Type of exercise",
    MODES,
    horizontal=True,
    key="script_mode",
    format_func=lambda m: MODE_LABELS[m],
    help="Dynamic: the players' decisions change the course, so the script has two "
    "branches. Linear: one fixed path; decisions are recorded but change nothing.",
)
ambient_on = st.checkbox(
    "Include ambient injects (press, politics, other authorities; no effect on figures)",
    key="script_ambient",
    **initial("script_ambient", value=True),
)
hostile = st.radio(
    "Assumptions",
    ["Friendly", "Hostile"],
    horizontal=True,
    key="script_assumptions",
    help="Friendly: no challenge from creditors, no buyer withdrawing, no negative "
    "market reaction. Hostile: these are added, and the liquidity need at reopening "
    "is also shown raised by an assumed share. Neither changes the run.",
) == "Hostile"
quiet = {
    key: item for key, item in config.script.challenges.items()
    if item.effect != "panic_shock"
}
defaults = active_challenges(config.script, None, hostile)
picked = st.multiselect(
    "Challenge injects",
    list(quiet),
    format_func=lambda k: quiet[k].title,
    key=f"script_challenges_{'hostile' if hostile else 'friendly'}",
    **initial(f"script_challenges_{'hostile' if hostile else 'friendly'}",
              default=[key for key in quiet if key in defaults]),
    help="Injects that challenge the players. They change no figure of the run. The "
    "market shocks, which do, are switched on above.",
)
setup = config.script.setup
left, right = st.columns(2)
sessions = left.number_input(
    "Session plan: number of sessions", min_value=1, max_value=20, step=1,
    key="script_sessions", **initial("script_sessions", value=int(setup.sessions.value)),
    help="The simulated days are mapped to this many real sessions, with breaks at "
    "the FoLtF determination and at the resolution decision where possible.",
)
session_hours = right.number_input(
    "Hours per session", min_value=0.5, max_value=12.0, step=0.5,
    key="script_session_hours",
    **initial("script_session_hours", value=float(setup.session_hours.value)),
)
other_choices = choices_of(paths[other_key])
branches = [("Path selected on the Design page", choices)]
if tuple(other_choices) != tuple(choices):
    branches.insert(0, (paths[other_key].name, other_choices))
else:
    st.info("Both branches are the same path: the script has a single branch.")
pack = build_pack(
    bank, config.model, scenario, config.decisions, config.script, branches,
    ambient=ambient_on,
    writedown_level=st.session_state.get("writedown_level", "central"),
    mode=mode,
    challenges=[*picked, *news_challenges],
    hostile=hostile,
    bank_alias=setup.bank_alias,
    background_key=scenario_key,
)
st.caption(MODE_TEXT[mode])
with st.container(key="ctl_export"):
    st.download_button(
        "Download the exercise pack (.zip)",
        data=pack_to_zip(
            pack, config.script, config.decisions, int(sessions), float(session_hours)),
        file_name=f"exercise_pack_{bank_key}_{scenario_key}.zip",
        mime="application/zip",
    )
st.caption(
    "The zip holds three folders. players: mock bank dossier, rules of play and "
    "feedback questionnaire, to share in advance. observers: one sheet per team. "
    "control_team: SimEx Charter draft, session plan, main events list, inject "
    "sheets, role cards, control team pack, feedback checklist, the table mapping "
    f"each output to the Handbook, and the data (.csv). In the documents the bank is "
    f"called {setup.bank_alias}."
)
with st.expander("Session plan"):
    for heading, plan in session_plans(
            pack, config.decisions, int(sessions), float(session_hours)):
        if heading:
            st.markdown(f"**{heading}**")
        st.dataframe(
            pd.DataFrame([
                {"Session": s.number,
                 "Simulated days": f"{s.first_day} to {s.last_day}",
                 "Injects": s.injects_text or str(s.injects),
                 "Real minutes per simulated day": round(s.minutes_per_day),
                 "Ends with": s.ends_with}
                for s in plan
            ]),
            hide_index=True, width="stretch",
        )
with st.expander(f"Main events list ({len(pack.events)} injects)"):
    st.caption(
        "Every inject to a player: its trigger, who delivers it, its purpose "
        "(advance, challenge, control) and the response the control team looks for."
    )
    events = pd.DataFrame(main_events_rows(pack, config.script))
    if len(pack.branches) == 1:
        events = events.drop(columns="Branch")
    st.dataframe(events, hide_index=True, width="stretch")
with st.expander("Role cards: the stakeholders the control team plays"):
    for card in pack.roles:
        st.markdown(
            f"**{card.name}** delivers {len(card.delivers)} inject"
            f"{'' if len(card.delivers) == 1 else 's'}. Wants: {card.wants[0]}"
        )
actor = st.selectbox(
    "Preview the inject sheets of",
    list(PLAYERS),
    format_func=lambda k: config.script.actors[k],
    key="script_actor",
)
document = next(d for d in pack.documents if d.actor == actor)
for sentence in document.information:
    st.markdown(f"- {sentence}")
sections = [("Shared part" if document.branches else "Injects", document.shared)]
sections += list(document.branches)
for title, injects in sections:
    st.markdown(f"**{title}**")
    if not injects:
        st.caption("Nothing further on this branch.")
    for inject in injects:
        when = f"Day {inject.day}" + (f", {inject.weekday}" if inject.weekday else "")
        with st.expander(f"{when}, {inject.time} - {inject.title}"):
            st.caption(
                f"Control team: {inject.purpose}"
                + (", fallback" if inject.fallback else "")
                + f". Delivered by {inject.delivered_by}. {inject.trigger}."
            )
            st.markdown(f"*From: {inject.sender}*")
            for line in inject.text.split("\n"):
                st.write(line)
            if inject.headline:
                st.caption(
                    "Figures behind this notification, as of the close of day "
                    f"{inject.headline[0].as_of_day}, as passed on by the sender"
                )
                for figure in inject.headline:
                    limit = f" (threshold {figure.threshold_text})" if figure.threshold_text else ""
                    st.markdown(f"- {figure.text}{limit}")
            if inject.notified:
                st.caption("Told in notifications so far")
                for figure in inject.notified:
                    st.markdown(f"- {figure.text}, as of day {figure.as_of_day}")
            if inject.resolution:
                st.caption("Estimates, resting on an assumed write-down")
                for figure in inject.resolution:
                    st.markdown(f"- {figure.text}")
            if inject.figures:
                st.caption(
                    "Latest regular report, as of the close of day "
                    f"{inject.figures[0].as_of_day}"
                )
                for figure in inject.figures:
                    st.markdown(f"- {figure.text}")
            due = inject.decision_due
            if due is not None:
                st.markdown(f"**Question:** {due.question}")
                for option in due.options:
                    code = f"`{option.branch_code}` " if option.branch_code else ""
                    st.markdown(f"- {code}{option.name}")
                st.markdown(
                    f"**Decide by** the close of day {due.deadline_day}"
                    + (" (the earlier of the two branches). " if due.deadline_shared else ". ")
                    + 
                    f"**Consult first:** {', '.join(due.consult) or 'nobody'}. "
                    f"**Inform afterwards:** {', '.join(due.inform) or 'nobody'}."
                )
