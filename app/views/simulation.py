"""Simulation page: selectors, sliders, results in tabs and the guided demo."""

import time

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from app import demo
from app.charts import (
    TRIGGERS,
    build_failure_distribution,
    build_path_comparison,
    build_timeline,
)
from engine.config import load_config
from engine.decisions import (
    OPEN_OUTCOMES,
    OUTCOME_LABELS,
    Choice,
    choices_of,
    decision_log_rows,
    intraday_allocation,
    scorecard_rows,
)
from engine.export import main_events_rows, pack_to_zip
from engine.model import simulate, uninsured_share, with_uninsured_share
from engine.resolution import (
    LEVELS,
    level_value,
    breakpoint_text,
    resolution_levels,
    summary_rows,
)
from engine.schema import TAXONOMY, TAXONOMY_TITLES
from engine.schema import PLAYERS
from engine.setup import session_plans
from engine.script import (
    MODE_LABELS,
    MODE_TEXT,
    MODES,
    active_challenges,
    build_pack,
    scenario_with_challenges,
)
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
    "funded_ela": "ELA",
    "funded_fire_sale": "Fire sale",
    "unmet_outflow": "Unmet",
    "queued_withdrawals": "Queued",
    "status": "Status",
}


def _status_label(status: str) -> str:
    """Stage name as shown to the user; the engine calls the payments default 'failed'."""
    return "payments default" if status == "failed" else status.replace("_", " ")


@st.cache_resource
def get_config():
    return load_config()


# ------------------------------------------------- scenario designer


def scenario_designer(config, default: str) -> list[str]:
    """Filters by the EBA Handbook's taxonomy; returns the scenarios that match."""
    chosen = {}
    with st.expander("Scenario designer"):
        st.caption(
            "Filter the scenarios by the taxonomy of the EBA Handbook on simulation exercises "
            "(par. 115): type of issue, systemic relevance, speed of events."
        )
        for dimension, values in TAXONOMY.items():
            chosen[dimension] = st.selectbox(
                TAXONOMY_TITLES[dimension],
                ["any", *values],
                format_func=lambda k, values=values: "Any" if k == "any" else values[k],
                key=f"taxonomy:{dimension}",
            )
    active = {d: v for d, v in chosen.items() if v != "any"}
    if not active:
        options = list(config.scenarios)
    else:
        options = [
            key for key, scenario in config.scenarios.items()
            if scenario.taxonomy is not None
            and all(getattr(scenario.taxonomy, d) == v for d, v in active.items())
        ]
    if not options:
        if active.get("scope") == "system_wide":
            st.warning("System-wide scenarios need several banks: not available yet. All scenarios are shown.")
        else:
            st.warning("No scenario matches this combination. All scenarios are shown.")
        options = list(config.scenarios)
    if st.session_state.get("scenario") not in options:
        st.session_state["scenario"] = default if default in options else options[0]
    return options


# ------------------------------------------------- decisions


def _load_path() -> None:
    """Set every decision widget to the options and days of the chosen named path."""
    catalogue = get_config().decisions
    chosen = {c.decision: c for c in catalogue.paths[st.session_state["decision_path"]].choices}
    for key, decision in catalogue.decisions.items():
        choice = chosen.get(key)
        st.session_state[f"dec:{key}:option"] = choice.option if choice else decision.default_option
        if choice:
            st.session_state[f"dec:{key}:day"] = choice.day


