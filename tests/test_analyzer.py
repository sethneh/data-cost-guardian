"""Unit tests for the analyzer: rolling averages, anomaly detection, drivers."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.analyzer import (
    analyze,
    detect_anomalies,
    rolling_average,
    service_daily,
    top_cost_drivers,
)


def _flat_df(days: int = 40, cost: float = 100.0,
             service: str = "COMPUTE_WH", platform: str = "snowflake") -> pd.DataFrame:
    end = date.today()
    rows = [
        {"date": end - timedelta(days=i), "platform": platform,
         "service": service, "cost_usd": cost}
        for i in range(days - 1, -1, -1)
    ]
    return pd.DataFrame(rows)


def test_rolling_average_correct():
    s = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert rolling_average(s, 2) == pytest.approx(35.0)
    assert rolling_average(s, 10) == pytest.approx(25.0)  # shorter series → mean of all


def test_service_daily_aggregates():
    df = _flat_df(days=3, cost=50.0)
    df = pd.concat([df, df], ignore_index=True)  # duplicate rows → sums double
    svc = service_daily(df)
    assert (svc["cost_usd"] == 100.0).all()
    assert len(svc) == 3


def test_anomaly_flagged_when_spike_injected():
    df = _flat_df(days=40, cost=100.0)
    df.loc[df["date"] == df["date"].max(), "cost_usd"] = 400.0  # 4x spike today
    anomalies = detect_anomalies(service_daily(df), threshold_pct=50.0, z_thresh=2.5)
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a["service"] == "COMPUTE_WH"
    assert a["pct_over_avg"] == pytest.approx(300.0)
    assert a["severity"] == "critical"


def test_no_false_positive_on_flat_data():
    df = _flat_df(days=40, cost=100.0)
    anomalies = detect_anomalies(service_daily(df), threshold_pct=50.0, z_thresh=2.5)
    assert anomalies == []


def test_zscore_catches_moderate_but_unusual_jump():
    # noisy baseline, then a jump that is <50% over avg but many stddevs out
    import numpy as np
    rng = np.random.default_rng(0)
    df = _flat_df(days=40, cost=100.0)
    df["cost_usd"] = 100.0 + rng.normal(0, 1.0, size=len(df))  # tiny noise
    df.loc[df["date"] == df["date"].max(), "cost_usd"] = 120.0  # +20%, z≈20
    anomalies = detect_anomalies(service_daily(df), threshold_pct=50.0, z_thresh=2.5)
    assert len(anomalies) == 1
    assert anomalies[0]["z_score"] > 2.5


def test_small_spender_below_min_cost_ignored():
    df = _flat_df(days=40, cost=5.0)
    df.loc[df["date"] == df["date"].max(), "cost_usd"] = 50.0  # 10x but tiny $$
    anomalies = detect_anomalies(service_daily(df), threshold_pct=50.0,
                                 z_thresh=2.5, min_cost_usd=10.0)
    assert anomalies == []


def test_top_drivers_shares_sum_to_100():
    end = date.today()
    rows = []
    for i in range(30):
        d = end - timedelta(days=i)
        rows.append({"date": d, "platform": "snowflake", "service": "A", "cost_usd": 60.0})
        rows.append({"date": d, "platform": "snowflake", "service": "B", "cost_usd": 40.0})
    drivers = top_cost_drivers(service_daily(pd.DataFrame(rows)), top_n=5)
    assert {d["service"] for d in drivers} == {"A", "B"}
    assert sum(d["share_pct"] for d in drivers) == pytest.approx(100.0)
    assert drivers[0]["service"] == "A"  # sorted desc


def test_analyze_output_is_json_serializable():
    import json
    from src.demo_data import generate_demo_data
    findings = analyze(generate_demo_data(days=45, seed=7),
                       {"windows": [7, 30],
                        "alerts": {"threshold_pct": 50.0, "z_score": 2.5,
                                   "min_cost_usd": 10.0}})
    json.dumps(findings)  # must not raise
    assert findings["kpis"]["today_total"] > 0
    assert "week_over_week" in findings and "month_over_month" in findings
