"""Simulation page: selectors, sliders, results in tabs and the guided demo."""

import time

import streamlit as st
from streamlit.errors import StreamlitAPIException

from app import demo
from app.charts import (
    TRIGGERS,
    build_contagion_chart,
    build_failure_distribution,
    build_timeline,
)
from engine.config import load_config
from engine.contagion import similarity_factor, simulate_contagion
from engine.export import daily_table_to_csv, injects_to_csv, script_to_docx
from engine.injects import build_script
from engine.model import simulate, uninsured_share, with_uninsured_share
from engine.montecarlo import (
    bands,
    describe_failure_day,
    failure_day_counts,
    recentre,
    run_monte_carlo,
    summarise,
)

# Range of the uninsured-share slider (a display limit, not a model parameter).
UNINSURED_SHARE_RANGE = (10, 95)

# Model parameters with a slider: name -> (label, display factor, step, format, help).
PARAMETER_SLIDERS = {
    "panic_max_multiplier": (
        "Maximum panic multiplier", 1.0, 0.5, "%.1f",
        "How many times faster than the base rate deposits leave at peak panic.",
    ),
    "central_bank_mobilisation_lag_days": (
        "Central bank mobilisation lag (days)", 1.0, 1.0, "%d",
        "Days between the recovery trigger and central bank funding becoming usable.",
    ),
    "central_bank_haircut": (
        "Central bank haircut (%)", 100.0, 1.0, "%d",
        "Haircut applied to collateral pledged with the central bank.",
    ),
    "fire_sale_discount": (
        "Fire-sale discount (%)", 100.0, 1.0, "%d",
        "Loss on illiquid assets sold in a hurry; it reduces CET1.",
    ),
}

WATERFALL_COLUMNS = {
    "day": "Day",
    "outflow_total": "Outflow",
    "funded_cash": "Cash",
    "funded_hqla": "HQLA sale",
    "funded_central_bank": "Central bank",
    "funded_fire_sale": "Fire sale",
    "unmet_outflow": "Unmet",
    "queued_withdrawals": "Queued",
    "status": "Status",
}


@st.cache_resource
def get_config():
    return load_config()


# ------------------------------------------------- guided demo


def _go_to_step(step: int) -> None:
    st.session_state["demo_step"] = step
    tab = demo.DEMO_STEPS[step].tab
    if tab is not None:
        st.session_state["view"] = tab


def _run_pressed() -> None:
    _go_to_step(demo.FIRST_STEP_WITH_RESULTS)
    st.session_state["play_next"] = 0  # the crisis starts playing from day 0


def _exit_demo() -> None:
    st.session_state["demo_on"] = False


def demo_panel(step_index: int) -> None:
    step = demo.DEMO_STEPS[step_index]
    last = len(demo.DEMO_STEPS) - 1
    st.html(demo.highlight_css(step.control))
    with st.container(border=True):
        st.markdown(
            f"**Guided demo · step {step_index + 1} of {len(demo.DEMO_STEPS)}: {step.title}**"
        )
        st.write(step.instruction)
        back, forward, leave, _ = st.columns([1, 2, 2, 5])
        back.button(
            "Back", key="demo_back", disabled=step_index == 0,
            on_click=_go_to_step, args=(max(step_index - 1, 0),),
        )
        if step_index == demo.FIRST_STEP_WITH_RESULTS - 1:
            with forward.container(key="ctl_run"):
                st.button(
                    "Run the simulation", key="demo_run", type="primary", on_click=_run_pressed
                )
        else:
            forward.button(
                "Next", key="demo_next", type="primary", disabled=step_index == last,
                on_click=_go_to_step, args=(min(step_index + 1, last),),
            )
        leave.button("Exit demo", key="demo_exit", on_click=_exit_demo)


# ------------------------------------------------- timeline, day by day


def _start_playback() -> None:
    st.session_state["play_next"] = 0


def _stop_playback() -> None:
    st.session_state["play_next"] = None


