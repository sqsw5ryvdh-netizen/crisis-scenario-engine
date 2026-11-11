"""Streamlit entry point. Run with: streamlit run app/main.py"""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app.charts import (  # noqa: E402
    build_contagion_chart,
    build_failure_distribution,
    build_timeline,
)
from engine.config import load_config  # noqa: E402
from engine.contagion import similarity_factor, simulate_contagion  # noqa: E402
from engine.export import daily_table_to_csv, injects_to_csv, script_to_docx  # noqa: E402
from engine.injects import build_script  # noqa: E402
from engine.model import simulate, uninsured_share, with_uninsured_share  # noqa: E402
from engine.montecarlo import (  # noqa: E402
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


st.set_page_config(page_title="Crisis Scenario Engine", layout="wide")
config = get_config()

with st.sidebar:
    st.header("Scenario")
    bank_key = st.selectbox(
        "Bank archetype",
        list(config.banks),
        format_func=lambda k: config.banks[k].name,
        key="bank",
    )
    scenario_key = st.selectbox(
        "Trigger scenario",
        list(config.scenarios),
        format_func=lambda k: config.scenarios[k].name,
        key="scenario",
    )
    bank = config.banks[bank_key]
    scenario = config.scenarios[scenario_key]
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
table = result.table
horizon = int(config.model.horizon_days.value)
monte_carlo = run_monte_carlo(bank, config.model, scenario, n_runs, int(seed))
uncertainty = summarise(monte_carlo)

st.title("Crisis Scenario Engine")
st.caption(f"{bank.name} · {scenario.name}. {scenario.description} Amounts in {bank.unit}.")

failure, deposit_loss, funding = st.columns(3)
failure.metric(
    "Failure day",
    f"Day {result.failure_day}" if result.failure_day is not None else f"None in {horizon} days",
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

st.subheader("Timeline")
st.plotly_chart(
    build_timeline(result, horizon, bank.unit, bands(monte_carlo)), width="stretch"
)
st.caption(
    f"Line: central run. Shaded band: 5th to 95th percentile of {n_runs} runs; "
    "a run that fails keeps its last values."
)

st.subheader("Failure-day distribution")
st.plotly_chart(
    build_failure_distribution(failure_day_counts(monte_carlo), horizon), width="stretch"
)

st.subheader("How each day's outflow was funded")
waterfall = table[list(WATERFALL_COLUMNS)].rename(columns=WATERFALL_COLUMNS)
waterfall["Status"] = waterfall["Status"].str.replace("_", " ")
amount_columns = [c for c in waterfall.columns if c not in ("Day", "Status")]
st.dataframe(
    waterfall,
    hide_index=True,
    width="stretch",
    column_config={c: st.column_config.NumberColumn(format="%.2f") for c in amount_columns},
)

st.subheader("Exercise script")
script = build_script(bank, config.model, scenario, result, config.injects)
st.write(script.summary)
stem = f"{bank_key}_{scenario_key}"
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

st.subheader("Contagion to a second bank")
st.caption(
    "A second bank in calm conditions is hit by news about the first bank, one day after "
    "each trigger stage. The shock is scaled by how similar the two banks are."
)
second_column, country_column = st.columns(2)
second_key = second_column.selectbox(
    "Second bank archetype",
    list(config.banks),
    index=list(config.banks).index(bank_key),
    format_func=lambda k: config.banks[k].name,
    # Keyed by the first bank, so it goes back to "same archetype" when the first bank changes.
    key=f"second_bank:{bank_key}",
)
same_country = country_column.checkbox(
    "Same country as the first bank", value=True, key="same_country"
)
similarity = similarity_factor(second_key == bank_key, same_country, config.model)
contagion = simulate_contagion(
    bank, scenario, config.banks[second_key], config.scenarios["baseline"], config.model, similarity
)


def _outcome(run) -> str:
    stage = run.table["status"].iloc[-1].replace("_", " ")
    if run.failure_day is not None:
        return f"Fails day {run.failure_day}"
    return f"No failure ({stage})"


similarity_metric, alone_metric, hit_metric = st.columns(3)
similarity_metric.metric(
    "Similarity",
    f"{similarity:.0%}",
    help="Same archetype and same country each add to the similarity. At 0% nothing spreads.",
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
