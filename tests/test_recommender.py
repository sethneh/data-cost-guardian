"""Unit tests for the v2 recommender: rules fire correctly, savings math is
right, the noise floor is respected, and totals add up."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from src.recommender import recommend, total_projected_savings

BASE_CONFIG = {
    "windows": [7, 30],
    "alerts": {"threshold_pct": 50.0, "z_score": 2.5, "min_cost_usd": 10.0},
    "recommendations": {
        "enabled": True,
        "idle": {"idle_day_pct": 0.05, "min_idle_fraction": 0.25},
        "weekend": {"dev_name_patterns": ["dev", "test", "sandbox", "staging"],
                    "min_weekend_share": 0.10},
        "commitment": {"max_cv": 0.25, "min_monthly_usd": 2000.0,
                       "discount_assumption": 0.15},
        "model_review": {"min_monthly_usd": 500.0, "min_cv": 0.40,
                         "routable_fraction_assumption": 0.30},
    },
}

REQUIRED_KEYS = {"id", "platform", "service", "type", "title", "evidence",
                 "projected_monthly_savings_usd", "confidence", "effort",
                 "rationale"}


def _make_df(entries: list[tuple[str, str, int, float]]) -> pd.DataFrame:
    """entries: (platform, service, days_ago, cost_usd)."""
    end = date.today()
    rows = [{"date": end - timedelta(days=d), "platform": p,
             "service": s, "cost_usd": c} for p, s, d, c in entries]
    return pd.DataFrame(rows)


def test_idle_service_fires_with_correct_savings():
    # 30 days: 24 idle ($2) + 6 active ($100) → idle fraction 80%
    entries = ([("snowflake", "IDLE_WH", d, 2.0) for d in range(6, 30)]
               + [("snowflake", "IDLE_WH", d, 100.0) for d in range(6)])
    recs = recommend(_make_df(entries), BASE_CONFIG)
    idle = [r for r in recs if r["type"] == "idle_resource"]
    assert len(idle) == 1
    # mean = (24*2 + 6*100)/30 = 21.6; savings = 21.6 * 0.8 * 30 = 518.4
    assert idle[0]["projected_monthly_savings_usd"] == pytest.approx(518.4)
    assert idle[0]["confidence"] == "high"


def test_flat_high_baseline_fires_commitment():
    entries = [("snowflake", "STEADY_WH", d, 200.0) for d in range(40)]
    recs = recommend(_make_df(entries), BASE_CONFIG)
    commit = [r for r in recs if r["type"] == "sustained_baseline"]
    assert len(commit) == 1
    # monthly = 200*30 = 6000; savings = 6000 * 0.15 = 900
    assert commit[0]["projected_monthly_savings_usd"] == pytest.approx(900.0)
    assert "assumption" in commit[0]["evidence"]


def test_sub_noise_floor_service_fires_nothing():
    # Spiky-looking, but mean $5.60/day is below the $10 noise floor.
    entries = ([("snowflake", "TINY_WH", d, 2.0) for d in range(6, 30)]
               + [("snowflake", "TINY_WH", d, 20.0) for d in range(6)])
    assert recommend(_make_df(entries), BASE_CONFIG) == []


def test_weekend_burn_fires_for_dev_service():
    # 4 full weeks anchored on a Monday: weekdays $60, weekends $40.
    end = date.today()
    monday = end - timedelta(days=end.weekday())
    entries = []
    for i in range(28):
        d = monday + timedelta(days=i)
        days_ago = (end - d).days
        cost = 40.0 if d.weekday() >= 5 else 60.0
        entries.append(("databricks", "DEV_ETL", days_ago, cost))
    recs = recommend(_make_df(entries), BASE_CONFIG)
    wknd = [r for r in recs if r["type"] == "weekend_burn"]
    assert len(wknd) == 1
    # weekend daily avg $40 × 8.57 weekend-days/month
    assert wknd[0]["projected_monthly_savings_usd"] == pytest.approx(342.8)
    # monthly weekday baseline $1800 < $2000 → no commitment rec for it
    assert not [r for r in recs if r["type"] == "sustained_baseline"]


def test_total_projected_savings_sums_correctly():
    idle_entries = ([("snowflake", "IDLE_WH", d, 2.0) for d in range(6, 30)]
                    + [("snowflake", "IDLE_WH", d, 100.0) for d in range(6)])
    steady_entries = [("snowflake", "STEADY_WH", d, 200.0) for d in range(40)]
    recs = recommend(_make_df(idle_entries + steady_entries), BASE_CONFIG)
    assert len(recs) >= 2
    # sorted by savings desc
    savings = [r["projected_monthly_savings_usd"] for r in recs]
    assert savings == sorted(savings, reverse=True)
    assert total_projected_savings(recs) == pytest.approx(sum(savings))
    assert total_projected_savings(recs) == pytest.approx(518.4 + 900.0)


def test_recommendations_are_json_serializable_with_required_keys():
    from src.demo_data import generate_demo_data
    recs = recommend(generate_demo_data(days=45, seed=7), BASE_CONFIG)
    json.dumps(recs)  # must not raise
    assert len(recs) >= 1
    for r in recs:
        assert REQUIRED_KEYS <= set(r.keys())
        assert r["confidence"] in {"high", "medium", "low"}
        assert r["effort"] in {"high", "medium", "low"}
        assert isinstance(r["projected_monthly_savings_usd"], float)
        assert all(isinstance(v, str) for v in r["evidence"].values())


def test_demo_run_produces_recommendations():
    from src.demo_data import generate_demo_data
    recs = recommend(generate_demo_data(days=90, seed=42), BASE_CONFIG)
    types = {r["type"] for r in recs}
    assert len(recs) >= 4
    assert "weekend_burn" in types       # DEV_WH scaled-down on weekends
    assert "sustained_baseline" in types  # COMPUTE_WH steady baseline
    assert "model_cost_review" in types   # gpt-4o pricey + spiky
    dev = next(r for r in recs if r["service"] == "DEV_WH")
    assert dev["type"] == "weekend_burn"
    assert dev["projected_monthly_savings_usd"] > 100


def test_weekend_service_skipped_for_commitment():
    # A dev service burning cash on weekends should get the pause-windows
    # rec, not a commitment rec — pause first, commit later. Weekdays $100
    # (flat → CV 0, monthly $3000 → commitment-eligible), weekends $35.
    end = date.today()
    monday = end - timedelta(days=end.weekday())
    entries = []
    for i in range(28):
        d = monday + timedelta(days=i)
        days_ago = (end - d).days
        cost = 35.0 if d.weekday() >= 5 else 100.0
        entries.append(("databricks", "DEV_BATCH", days_ago, cost))
    recs = recommend(_make_df(entries), BASE_CONFIG)
    by_type = {r["type"] for r in recs if r["service"] == "DEV_BATCH"}
    assert "weekend_burn" in by_type
    assert "sustained_baseline" not in by_type


def test_disabled_config_returns_empty():
    entries = [("snowflake", "STEADY_WH", d, 200.0) for d in range(40)]
    cfg = {**BASE_CONFIG, "recommendations": {"enabled": False}}
    assert recommend(_make_df(entries), cfg) == []


def test_idle_service_skipped_for_commitment():
    # An idle service should get the downsize rec, not a commitment rec —
    # commitments lock in capacity you should be eliminating.
    # Weekdays $200 (flat → CV 0, monthly $6000 → commitment-eligible),
    # weekends $2 (idle). Without the priority rule both would fire.
    end = date.today()
    entries = []
    for d in range(30):
        day = end - timedelta(days=d)
        cost = 2.0 if day.weekday() >= 5 else 200.0
        entries.append(("snowflake", "WEEKEND_IDLE_WH", d, cost))
    recs = recommend(_make_df(entries), BASE_CONFIG)
    by_type = {r["type"] for r in recs if r["service"] == "WEEKEND_IDLE_WH"}
    assert "idle_resource" in by_type
    assert "sustained_baseline" not in by_type
