"""Spend analysis: rollups, rolling averages, comparisons, anomaly detection.

Input: DataFrame with at least [date, platform, service, cost_usd].
Output of :func:`analyze`: a JSON-serializable findings dict consumed by the
dashboard and the alerting engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {"date", "platform", "service", "cost_usd"}


def _check(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"input DataFrame missing columns: {sorted(missing)}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["cost_usd"] = out["cost_usd"].astype(float)
    return out


def service_daily(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (date, platform, service) with summed cost."""
    df = _check(df)
    g = df.groupby(["date", "platform", "service"], as_index=False)["cost_usd"].sum()
    return g.sort_values(["platform", "service", "date"]).reset_index(drop=True)


def daily_totals(df: pd.DataFrame) -> pd.DataFrame:
    """One row per date with total spend and per-platform columns."""
    svc = service_daily(df)
    totals = svc.groupby("date", as_index=False)["cost_usd"].sum().rename(
        columns={"cost_usd": "total"}
    )
    by_platform = svc.pivot_table(index="date", columns="platform",
                                  values="cost_usd", aggfunc="sum").reset_index()
    return totals.merge(by_platform, on="date", how="left").sort_values("date")


def rolling_average(values: pd.Series, window: int) -> float:
    """Mean of the last ``window`` values (fewer if the series is shorter)."""
    return float(values.tail(window).mean())


def period_change(svc: pd.DataFrame, days: int) -> dict:
    """Compare the most recent ``days`` against the ``days`` before them."""
    latest = svc["date"].max()
    cur = svc[svc["date"] > latest - pd.Timedelta(days=days)]["cost_usd"].sum()
    prev = svc[(svc["date"] <= latest - pd.Timedelta(days=days))
               & (svc["date"] > latest - pd.Timedelta(days=2 * days))]["cost_usd"].sum()
    pct = ((cur - prev) / prev * 100.0) if prev > 0 else 0.0
    return {"current": round(float(cur), 2), "previous": round(float(prev), 2),
            "delta_pct": round(float(pct), 2)}


def detect_anomalies(
    svc: pd.DataFrame,
    threshold_pct: float = 50.0,
    z_thresh: float = 2.5,
    min_cost_usd: float = 10.0,
    baseline_days: int = 30,
    min_baseline_days: int = 7,
) -> list[dict]:
    """Flag any day whose cost deviates from its own trailing baseline.

    Every day (after a warm-up of ``min_baseline_days``) is compared against
    the ``baseline_days`` preceding it. A day is anomalous when it exceeds the
    baseline average by ``threshold_pct`` **or** its z-score exceeds
    ``z_thresh``. The day itself is excluded from its baseline so a spike
    can't hide inside its own average, and services whose *normal* spend is
    below ``min_cost_usd``/day are ignored as noise.
    """
    svc = svc.sort_values(["platform", "service", "date"])
    findings: list[dict] = []
    for (platform, service), grp in svc.groupby(["platform", "service"]):
        grp = grp.sort_values("date").reset_index(drop=True)
        costs = grp["cost_usd"].to_numpy(dtype=float)
        dates = grp["date"]
        for i in range(1, len(costs)):
            baseline = costs[max(0, i - baseline_days):i]
            if len(baseline) < min_baseline_days:
                continue
            mean = float(baseline.mean())
            if mean < min_cost_usd:  # tiny spender — not worth alerting on
                continue
            std = float(baseline.std())
            cost = float(costs[i])
            pct_over = ((cost - mean) / mean * 100.0) if mean > 0 else 0.0
            z = ((cost - mean) / std) if std > 0 else 0.0
            if pct_over > threshold_pct or z > z_thresh:
                severity = "critical" if (pct_over > 100 or z > 4) else "warning"
                findings.append({
                    "date": dates.iloc[i].date().isoformat(),
                    "platform": platform,
                    "service": service,
                    "cost_usd": round(cost, 2),
                    "baseline_30d_avg": round(mean, 2),
                    "pct_over_avg": round(float(pct_over), 1),
                    "z_score": round(float(z), 2),
                    "severity": severity,
                })
    return sorted(findings, key=lambda f: (f["date"], f["pct_over_avg"]), reverse=True)