def decision_panel(catalogue, horizon: int) -> tuple[Choice, ...]:
    """Sidebar panel: one option per decision, and the day from which it may be taken."""
    st.selectbox(
        "Decision path",
        list(catalogue.paths),
        format_func=lambda k: catalogue.paths[k].name,
        key="decision_path",
        on_change=_load_path,
        help="A ready-made set of decisions. Change single decisions below.",
    )
    choices = []
    with st.expander("Decisions"):
        st.caption(
            "A decision is taken after the close of a day and is in effect the next day. "
            "If its condition is not met yet, it is taken as soon as it is."
        )
        for key, decision in catalogue.decisions.items():
            actor = decision.actor.replace("_", " ").capitalize()
            option = st.selectbox(
                f"{actor}: {decision.name}",
                list(decision.options),
                index=list(decision.options).index(decision.default_option),
                format_func=lambda k, options=decision.options: options[k].name,
                key=f"dec:{key}:option",
            )
            if option != decision.default_option:
                day_key = f"dec:{key}:day"
                # A value loaded from a named path is already in the session state.
                start = {} if day_key in st.session_state else {"value": 1}
                day = st.number_input(
                    f"{decision.name}: decide from the close of day",
                    min_value=0,
                    max_value=horizon,
                    step=1,
                    key=day_key,
                    **start,
                )
                choices.append(Choice(key, option, int(day)))
    return tuple(choices)


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
def timeline_view(result, horizon, unit, band_data, n_runs, start_weekday, weekends, params) -> None:
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
        build_timeline(
            result, horizon, unit, band_data, until_day=day,
            start_weekday=start_weekday, weekends=weekends,
        ),
        width="stretch",
    )
    chart_column.caption(
        f"Line: central run. Shaded band, shown on the last day: 5th to 95th percentile of "
        f"{n_runs} runs; a run that ends in a payments default keeps its last values."
    )
    row = table[table["day"] == day].iloc[0]
    with day_column:
        weekday = f", {row['weekday']}" if row["weekday"] else ""
        st.markdown(f"**Day {day}{weekday}: {_status_label(row['status'])}**")
        if row["weekend"]:
            st.caption("Weekend: no market sales, withdrawals paid from cash only.")
        if row["queued_withdrawals"] > 0:
            st.metric(f"Requested, not yet settled ({unit})", f"{row['queued_withdrawals']:.2f}")
        st.metric(f"Liquidity buffer ({unit})", f"{row['liquidity_buffer']:.2f}")
        st.metric("Deposits lost", f"{row['cum_deposit_loss_pct']:.1f}%")
        st.metric("CET1 ratio", f"{100 * row['cet1_ratio']:.1f}%")
        if row["outflow_total"] > 0:
            st.markdown("**Outflow by time of day**")
            for label, amount, _ in intraday_allocation(row["outflow_total"], params):
                st.markdown(f"- {label}: {amount:.2f}")
            st.caption("An allocation of the daily figure with fixed shares, not a simulation.")
        st.markdown("**Events so far**")
        events = [event for event in result.events if event.day <= day]
        if not events:
            st.caption("Nothing yet.")
        for event in events:
            label = TRIGGERS[event.kind][0] if event.kind in TRIGGERS else event.kind.capitalize()
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
showcase = config.script.showcase
if "decision_path" not in st.session_state:
    # The app opens on the showcase run, with its decision path loaded.
    st.session_state["decision_path"] = showcase.path
    _load_path()
st.session_state.setdefault("demo_on", True)
st.session_state.setdefault("demo_step", 0)