@st.fragment
def timeline_view(result, horizon, unit, band_data, n_runs) -> None:
    """Timeline revealed up to a chosen day. A fragment, so playback redraws only this part."""
    table = result.table
    last_day = int(table["day"].max())
    if st.session_state.get("play_next") is not None:
        st.session_state["day"] = min(st.session_state["play_next"], last_day)
    st.session_state["day"] = min(st.session_state.get("day", last_day), last_day)

    with st.container(key="ctl_play"):
        play_column, slider_column = st.columns([1, 7], vertical_alignment="bottom")
        play_column.button("Play", key="play", on_click=_start_playback)
        day = slider_column.slider(
            "Day shown",
            min_value=0,
            max_value=last_day,
            key="day",
            on_change=_stop_playback,
            help="The chart shows the crisis up to this day.",
        )

    chart_column, day_column = st.columns([3, 1])
    chart_column.plotly_chart(
        build_timeline(result, horizon, unit, band_data, until_day=day), width="stretch"
    )
    chart_column.caption(
        f"Line: central run. Shaded band, shown on the last day: 5th to 95th percentile of "
        f"{n_runs} runs; a run that fails keeps its last values."
    )
    row = table[table["day"] == day].iloc[0]
    with day_column:
        st.markdown(f"**Day {day}: {row['status'].replace('_', ' ')}**")
        st.metric(f"Liquidity buffer ({unit})", f"{row['liquidity_buffer']:.2f}")
        st.metric("Deposits lost", f"{row['cum_deposit_loss_pct']:.1f}%")
        st.metric("CET1 ratio", f"{100 * row['cet1_ratio']:.1f}%")
        st.markdown("**Events so far**")
        events = [event for event in result.events if event.day <= day]
        if not events:
            st.caption("Nothing yet.")
        for event in events:
            label = TRIGGERS[event.kind][0] if event.kind in TRIGGERS else "News"
            st.markdown(f"- Day {event.day}, {label}: {event.detail}")

    if st.session_state.get("play_next") is not None:
        if day < last_day:
            st.session_state["play_next"] = day + 1
            time.sleep(demo.PLAYBACK_SECONDS)
            try:
                st.rerun(scope="fragment")
            except StreamlitAPIException:
                # Playback started by a full page run (the demo's Run button).
                st.rerun()
        st.session_state["play_next"] = None


# ------------------------------------------------- page

config = get_config()
st.session_state.setdefault("demo_on", True)
st.session_state.setdefault("demo_step", 0)

with st.sidebar:
    st.toggle("Guided demo", key="demo_on", help="A six-step walk through the tool.")
    st.header("Scenario")
    with st.container(key="ctl_bank"):
        bank_key = st.selectbox(
            "Bank archetype",
            list(config.banks),
            index=list(config.banks).index(demo.DEMO_BANK),
            format_func=lambda k: config.banks[k].name,
            key="bank",
        )
    with st.container(key="ctl_scenario"):
        scenario_key = st.selectbox(
            "Trigger scenario",
            list(config.scenarios),
            index=list(config.scenarios).index(demo.DEMO_SCENARIO),
            format_func=lambda k: config.scenarios[k].name,
            key="scenario",
        )
    start_bank = config.banks[bank_key]
    start_scenario = config.scenarios[scenario_key]
    bank, scenario = start_bank, start_scenario
    resolved = config.model.with_overrides(scenario.overrides)
    # Slider keys include the bank and scenario, so the sliders reset when either changes.
    prefix = f"{bank_key}:{scenario_key}:"

    st.header("Parameters")
    severity = st.slider(
        "Scenario severity",
        min_value=float(scenario.severity.low),
        max_value=float(scenario.severity.high),
        value=float(scenario.severity.value),
        step=0.1,
        key=prefix + "severity",
        help="Multiplies every deposit category's base run-off rate.",
    )
    with st.container(key="ctl_uninsured"):
        share_pct = st.slider(
            "Uninsured deposits (% of deposits)",
            min_value=UNINSURED_SHARE_RANGE[0],
            max_value=UNINSURED_SHARE_RANGE[1],
            value=round(100 * uninsured_share(bank)),
            step=1,
            key=prefix + "uninsured_share",
            help="Everything except insured retail deposits. Total deposits stay the same.",
        )
    overrides = dict(scenario.overrides)
    shown_values = []
    for name, (label, factor, step, fmt, help_text) in PARAMETER_SLIDERS.items():
        base = getattr(config.model, name)
        current = getattr(resolved, name)
        shown = st.slider(
            label,
            min_value=base.low * factor,
            max_value=base.high * factor,
            value=current.value * factor,
            step=step,
            format=fmt,
            key=prefix + name,
            help=help_text,
        )
        shown_values.append(shown)
        # The slider sets the central value; the uncertainty range moves with it.
        overrides[name] = recentre(name, current, shown / factor)

    st.header("Uncertainty")
    n_runs = int(config.model.monte_carlo_runs.value)
    seed = st.number_input(
        "Random seed",
        min_value=0,
        value=int(config.model.monte_carlo_seed.value),
        step=1,
        help=f"The same seed always gives the same {n_runs} runs.",
    )

