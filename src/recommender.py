"""Cost recommendations with projected savings (v2).

Rule-based layer on top of the analyzer: pure arithmetic on the daily spend
series — no ML. Reads the same daily spend frame the analyzer uses and emits
a list of recommendation dicts (written to data/recommendations.json).

Every recommendation has: id, platform, service, type, title, evidence
(numbers as strings), projected_monthly_savings_usd (float), confidence
(high/medium/low), effort (low/medium/high), and rationale (1-2 sentences).

Rules (all respect the alerts.min_cost_usd noise floor):
  idle_resource      — service idle for >= min_idle_fraction of trailing-30d
                       days → tighten auto-suspend / downsize.
  weekend_burn       — dev/test-named service burning material spend on
                       weekends → scheduled pause windows.
  sustained_baseline — flat, high weekday baseline (low CV) → evaluate
                       committed-use / reserved pricing. Skipped for services
                       that already got an idle recommendation (downsize
                       first, commit later).
  model_cost_review  — priciest spiky AI model → candidate for smaller-model
                       routing on non-critical paths (human review required).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analyzer import service_daily

WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 7
WEEKEND_DAYS_PER_MONTH = 8.57  # 2/7 of a 30-day month


def _rec_config(config: dict) -> dict:
    return config.get("recommendations", {}) or {}


def _min_cost(config: dict) -> float:
    return float(config.get("alerts", {}).get("min_cost_usd", 10.0))


def _window(svc: pd.DataFrame) -> pd.DataFrame:
    """Trailing WINDOW_DAYS of per-service daily spend."""
    latest = svc["date"].max()
    return svc[svc["date"] > latest - pd.Timedelta(days=WINDOW_DAYS)].copy()


def _fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def _idle_recommendation(platform: str, service: str, daily: pd.DataFrame,
                         cfg: dict) -> dict | None:
    """Flag services that sit idle for a large share of days."""
    idle_cfg = cfg.get("idle", {})
    idle_day_pct = float(idle_cfg.get("idle_day_pct", 0.05))
    min_idle_fraction = float(idle_cfg.get("min_idle_fraction", 0.25))

    costs = daily["cost_usd"].to_numpy(dtype=float)
    p90 = float(np.percentile(costs, 90))
    idle_line = idle_day_pct * p90
    idle_days = int((costs < idle_line).sum())
    idle_frac = idle_days / len(costs)
    if idle_frac < min_idle_fraction:
        return None

    baseline_daily = float(costs.mean())
    savings = round(baseline_daily * idle_frac * 30, 2)
    return {
        "id": f"idle_resource:{platform}:{service}",
        "platform": platform,
        "service": service,
        "type": "idle_resource",
        "title": (f"{service} sits idle {idle_frac:.0%} of days "
                  f"— tighten auto-suspend / downsize"),
        "evidence": {
            "idle_days": f"{idle_days}/{len(costs)}",
            "idle_fraction": f"{idle_frac:.1%}",
            "idle_definition": f"day cost < {idle_day_pct:.0%} of p90 daily "
                               f"({_fmt_money(idle_line)})",
            "baseline_daily_usd": f"{baseline_daily:,.2f}",
            "savings_math": f"{_fmt_money(baseline_daily)} x {idle_frac:.1%} "
                            f"idle x 30 days",
        },
        "projected_monthly_savings_usd": savings,
        "confidence": "high",
        "effort": "low",
        "rationale": (f"{service} does (near-)nothing on {idle_frac:.0%} of days, "
                      "so you are paying for capacity that never runs. "
                      "Shortening auto-suspend or dropping a size captures "
                      "most of this with no workload change."),
    }


def _weekend_burn_recommendation(platform: str, service: str, daily: pd.DataFrame,
                                 cfg: dict, min_cost: float) -> dict | None:
    """Flag dev/test services burning material spend on weekends."""
    wk_cfg = cfg.get("weekend", {})
    patterns = [p.lower() for p in wk_cfg.get("dev_name_patterns",
                                              ["dev", "test", "sandbox", "staging"])]
    min_weekend_share = float(wk_cfg.get("min_weekend_share", 0.10))
    if not any(pat in service.lower() for pat in patterns):
        return None

    costs = daily["cost_usd"].to_numpy(dtype=float)
    is_weekend = daily["date"].dt.weekday.to_numpy() >= 5
    if is_weekend.sum() == 0 or (~is_weekend).sum() == 0:
        return None
    weekend_daily = float(costs[is_weekend].mean())
    weekend_total = float(costs[is_weekend].sum())
    total = float(costs.sum())
    weekend_share = weekend_total / total if total > 0 else 0.0
    if weekend_share < min_weekend_share or weekend_daily < min_cost:
        return None

    savings = round(weekend_daily * WEEKEND_DAYS_PER_MONTH, 2)
    return {
        "id": f"weekend_burn:{platform}:{service}",
        "platform": platform,
        "service": service,
        "type": "weekend_burn",
        "title": (f"{service} burns {_fmt_money(weekend_daily)}/day on weekends "
                  f"— add scheduled pause windows"),
        "evidence": {
            "weekend_daily_avg_usd": f"{weekend_daily:,.2f}",
            "weekend_share_of_spend": f"{weekend_share:.1%}",
            "assumption": f"pausing every weekend saves ~{WEEKEND_DAYS_PER_MONTH} "
                          "weekend-days/month",
            "savings_math": f"{_fmt_money(weekend_daily)} x "
                            f"{WEEKEND_DAYS_PER_MONTH} weekend-days/mo",
        },
        "projected_monthly_savings_usd": savings,
        "confidence": "high",
        "effort": "low",
        "rationale": (f"Weekend spend is {weekend_share:.0%} of {service}'s total — "
                      "unusual for a dev/test resource nobody queries on "
                      "Saturdays. Scheduled suspend/resume windows remove it "
                      "almost entirely."),
    }


def _commitment_recommendation(platform: str, service: str, daily: pd.DataFrame,
                               cfg: dict) -> dict | None:
    """Flag flat, high weekday baselines as committed-use candidates."""
    c_cfg = cfg.get("commitment", {})
    max_cv = float(c_cfg.get("max_cv", 0.25))
    min_monthly = float(c_cfg.get("min_monthly_usd", 2000.0))
    discount = float(c_cfg.get("discount_assumption", 0.15))

    weekday = daily[daily["date"].dt.weekday < 5]
    costs = weekday["cost_usd"].to_numpy(dtype=float)
    mean = float(costs.mean())
    if mean <= 0:
        return None
    cv = float(costs.std() / mean)
    monthly = mean * 30
    if cv > max_cv or monthly < min_monthly:
        return None

    savings = round(monthly * discount, 2)
    return {
        "id": f"sustained_baseline:{platform}:{service}",
        "platform": platform,
        "service": service,
        "type": "sustained_baseline",
        "title": (f"{service} holds a steady {_fmt_money(mean)}/day baseline "
                  f"— evaluate committed-use / reserved pricing"),
        "evidence": {
            "weekday_daily_avg_usd": f"{mean:,.2f}",
            "weekday_cv": f"{cv:.2f}",
            "est_monthly_usd": f"{monthly:,.2f}",
            "assumption": f"{discount:.0%} commitment discount (assumption — "
                          "validate against your actual contract)",
            "savings_math": f"{_fmt_money(monthly)} x {discount:.0%} discount",
        },
        "projected_monthly_savings_usd": savings,
        "confidence": "medium",
        "effort": "low",
        "rationale": (f"{service}'s weekday spend barely moves (CV {cv:.2f}), so "
                      "the baseline is predictable enough to pre-commit. "
                      "Downsize or eliminate idle capacity first — commitments "
                      "lock in whatever you buy."),
    }


def _model_review_recommendation(candidates: list[dict], cfg: dict) -> dict | None:
    """Flag the priciest spiky AI model for smaller-model routing review."""
    if not candidates:
        return None
    m_cfg = cfg.get("model_review", {})
    routable = float(m_cfg.get("routable_fraction_assumption", 0.30))
    best = max(candidates, key=lambda c: c["monthly"])
    savings = round(best["monthly"] * routable, 2)
    return {
        "id": f"model_cost_review:{best['platform']}:{best['service']}",
        "platform": best["platform"],
        "service": best["service"],
        "type": "model_cost_review",
        "title": (f"{best['service']} is pricey and spiky — review smaller-model "
                  "routing for non-critical paths"),
        "evidence": {
            "est_monthly_usd": f"{best['monthly']:,.2f}",
            "cv": f"{best['cv']:.2f}",
            "assumption": f"{routable:.0%} of traffic routable to a smaller "
                          "model (assumption — measure quality impact first)",
            "savings_math": f"{_fmt_money(best['monthly'])} x {routable:.0%} "
                            "routable traffic",
        },
        "projected_monthly_savings_usd": savings,
        "confidence": "medium",
        "effort": "medium",
        "rationale": (f"{best['service']} is the costliest AI model and its usage "
                      f"is spiky (CV {best['cv']:.2f}), suggesting bursty "
                      "non-critical workloads. Route evals, drafts, or retries "
                      "to a smaller model — but quality trade-offs need human "
                      "review before acting."),
    }


def recommend(df: pd.DataFrame, config: dict) -> list[dict]:
    """Generate cost recommendations with projected monthly savings.

    Returns a list of recommendation dicts sorted by projected savings
    (descending). Returns [] when disabled, when input is empty, or when no
    rule fires.
    """
    cfg = _rec_config(config)
    if not cfg.get("enabled", True):
        return []
    if df is None or df.empty:
        return []

    min_cost = _min_cost(config)
    svc = service_daily(df)
    window = _window(svc)
    if window.empty:
        return []

    m_cfg = cfg.get("model_review", {})
    model_min_monthly = float(m_cfg.get("min_monthly_usd", 500.0))
    model_min_cv = float(m_cfg.get("min_cv", 0.40))

    recs: list[dict] = []
    idle_services: set[tuple[str, str]] = set()
    paused_services: set[tuple[str, str]] = set()
    model_candidates: list[dict] = []

    for (platform, service), grp in window.groupby(["platform", "service"]):
        grp = grp.sort_values("date").reset_index(drop=True)
        if len(grp) < MIN_HISTORY_DAYS:
            continue
        daily_mean = float(grp["cost_usd"].mean())
        if daily_mean < min_cost:  # noise floor — not worth recommending on
            continue

        idle_rec = _idle_recommendation(platform, service, grp, cfg)
        if idle_rec:
            recs.append(idle_rec)
            idle_services.add((platform, service))

        weekend_rec = _weekend_burn_recommendation(platform, service, grp, cfg,
                                                  min_cost)
        if weekend_rec:
            recs.append(weekend_rec)
            paused_services.add((platform, service))

        # Commitments lock in capacity — skip services we're telling you to
        # downsize or pause first.
        if ((platform, service) not in idle_services
                and (platform, service) not in paused_services):
            commit_rec = _commitment_recommendation(platform, service, grp, cfg)
            if commit_rec:
                recs.append(commit_rec)

        if platform == "ai":
            costs = grp["cost_usd"].to_numpy(dtype=float)
            mean = float(costs.mean())
            cv = float(costs.std() / mean) if mean > 0 else 0.0
            monthly = mean * 30
            if monthly >= model_min_monthly and cv >= model_min_cv:
                model_candidates.append({
                    "platform": platform, "service": service,
                    "monthly": monthly, "cv": cv,
                })

    model_rec = _model_review_recommendation(model_candidates, cfg)
    if model_rec:
        recs.append(model_rec)

    recs.sort(key=lambda r: r["projected_monthly_savings_usd"], reverse=True)
    return recs


def total_projected_savings(recs: list[dict]) -> float:
    """Sum of projected monthly savings across recommendations."""
    return round(sum(float(r["projected_monthly_savings_usd"]) for r in recs), 2)
