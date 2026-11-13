"""A player's page (SPEC_V3 phase H): read-only, only that player's injects so far.

Opened with a link from the control console: ?exercise=<id>&player=<player>&code=<code>.
The page reads the saved exercise file and shows the view stored there for this player.
"""

import streamlit as st

from app import store

REFRESH_SECONDS = 3
query = st.query_params
exercise_id, player, code = query.get("exercise", ""), query.get("player", ""), query.get("code", "")


def show_inject(inject: dict) -> None:
    weekday = f", {inject['weekday']}" if inject["weekday"] else ""
    with st.container(border=True):
        st.markdown(f"**Day {inject['day']}{weekday}, {inject['time']} - {inject['title']}**")
        st.caption(f"From: {inject['sender']}")
        for line in inject["text"]:
            st.write(line)
        if inject["headline"]:
            st.caption(f"Figures behind this notification, as of the close of day "
                       f"{inject['headline_day']}, as passed on by the sender")
            for label, value, threshold in inject["headline"]:
                st.markdown(f"- {label}: {value}" + (f" (threshold {threshold})" if threshold else ""))
        if inject["notified"]:
            st.caption("What you have been told in notifications so far")
            for label, value, day in inject["notified"]:
                st.markdown(f"- {label}: {value}, as of {day}")
        if inject["estimates"]:
            st.caption("Estimates. They rest on an assumed write-down and may be revised")
            for label, value in inject["estimates"]:
                st.markdown(f"- {label}: {value}")
        if inject["report"]:
            st.caption(f"Your latest regular report, as of the close of day {inject['report_day']}")
            for label, value in inject["report"]:
                st.markdown(f"- {label}: {value}")
        decision = inject["decision"]
        if decision:
            st.markdown(f"**Question:** {decision['question']}")
            for name, branch_code in decision["options"]:
                st.markdown(f"- `{branch_code}` {name}" if branch_code else f"- {name}")
            st.markdown(
                f"**Decide by** the close of day {decision['deadline_day']}. "
                f"**Consult first:** {', '.join(decision['consult']) or 'nobody'}. "
                f"**Inform afterwards:** {', '.join(decision['inform']) or 'nobody'}. "
                "Give your decision to the control team."
            )


@st.fragment(run_every=REFRESH_SECONDS)
def page() -> None:
    view = store.player_view(exercise_id, player, code)
    if view is None:
        st.error(
            "This page cannot be opened. Check the link you were given by the control team: "
            "it holds the exercise, your team and your code."
        )
        return
    st.title(view["player"])
    st.caption(
        f"SIMULATION - exercise material, not a real event. {view['exercise']}: {view['bank']}. "
        "This page shows what your team has received so far. It updates by itself."
    )
    weekday = f", {view['weekday']}" if view["weekday"] else ""
    left, right = st.columns(2)
    left.metric("Simulated time", f"Day {view['day']}{weekday}, {view['time']}")
    right.metric("Exercise", view["status"].capitalize())
    if view["status"] == "paused":
        st.warning("The exercise is paused. Wait for the control team.")
    if view["status"] == "ended":
        st.success("The exercise has ended. The debrief follows.")
    if not view["injects"]:
        st.info("Nothing has reached your team yet.")
    for inject in reversed(view["injects"]):  # the latest first
        show_inject(inject)


page()
