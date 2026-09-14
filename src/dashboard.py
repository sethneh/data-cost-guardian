"""Streamlit dashboard for Data Cost Guardian.

Reads the pipeline output in data/ (daily_spend.csv + findings.json).
Launch with:  streamlit run src/dashboard.py   (from the repo root)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SPEND_CSV = DATA_DIR / "daily_spend.csv"
FINDINGS_JSON = DATA_DIR / "findings.json"
RECOMMENDATIONS_JSON = DATA_DIR / "recommendations.json"

st.set_page_config(page_title="Data Cost Guardian", page_icon="💰", layout="wide")


@st.cache_data
def load_data() -> tuple[pd.DataFrame | None, dict | None]:
    if not SPEND_CSV.exists():
        return None, None
    df = pd.read_csv(SPEND_CSV, parse_dates=["date"])
    findings = json.loads(FINDINGS_JSON.read_text()) if FINDINGS_JSON.exists() else None
    return df, findings


@st.cache_data
def load_recommendations() -> list[dict] | None:
    if not RECOMMENDATIONS_JSON.exists():
        return None
    return json.loads(RECOMMENDATIONS_JSON.read_text())


def kpi_cards(findings: dict) -> None:
    k = findings["kpis"]
    cols = st.columns(4)
    cols[0].metric("Spend today", f"${k['today_total']:,.0f}")
    for i, (w, vals) in enumerate(k["averages"].items()):
        cols[i + 1].metric(f"{w} avg", f"${vals['avg']:,.0f}",
                           delta=f"{vals['delta_pct']:+.1f}% vs today")
    wow, mom = findings["week_over_week"], findings["month_over_month"]
    c2 = st.columns(2)
    c2[0].metric("Week over week", f"${wow['current']:,.0f}",
                 delta=f"{wow['delta_pct']:+.1f}%")
    c2[1].metric("Month over month", f"${mom['current']:,.0f}",
                 delta=f"{mom['delta_pct']:+.1f}%")


def trend_chart(df: pd.DataFrame) -> None:
    daily = df.groupby(["date", "platform"], as_index=False)["cost_usd"].sum()
    fig = px.line(daily, x="date", y="cost_usd", color="platform",
                  title="Daily spend trend by platform",
                  labels={"cost_usd": "USD / day", "date": ""})
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)


def stacked_bar(df: pd.DataFrame) -> None:
    last30 = df[df["date"] >= df["date"].max() - pd.Timedelta(days=29)]
    daily = last30.groupby(["date", "platform"], as_index=False)["cost_usd"].sum()
    fig = px.bar(daily, x="date", y="cost_usd", color="platform",
                 title="Daily spend composition — trailing 30 days",
                 labels={"cost_usd": "USD", "date": ""})
    st.plotly_chart(fig, use_container_width=True)


def anomaly_callouts(findings: dict) -> None:
    st.subheader("🚨 Anomalies")
    anomalies = findings.get("anomalies", [])
    if not anomalies:
        st.success("No anomalies vs 30-day baselines. All quiet. 🎉")
        return
    for a in anomalies:
        msg = (f"**{a['service']}** ({a['platform']}) — ${a['cost_usd']:,.2f} on {a['date']}: "
               f"+{a['pct_over_avg']:.0f}% vs 30d avg of ${a['baseline_30d_avg']:,.2f} "
               f"(z={a['z_score']})")
        if a["severity"] == "critical":
            st.error(msg)
        else:
            st.warning(msg)


def drivers_table(findings: dict) -> None:
    st.subheader("Top cost drivers (trailing 30d)")
    drivers = pd.DataFrame(findings.get("top_drivers", []))
    if drivers.empty:
        st.info("No driver data yet.")
        return
    st.dataframe(
        drivers.rename(columns={"service": "Service", "platform": "Platform",
                                "cost_usd": "30d cost ($)", "share_pct": "Share (%)",
                                "delta_vs_prev_pct": "Δ vs prev 30d (%)"}),
        use_container_width=True, hide_index=True,
    )


def service_breakdown(df: pd.DataFrame) -> None:
    st.subheader("Spend by service — trailing 30 days")
    last30 = df[df["date"] >= df["date"].max() - pd.Timedelta(days=29)]
    svc = last30.groupby(["platform", "service"], as_index=False)["cost_usd"].sum()
    svc = svc.sort_values("cost_usd", ascending=False)
    fig = px.bar(svc, x="service", y="cost_usd", color="platform",
                 title="", labels={"cost_usd": "USD (30d)", "service": ""})
    st.plotly_chart(fig, use_container_width=True)


def recommendations_section(recs: list[dict] | None) -> None:
    st.subheader("💡 Recommendations — projected savings")
    if not recs:
        st.info("No recommendations in this run. Check that the pipeline ran "
                "without --no-recommendations.")
        return
    recs = sorted(recs, key=lambda r: r["projected_monthly_savings_usd"],
                  reverse=True)
    total = sum(r["projected_monthly_savings_usd"] for r in recs)
    st.metric("Total projected monthly savings", f"${total:,.0f}")
    for r in recs:
        with st.expander(
                f"💰 ~${r['projected_monthly_savings_usd']:,.0f}/mo — {r['title']}"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Service", f"{r['service']}")
            c2.metric("Confidence", r["confidence"].title())
            c3.metric("Effort", r["effort"].title())
            st.write(f"**Platform:** {r['platform']} · **Type:** "
                     f"`{r['type']}`")
            st.write("**Evidence**")
            st.json(r["evidence"])
            st.write("**Why this is recommended**")
            st.write(r["rationale"])


def main() -> None:
    st.title("💰 Data Cost Guardian")
    st.caption("AI FinOps: Snowflake · Databricks · LLM spend vs 7d/30d baselines")

    df, findings = load_data()
    recs = load_recommendations()
    if df is None:
        st.warning("No pipeline output found. Run the pipeline first:\n\n"
                   "`python -m src.main --demo --report`")
        return

    tab_overview, tab_recs = st.tabs(["📊 Overview", "💡 Recommendations"])

    with tab_overview:
        with st.sidebar:
            st.header("Filters")
            platforms = sorted(df["platform"].unique())
            chosen = st.multiselect("Platforms", platforms, default=platforms)
            window = st.slider("Trend window (days)", 7, 90, 30)
        df = df[df["platform"].isin(chosen)]
        df = df[df["date"] >= df["date"].max() - pd.Timedelta(days=window - 1)]

        if findings:
            kpi_cards(findings)
            st.divider()
            anomaly_callouts(findings)
            st.divider()
        trend_chart(df)
        stacked_bar(df)
        if findings:
            drivers_table(findings)
        service_breakdown(df)
        st.caption(f"Data: {SPEND_CSV} · {len(df)} rows · "
                   f"generated {findings['generated_at'] if findings else 'n/a'}")

    with tab_recs:
        recommendations_section(recs)


if __name__ == "__main__":
    main()