# When any input changes, playback stops and the timeline shows the whole new run.
signature = (prefix, severity, share_pct, *shown_values)
if st.session_state.get("run_signature") != signature:
    st.session_state["run_signature"] = signature
    st.session_state["play_next"] = None
    st.session_state.pop("day", None)

# Less empty space above the title, so the chart starts higher on a laptop screen.
st.html("<style>.stMainBlockContainer { padding-top: 2.5rem; }</style>")
st.title("Crisis Scenario Engine")
st.caption(f"{bank.name} · {scenario.name}. {scenario.description} Amounts in {bank.unit}.")

demo_on = st.session_state["demo_on"]
demo_step = st.session_state["demo_step"]
if demo_on:
    demo_panel(demo_step)
    if demo_step < demo.FIRST_STEP_WITH_RESULTS:
        st.info("The results appear here once you press Run the simulation (step 3).")
        st.stop()

# The slider starts at the rounded share; only rebuild the bank when it was moved.
if share_pct != round(100 * uninsured_share(bank)):
    bank = with_uninsured_share(bank, share_pct / 100)
scenario = scenario.model_copy(
    update={
        "severity": recentre("severity", scenario.severity, severity),
        "overrides": overrides,
    }
)
result = simulate(bank, config.model, scenario)
reference = simulate(start_bank, config.model, start_scenario)
table = result.table
horizon = int(config.model.horizon_days.value)
# The runs are kept while the inputs stay the same, so playback and tab changes are instant.
runs_key = (signature, n_runs, int(seed))
if st.session_state.get("runs_key") != runs_key:
    st.session_state["runs"] = run_monte_carlo(bank, config.model, scenario, n_runs, int(seed))
    st.session_state["runs_key"] = runs_key
monte_carlo = st.session_state["runs"]
uncertainty = summarise(monte_carlo)

failure, deposit_loss, funding = st.columns(3)
failure.metric(
    "Failure day",
    f"Day {result.failure_day}" if result.failure_day is not None else f"None in {horizon} days",
    delta=demo.describe_shift(result.failure_day, reference.failure_day),
    delta_color="off",
    delta_arrow="off",
    help="Central run. The line below compares it with the bank and scenario as first shown, "
    "before any slider was moved.",
)
deposit_loss.metric("Cumulative deposit loss", f"{table['cum_deposit_loss_pct'].iloc[-1]:.1f}%")
funding.metric(
    f"Resolution funding need ({bank.unit})",
    f"{result.funding_need.need:.2f}" if result.funding_need else "n/a",
    help="Liquidity needed to cover projected outflows after the failure day.",
)

if uncertainty.funding_need_low is None:
    need_range = "no run fails"
else:
    need_range = (
        f"{uncertainty.funding_need_low:.2f} to {uncertainty.funding_need_high:.2f} {bank.unit}"
    )
st.caption(
    f"Across {n_runs} runs with uncertain parameters: the bank fails in "
    f"{uncertainty.fail_share:.0%} of runs; failure {describe_failure_day(uncertainty, horizon)}; "
    f"funding need {need_range}. Ranges are 5th to 95th percentiles. The headline figures "
    "above are the central run, which is not always the median."
)

# on_change="rerun" makes the tabs lazy: only the open tab is computed.
timeline_tab, funding_tab, uncertainty_tab, script_tab, contagion_tab = st.tabs(
    demo.TABS, key="view", on_change="rerun"
)

if not timeline_tab.open:
    st.session_state["play_next"] = None

if timeline_tab.open:
    with timeline_tab:
        timeline_view(result, horizon, bank.unit, bands(monte_carlo), n_runs)

if funding_tab.open:
    with funding_tab:
        st.subheader("How each day's outflow was funded")
        st.caption(
            "Order of use: cash, sale of liquid assets (HQLA), central bank funding, "
            f"fire sale of illiquid assets. Amounts in {bank.unit}."
        )
        waterfall = table[list(WATERFALL_COLUMNS)].rename(columns=WATERFALL_COLUMNS)
        waterfall["Status"] = waterfall["Status"].str.replace("_", " ")
        amount_columns = [c for c in waterfall.columns if c not in ("Day", "Status")]
        st.dataframe(
            waterfall,
            hide_index=True,
            width="stretch",
            column_config={
                c: st.column_config.NumberColumn(format="%.2f") for c in amount_columns
            },
        )

