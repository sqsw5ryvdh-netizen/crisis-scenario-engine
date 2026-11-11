"""Streamlit entry point. Run with: streamlit run app/main.py"""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app.charts import build_timeline  # noqa: E402
from engine.config import load_config  # noqa: E402
from engine.export import daily_table_to_csv, injects_to_csv, script_to_docx  # noqa: E402
from engine.injects import build_script  # noqa: E402
from engine.model import simulate, uninsured_share, with_uninsured_share  # noqa: E402

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
        "Bank archetype", list(config.banks), format_func=lambda k: config.banks[k].name
    )
    scenario_key = st.selectbox(
        "Trigger scenario", list(config.scenarios), format_func=lambda k: config.scenarios[k].name
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
        overrides[name] = current.model_copy(update={"value": shown / factor})

# The slider starts at the rounded share; only rebuild the bank when it was moved.
if share_pct != round(100 * uninsured_share(bank)):
    bank = with_uninsured_share(bank, share_pct / 100)
scenario = scenario.model_copy(
    update={
        "severity": scenario.severity.model_copy(update={"value": severity}),
        "overrides": overrides,
    }
)
result = simulate(bank, config.model, scenario)
table = result.table
horizon = int(config.model.horizon_days.value)

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

st.subheader("Timeline")
st.plotly_chart(build_timeline(result, horizon, bank.unit), width="stretch")

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