def top_cost_drivers(svc: pd.DataFrame, top_n: int = 5, window: int = 30) -> list[dict]:
    """Biggest spenders over the trailing ``window`` days, with share and
    delta vs the preceding window."""
    latest = svc["date"].max()
    cur = svc[svc["date"] > latest - pd.Timedelta(days=window)]
    prev = svc[(svc["date"] <= latest - pd.Timedelta(days=window))
               & (svc["date"] > latest - pd.Timedelta(days=2 * window))]
    cur_sum = cur.groupby(["platform", "service"])["cost_usd"].sum()
    prev_sum = prev.groupby(["platform", "service"])["cost_usd"].sum()
    total = float(cur_sum.sum())
    drivers = []
    for (platform, service), cost in cur_sum.sort_values(ascending=False).head(top_n).items():
        cost_f = float(cost)
        prev_f = float(prev_sum.get((platform, service), 0.0))
        delta = ((cost_f - prev_f) / prev_f * 100.0) if prev_f > 0 else 0.0
        drivers.append({
            "platform": platform,
            "service": service,
            "cost_usd": round(cost_f, 2),
            "share_pct": round(cost_f / total * 100.0, 1) if total > 0 else 0.0,
            "delta_vs_prev_pct": round(float(delta), 1),
        })
    return drivers


def kpis(svc: pd.DataFrame, totals: pd.DataFrame, windows: list[int]) -> dict:
    """Today vs rolling averages, overall and per platform."""
    latest = totals["date"].max()
    today_total = float(totals.loc[totals["date"] == latest, "total"].iloc[0])
    out: dict = {"as_of": latest.date().isoformat(), "today_total": round(today_total, 2),
                 "averages": {}, "per_platform": {}}
    for w in windows:
        avg = float(totals[totals["date"] < latest]["total"].tail(w).mean())
        delta = ((today_total - avg) / avg * 100.0) if avg > 0 else 0.0
        out["averages"][f"{w}d"] = {"avg": round(avg, 2), "delta_pct": round(delta, 1)}
    for platform in sorted(svc["platform"].unique()):
        p = svc[svc["platform"] == platform].groupby("date")["cost_usd"].sum()
        p_today = float(p.loc[latest]) if latest in p.index else 0.0
        p_avg7 = float(p[p.index < latest].tail(7).mean())
        out["per_platform"][platform] = {
            "today": round(p_today, 2),
            "avg_7d": round(p_avg7, 2),
            "delta_vs_7d_pct": round((p_today - p_avg7) / p_avg7 * 100.0, 1) if p_avg7 > 0 else 0.0,
        }
    return out


def analyze(df: pd.DataFrame, config: dict) -> dict:
    """Run the full analysis and return a JSON-serializable findings dict."""
    svc = service_daily(df)
    totals = daily_totals(df)
    windows: list[int] = [int(w) for w in config.get("windows", [7, 30])]
    alert_cfg = config.get("alerts", {})
    threshold_pct = float(alert_cfg.get("threshold_pct", 50.0))
    z_thresh = float(alert_cfg.get("z_score", 2.5))
    min_cost = float(alert_cfg.get("min_cost_usd", 10.0))

    findings = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": int(svc["date"].nunique()),
        "services_tracked": int(svc[["platform", "service"]].drop_duplicates().shape[0]),
        "kpis": kpis(svc, totals, windows),
        "week_over_week": period_change(svc, 7),
        "month_over_month": period_change(svc, 30),
        "anomalies": detect_anomalies(svc, threshold_pct, z_thresh, min_cost),
        "top_drivers": top_cost_drivers(svc),
        "config_used": {
            "threshold_pct": threshold_pct,
            "z_score": z_thresh,
            "min_cost_usd": min_cost,
            "windows": windows,
        },
    }
    return findings


def daily_frame_for_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy per-day × platform frame the dashboard plots from."""
    svc = service_daily(df)
    return svc