if uncertainty_tab.open:
    with uncertainty_tab:
        st.subheader("Failure-day distribution")
        st.caption(f"Number of the {n_runs} runs in which the bank fails on each day.")
        st.plotly_chart(
            build_failure_distribution(failure_day_counts(monte_carlo), horizon), width="stretch"
        )
        st.caption(
            "Every parameter and its range is listed on the Assumptions page; most are "
            "assumptions without a verifiable source."
        )

if script_tab.open:
    with script_tab:
        st.subheader("Exercise script")
        script = build_script(bank, config.model, scenario, result, config.injects)
        st.write(script.summary)
        stem = f"{bank_key}_{scenario_key}"
        with st.container(key="ctl_export"):
            docx_button, injects_button, data_button = st.columns(3)
            docx_button.download_button(
                "Download script (.docx)",
                data=script_to_docx(script),
                file_name=f"exercise_script_{stem}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            injects_button.download_button(
                "Download injects (.csv)",
                data=injects_to_csv(script),
                file_name=f"injects_{stem}.csv",
                mime="text/csv",
            )
            data_button.download_button(
                "Download daily data (.csv)",
                data=daily_table_to_csv(result),
                file_name=f"daily_table_{stem}.csv",
                mime="text/csv",
            )
        for key_day in script.key_days:
            titles = "; ".join(event.title for event in key_day.events)
            with st.expander(f"Day {key_day.day}: {titles}"):
                for event in key_day.events:
                    st.markdown(f"**{event.title}**")
                    st.write(event.situation)
                    for inject in event.injects:
                        st.markdown(
                            f"*{inject.actor_name}*\n\n"
                            f"- **What you see:** {inject.sees}\n"
                            f"- **Decision due:** {inject.decision}"
                        )


def _outcome(run) -> str:
    stage = run.table["status"].iloc[-1].replace("_", " ")
    if run.failure_day is not None:
        return f"Fails day {run.failure_day}"
    return f"No failure ({stage})"


if contagion_tab.open:
    with contagion_tab:
        st.subheader("Contagion to a second bank")
        st.caption(
            "A second bank in calm conditions is hit by news about the first bank, one day "
            "after each trigger stage. The shock is scaled by how similar the two banks are."
        )
        second_column, country_column = st.columns(2)
        second_key = second_column.selectbox(
            "Second bank archetype",
            list(config.banks),
            index=list(config.banks).index(bank_key),
            format_func=lambda k: config.banks[k].name,
            # Keyed by the first bank, so it returns to "same archetype" when that changes.
            key=f"second_bank:{bank_key}",
        )
        same_country = country_column.checkbox(
            "Same country as the first bank", value=True, key="same_country"
        )
        similarity = similarity_factor(second_key == bank_key, same_country, config.model)
        contagion = simulate_contagion(
            bank,
            scenario,
            config.banks[second_key],
            config.scenarios["baseline"],
            config.model,
            similarity,
        )
        similarity_metric, alone_metric, hit_metric = st.columns(3)
        similarity_metric.metric(
            "Similarity",
            f"{similarity:.0%}",
            help="Same archetype and same country each add to the similarity. "
            "At 0% nothing spreads.",
        )
        alone_metric.metric(
            "Second bank deposit loss, without contagion",
            f"{contagion.second_alone.table['cum_deposit_loss_pct'].iloc[-1]:.1f}%",
            help=_outcome(contagion.second_alone),
        )
        hit_metric.metric(
            "Second bank deposit loss, with contagion",
            f"{contagion.second.table['cum_deposit_loss_pct'].iloc[-1]:.1f}%",
            help=_outcome(contagion.second),
        )
        st.write(
            f"Outcome for the second bank: **{_outcome(contagion.second)}** with contagion, "
            f"**{_outcome(contagion.second_alone)}** without."
        )
        if contagion.events:
            st.plotly_chart(build_contagion_chart(contagion, horizon), width="stretch")
            for event in contagion.events:
                st.markdown(f"- Day {event.day}: {event.headline}")
        else:
            st.info(
                "Nothing spreads to the second bank: either the two banks have no similarity, "
                "or the first bank reaches no trigger stage."
            )