with st.sidebar:
    st.toggle("Guided demo", key="demo_on", help="A six-step walk through the tool.")
    st.header("Scenario")
    with st.container(key="ctl_bank"):
        bank_key = st.selectbox(
            "Bank archetype",
            list(config.banks),
            index=list(config.banks).index(showcase.bank),
            format_func=lambda k: config.banks[k].name,
            key="bank",
        )
    with st.container(key="ctl_scenario"):
        scenario_options = scenario_designer(config, showcase.scenario)
        # Its value is set in the session state by the designer, so no index is given here.
        scenario_key = st.selectbox(
            "Trigger scenario",
            scenario_options,
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

    st.header("Decisions")
    with st.container(key="ctl_decisions"):
        choices = decision_panel(config.decisions, int(config.model.horizon_days.value))
    for key, item in config.script.challenges.items():
        if item.effect == "panic_shock":
            st.checkbox(
                f"Challenge inject: {item.title.lower()}",
                key=f"challenge_{key}",
                help="A public news event with a panic shock, placed after its stage on the "
                "path without decisions. The only challenge inject that changes the run. "
                "The others are chosen in the Exercise script tab.",
            )

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
signature = (
    prefix, severity, share_pct, *shown_values, choices,
    *(st.session_state.get(f"challenge_{key}", False) for key in config.script.challenges),
)
if st.session_state.get("run_signature") != signature:
    st.session_state["run_signature"] = signature
    st.session_state["play_next"] = None
    st.session_state.pop("day", None)

# Less empty space above the title, so the chart starts higher on a laptop screen.
st.html("<style>.stMainBlockContainer { padding-top: 2.5rem; }</style>")
st.title("Crisis Scenario Engine")
kind = f" ({scenario.taxonomy.label()})" if scenario.taxonomy else ""
st.caption(f"{bank.name} · {scenario.name}{kind}. {scenario.description} Amounts in {bank.unit}.")

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
# The only challenge inject that changes the run: a news event with a panic shock.
news_challenges = [
    key for key, item in config.script.challenges.items()
    if item.effect == "panic_shock" and st.session_state.get(f"challenge_{key}")
]
scenario = scenario_with_challenges(
    bank, config.model, scenario, config.decisions, config.script, news_challenges
)
weekends = resolved.weekend_rules_on.value >= 1
result = simulate(bank, config.model, scenario, config.decisions, choices)
reference = simulate(start_bank, config.model, start_scenario, config.decisions, choices)
table = result.table
horizon = int(config.model.horizon_days.value)
# The runs are kept while the inputs stay the same, so playback and tab changes are instant.
runs_key = (signature, n_runs, int(seed))
if st.session_state.get("runs_key") != runs_key:
    st.session_state["runs"] = run_monte_carlo(
        bank, config.model, scenario, n_runs, int(seed), config.decisions, choices
    )
    st.session_state["runs_key"] = runs_key
monte_carlo = st.session_state["runs"]
uncertainty = summarise(monte_carlo)

failure, flag, deposit_loss, funding = st.columns(4)
flag.metric(
    "Likely-to-fail flag",
    f"Day {result.likely_to_fail_day}" if result.likely_to_fail_day is not None else "Not raised",
    help="First day on which liquidity covers too few days of outflows, or capital is "
    "projected below the requirement. A warning by the model; the run continues.",
)
if result.failure_day is not None:
    default_text = f"Day {result.failure_day}"
elif result.outcome in OPEN_OUTCOMES:
    default_text = f"None in {horizon} days"
else:
    default_text = "Avoided"
if result.failure_day is not None:
    default_note = demo.describe_shift(result.failure_day, reference.failure_day)
elif result.outcome in OPEN_OUTCOMES:
    default_note = OUTCOME_LABELS[result.outcome]
else:
    default_note = f"{OUTCOME_LABELS[result.outcome]} on day {result.end_day}"
failure.metric(
    "Payments default",
    default_text,
    delta=default_note,
    delta_color="off",
    delta_arrow="off",
    help="Central run. The line below compares it with the bank and scenario as first shown, "
    "before any slider was moved.",
)
deposit_loss.metric("Cumulative deposit loss", f"{table['cum_deposit_loss_pct'].iloc[-1]:.1f}%")
funding.metric(
    f"Resolution funding need ({bank.unit})",
    f"{result.funding_need.need:.2f}" if result.funding_need else "n/a",
    help="Liquidity needed to cover projected outflows after the payments default.",
)

if uncertainty.funding_need_low is None:
    need_range = "no run defaults"
else:
    need_range = (
        f"{uncertainty.funding_need_low:.2f} to {uncertainty.funding_need_high:.2f} {bank.unit}"
    )
st.caption(
    f"Across {n_runs} runs with uncertain parameters: the bank defaults on payments in "
    f"{uncertainty.fail_share:.0%} of runs; default {describe_failure_day(uncertainty, horizon)}; "
    f"funding need {need_range}. Ranges are 5th to 95th percentiles. The headline figures "
    "above are the central run, which is not always the median."
)

# on_change="rerun" makes the tabs lazy: only the open tab is computed.
(
    timeline_tab,
    decisions_tab,
    funding_tab,
    uncertainty_tab,
    script_tab,
) = st.tabs(demo.TABS, key="view", on_change="rerun")

if not timeline_tab.open:
    st.session_state["play_next"] = None

if timeline_tab.open:
    with timeline_tab:
        timeline_view(
            result, horizon, bank.unit, bands(monte_carlo), n_runs,
            scenario.start_weekday, weekends, resolved,
        )

if decisions_tab.open:
    with decisions_tab:
        paths = config.decisions.paths
        st.subheader("Decision paths compared")
        st.caption(
            "Same bank, scenario and parameters; only the decisions differ. The selected path "
            "is the one set in the sidebar. Decision effects are assumptions."
        )
        reference_key = st.selectbox(
            "Compare the selected path with",
            list(paths),
            index=list(paths).index(config.decisions.default_path),
            format_func=lambda k: paths[k].name,
            key="reference_path",
        )
        other = simulate(
            bank, config.model, scenario, config.decisions, choices_of(paths[reference_key])
        )
        other_name = paths[reference_key].name
        st.plotly_chart(
            build_path_comparison(
                other, result, other_name, "Selected path", horizon, bank.unit,
                scenario.start_weekday, weekends,
            ),
            width="stretch",
        )
        st.subheader("Outcome scorecard")
        left = scorecard_rows(other.scorecard, bank.unit)
        right = scorecard_rows(result.scorecard, bank.unit)
        st.dataframe(
            pd.DataFrame(
                {
                    "Figure": [label for label, _ in left],
                    other_name: [value for _, value in left],
                    "Selected path": [value for _, value in right],
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption("Covered deposits are the insured retail deposits.")
        both = {
            other_name: resolution_levels(bank, config.model, other),
            "Selected path": resolution_levels(bank, config.model, result),
        }
        if any(levels is not None for levels in both.values()):
            st.subheader("Resolution summary")
            st.caption(
                "What happens to capital and creditors once the bank is declared failing or "
                "likely to fail. The write-down found by the valuation is not known: pick an "
                "assumed level. Losses fall in the order used in this model, which is not a "
                "statement of law; legal loss-absorption thresholds are not modelled."
            )
            st.radio(
                "Valuation write-down of the loan book (assumption)",
                LEVELS,
                index=1,
                key="writedown_level",
                horizontal=True,
                format_func=lambda level: (
                    f"{level.capitalize()}: "
                    f"{100 * level_value(config.model.resolution_writedown_share, level):.0f}%"
                ),
            )
            level = st.session_state["writedown_level"]
            columns = {
                name: dict(summary_rows(levels[level], bank.unit)) if levels else {}
                for name, levels in both.items()
            }
            labels = list(dict.fromkeys(label for rows in columns.values() for label in rows))
            st.dataframe(
                pd.DataFrame(
                    {"Figure": labels}
                    | {name: [rows.get(label, "") for label in labels] for name, rows in columns.items()}
                ),
                hide_index=True,
                width="stretch",
            )
            for name, levels in both.items():
                if levels is not None:
                    st.caption(f"{name}: {breakpoint_text(levels[level])}")
        st.subheader("Decision log of the selected path")
        if result.decision_log:
            st.dataframe(
                pd.DataFrame(decision_log_rows(result.decision_log, bank.unit)),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No decision is taken on the selected path: the authorities act late.")

if funding_tab.open:
    with funding_tab:
        st.subheader("How each day's outflow was funded")
        st.caption(
            "Order of use: cash, sale of liquid assets (HQLA), central bank funding, emergency "
            "liquidity assistance (ELA, only if granted), fire sale of illiquid assets. "
            f"Amounts in {bank.unit}."
        )
        waterfall = table[list(WATERFALL_COLUMNS)].rename(columns=WATERFALL_COLUMNS)
        waterfall["Status"] = waterfall["Status"].map(_status_label)
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
        st.subheader("Payments default day distribution")
        st.caption(
            f"Number of the {n_runs} runs in which the bank defaults on payments on each day."
        )
        st.plotly_chart(
            build_failure_distribution(
                failure_day_counts(monte_carlo), horizon, scenario.start_weekday
            ),
            width="stretch",
        )
        st.caption(
            "Every parameter and its range is listed on the Assumptions page; most are "
            "assumptions without a verifiable source."
        )

if script_tab.open:
    with script_tab:
        paths = config.decisions.paths
        st.subheader("Exercise script")
        st.caption(
            "For the control team: the inject sheets of the four players, each with only "
            "what that player knows and when it knows it, the main events list, the role "
            "cards of the stakeholders the control team plays, and the control team pack. "
            "Wording is a draft."
        )
        other_key = st.selectbox(
            "Second branch of the script",
            list(paths),
            index=list(paths).index(showcase.compare_path),
            format_func=lambda k: paths[k].name,
            key="script_branch",
            help="The script covers this path and the one selected in the sidebar.",
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
            value=True,
            key="script_ambient",
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
            default=[key for key in quiet if key in defaults],
            format_func=lambda k: quiet[k].title,
            key=f"script_challenges_{'hostile' if hostile else 'friendly'}",
            help="Injects that challenge the players. They change no figure of the run. The "
            "rating downgrade, which does, is switched on in the sidebar.",
        )
        setup = config.script.setup
        left, right = st.columns(2)
        sessions = left.number_input(
            "Session plan: number of sessions", min_value=1, max_value=20, step=1,
            value=int(setup.sessions.value), key="script_sessions",
            help="The simulated days are mapped to this many real sessions, with breaks at "
            "the FoLtF determination and at the resolution decision where possible.",
        )
        session_hours = right.number_input(
            "Hours per session", min_value=0.5, max_value=12.0, step=0.5,
            value=float(setup.session_hours.value), key="script_session_hours",
        )
        other_choices = choices_of(paths[other_key])
        branches = [("Path selected in the sidebar", choices)]
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
