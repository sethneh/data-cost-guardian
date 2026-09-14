"""Realistic synthetic spend data for Snowflake, Databricks, and AI APIs.

Generates ``days`` of daily spend with weekly seasonality (weekdays > weekends),
per-service noise, and a few injected cost anomalies so the demo always has
something interesting for the analyzer to find.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

# (platform, service, team, base_daily_usd, extra columns to synthesize)
SERVICES: list[tuple[str, str, str, float]] = [
    ("snowflake", "COMPUTE_WH", "analytics", 185.0),
    ("snowflake", "ANALYTICS_WH", "analytics", 120.0),
    ("snowflake", "INGEST_WH", "data-platform", 62.0),
    ("snowflake", "DEV_WH", "data-platform", 90.0),  # parked on weekends → idle rule fires
    ("databricks", "etl-cluster", "data-platform", 95.0),
    ("databricks", "ml-cluster", "ml-platform", 140.0),
    ("ai", "gpt-4o", "ai-apps", 26.0),
    ("ai", "claude-3-5-sonnet", "ai-apps", 18.0),
    ("ai", "gpt-4o-mini", "ai-apps", 6.0),
]

USERS = ["a.sharma", "j.chen", "m.okafor", "s.patel", "l.garcia", "r.kim"]

# (days_ago, service, multiplier, reason) — injected anomalies for the demo
ANOMALIES: list[tuple[int, str, float, str]] = [
    (4, "INGEST_WH", 4.2, "historical backfill re-ran the full bronze layer"),
    (13, "ml-cluster", 3.1, "unbounded hyperparameter sweep left running overnight"),
    (21, "gpt-4o", 5.4, "runaway eval script with no max-tokens cap"),
]

SNOWFLAKE_CREDIT_PRICE_USD = 3.0
DATABRICKS_DBU_PRICE_USD = 0.55


def _seasonality(d: date) -> float:
    """Weekday spend runs hotter than weekends; slight month-start bump."""
    weekly = 1.18 if d.weekday() < 5 else 0.55
    month_start = 1.08 if d.day <= 3 else 1.0
    return weekly * month_start


def generate_demo_data(days: int = 90, seed: int = 42) -> pd.DataFrame:
    """Return a DataFrame with columns:
    date, platform, service, team, user, cost_usd,
    credits (snowflake), dbus (databricks), tokens_in/tokens_out (ai).
    """
    rng = np.random.default_rng(seed)
    end = date.today()
    dates = [end - timedelta(days=i) for i in range(days - 1, -1, -1)]

    anomaly_lookup = {(a_days, svc): (mult, reason) for a_days, svc, mult, reason in ANOMALIES}

    rows: list[dict] = []
    for d in dates:
        days_ago = (end - d).days
        for platform, service, team, base in SERVICES:
            noise = rng.normal(1.0, 0.06)
            cost = max(0.5, base * _seasonality(d) * noise)
            if service == "DEV_WH":
                # Dev warehouse scaled down (not fully parked) on weekends:
                # still burns ~35% of its weekday rate — the weekend_burn
                # rule's canonical case. Damped noise and no month-start
                # batch bump keep v1's threshold from tripping on ordinary
                # weekdays (a fully-parked weekend would drag the trailing
                # mean down and cry wolf every Monday).
                level = 1.18 if d.weekday() < 5 else 1.18 * 0.35
                cost = max(0.5, base * level * (1.0 + (noise - 1.0) / 3.0))
            if (days_ago, service) in anomaly_lookup:
                mult, _reason = anomaly_lookup[(days_ago, service)]
                cost *= mult
            row: dict = {
                "date": d.isoformat(),
                "platform": platform,
                "service": service,
                "team": team,
                "user": str(rng.choice(USERS)),
                "cost_usd": round(float(cost), 2),
                "credits": round(float(cost) / SNOWFLAKE_CREDIT_PRICE_USD, 2)
                if platform == "snowflake"
                else 0.0,
                "dbus": round(float(cost) / DATABRICKS_DBU_PRICE_USD, 1)
                if platform == "databricks"
                else 0.0,
                "tokens_in": int(rng.integers(200_000, 2_000_000)) if platform == "ai" else 0,
                "tokens_out": int(rng.integers(50_000, 500_000)) if platform == "ai" else 0,
            }
            rows.append(row)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "platform", "service"]).reset_index(drop=True)


def anomaly_catalog() -> list[dict]:
    """Human-readable list of the injected anomalies (for docs/tests)."""
    end = date.today()
    return [
        {
            "date": (end - timedelta(days=days_ago)).isoformat(),
            "service": svc,
            "multiplier": mult,
            "reason": reason,
        }
        for days_ago, svc, mult, reason in ANOMALIES
    ]


if __name__ == "__main__":  # quick peek: python -m src.demo_data
    df = generate_demo_data()
    print(df.head(3).to_string(index=False))
    print(f"\n{len(df)} rows, {df['date'].nunique()} days, "
          f"total 30d spend ${df.tail(30 * len(SERVICES))['cost_usd'].sum():,.0f}")
