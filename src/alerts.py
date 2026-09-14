"""Alerting engine: rules over findings → console summary, Markdown report,
and an optional Slack webhook post.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    severity: str            # "critical" | "warning" | "info"
    title: str
    detail: str
    platform: str = ""
    service: str = ""
    meta: dict = field(default_factory=dict)


def evaluate_findings(findings: dict, config: dict) -> list[Alert]:
    """Turn analyzer findings into a ranked list of alerts."""
    alerts: list[Alert] = []
    kpis = findings.get("kpis", {})

    for a in findings.get("anomalies", []):
        alerts.append(Alert(
            severity=a["severity"],
            title=f"{a['service']} spend spike: ${a['cost_usd']:,.0f} "
                  f"(+{a['pct_over_avg']:.0f}% vs 30d avg)",
            detail=(f"{a['platform']} · {a['service']} cost ${a['cost_usd']:,.2f} on "
                    f"{a['date']}, vs a trailing-30d average of ${a['baseline_30d_avg']:,.2f} "
                    f"(+{a['pct_over_avg']:.1f}%, z={a['z_score']})."),
            platform=a["platform"], service=a["service"], meta=a,
        ))

    for w, label in (("week_over_week", "week"), ("month_over_month", "month")):
        period = findings.get(w, {})
        if abs(period.get("delta_pct", 0)) >= 20:
            direction = "up" if period["delta_pct"] > 0 else "down"
            alerts.append(Alert(
                severity="warning" if abs(period["delta_pct"]) < 40 else "critical",
                title=f"Total spend {direction} {abs(period['delta_pct']):.0f}% vs last {label}",
                detail=(f"${period['current']:,.0f} this {label} vs "
                        f"${period['previous']:,.0f} last {label}."),
                meta=period,
            ))

    for plat, vals in kpis.get("per_platform", {}).items():
        if vals.get("delta_vs_7d_pct", 0) >= 75:
            alerts.append(Alert(
                severity="warning",
                title=f"{plat} up {vals['delta_vs_7d_pct']:.0f}% vs 7d average today",
                detail=(f"{plat} at ${vals['today']:,.0f} today vs "
                        f"${vals['avg_7d']:,.0f} 7d average."),
                platform=plat,
            ))

    if not alerts:
        alerts.append(Alert(
            severity="info",
            title="No cost anomalies detected",
            detail="All services are within thresholds vs their 7d/30d baselines.",
        ))

    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: rank.get(a.severity, 3))


def render_markdown(alerts: list[Alert], findings: dict,
                    source_notes: list[str],
                    recommendations: list[dict] | None = None) -> str:
    """Render the full report as Markdown."""
    k = findings["kpis"]
    lines = [
        "# 💰 Data Cost Guardian — Spend Report",
        "",
        f"_Generated {findings['generated_at']} · {findings['window_days']} days · "
        f"{findings['services_tracked']} services tracked_",
        "",
        "## Key metrics",
        "",
        f"- **Today:** ${k['today_total']:,.2f}",
    ]
    for w, vals in k["averages"].items():
        arrow = "🔺" if vals["delta_pct"] > 0 else "🔻"
        lines.append(f"- **vs {w} avg:** ${vals['avg']:,.2f} "
                     f"({arrow} {vals['delta_pct']:+.1f}%)")
    wow, mom = findings["week_over_week"], findings["month_over_month"]
    lines += [
        f"- **Week over week:** {wow['delta_pct']:+.1f}% "
        f"(${wow['current']:,.0f} vs ${wow['previous']:,.0f})",
        f"- **Month over month:** {mom['delta_pct']:+.1f}% "
        f"(${mom['current']:,.0f} vs ${mom['previous']:,.0f})",
        "",
        "## Alerts",
        "",
    ]
    for a in alerts:
        icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(a.severity, "•")
        lines += [f"### {icon} [{a.severity.upper()}] {a.title}", "", a.detail, ""]
    lines += ["## 💡 Recommendations — projected savings", ""]
    recs = sorted(recommendations or [],
                  key=lambda r: r["projected_monthly_savings_usd"], reverse=True)[:5]
    if recs:
        total = sum(r["projected_monthly_savings_usd"] for r in recs)
        lines.append(f"_Total projected monthly savings (top {len(recs)}): "
                     f"**${total:,.0f}**_")
        lines.append("")
        for r in recs:
            lines += [
                f"### 💰 Save ~${r['projected_monthly_savings_usd']:,.0f}/mo — {r['title']}",
                "",
                f"- **Service:** {r['platform']} · {r['service']} · "
                f"**Confidence:** {r['confidence']} · **Effort:** {r['effort']}",
                f"- **Evidence:** " + "; ".join(
                    f"{k2}={v2}" for k2, v2 in r["evidence"].items()),
                f"- **Why:** {r['rationale']}",
                "",
            ]
    else:
        lines.append("_No cost-saving recommendations this run._")
        lines.append("")
    lines += ["## Top cost drivers (trailing 30d)", "",
              "| Service | Platform | 30d cost | Share | Δ vs prev 30d |",
              "|---|---|---|---|---|"]
    for d in findings["top_drivers"]:
        lines.append(f"| {d['service']} | {d['platform']} | ${d['cost_usd']:,.0f} | "
                     f"{d['share_pct']}% | {d['delta_vs_prev_pct']:+.1f}% |")
    lines += ["", "## Data sources", ""]
    lines += [f"- {n}" for n in source_notes]
    lines += ["", "---",
              "_Data Cost Guardian · demo mode uses synthetic data unless live "
              "connectors are configured._"]
    return "\n".join(lines) + "\n"


def write_alerts_md(alerts: list[Alert], findings: dict,
                    source_notes: list[str], path: str | Path,
                    recommendations: list[dict] | None = None) -> Path:
    """Write the Markdown report; return the path written."""
    path = Path(path)
    path.write_text(render_markdown(alerts, findings, source_notes, recommendations))
    logger.info("Wrote report to %s", path)
    return path


def print_console_summary(alerts: list[Alert], findings: dict,
                          recommendations: list[dict] | None = None) -> None:
    """Human-friendly console output."""
    k = findings["kpis"]
    print("\n==== Data Cost Guardian ====")
    print(f"Today: ${k['today_total']:,.2f}", end="")
    for w, vals in k["averages"].items():
        print(f"  |  vs {w} avg ${vals['avg']:,.2f} ({vals['delta_pct']:+.1f}%)", end="")
    print(f"\nAnomalies: {len(findings['anomalies'])}  |  "
          f"WoW: {findings['week_over_week']['delta_pct']:+.1f}%  |  "
          f"MoM: {findings['month_over_month']['delta_pct']:+.1f}%")
    print("\n-- Alerts --")
    for a in alerts:
        print(f"[{a.severity.upper():8}] {a.title}")
    recs = sorted(recommendations or [],
                  key=lambda r: r["projected_monthly_savings_usd"], reverse=True)[:5]
    if recs:
        print("\n-- Recommendations (projected monthly savings) --")
        for r in recs:
            print(f"[~${r['projected_monthly_savings_usd']:,.0f}/mo] "
                  f"{r['title']} (confidence: {r['confidence']})")
        total = sum(r["projected_monthly_savings_usd"] for r in recs)
        print(f"Total projected monthly savings: ~${total:,.0f}")
    print()


def build_slack_payload(alerts: list[Alert], findings: dict) -> dict:
    """Build a Slack incoming-webhook payload (Block Kit)."""
    k = findings["kpis"]
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "💰 Data Cost Guardian — Daily Spend Report"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f"*Today:* ${k['today_total']:,.2f}\n" +
                           "\n".join(
                               f"*vs {w} avg:* ${v['avg']:,.2f} ({v['delta_pct']:+.1f}%)"
                               for w, v in k["averages"].items()))}},
        {"type": "divider"},
    ]
    for a in alerts[:10]:  # keep the payload small
        icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(a.severity, "•")
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"{icon} *[{a.severity.upper()}] {a.title}*\n{a.detail}"}})
    return {"text": f"Data Cost Guardian: {len(alerts)} alert(s), "
                    f"today ${k['today_total']:,.2f}",
            "blocks": blocks}


def send_slack_alerts(alerts: list[Alert], findings: dict) -> bool:
    """POST the payload if SLACK_WEBHOOK_URL is set; otherwise print it.

    Returns True when a real POST was attempted.
    """
    payload = build_slack_payload(alerts, findings)
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        print("SLACK_WEBHOOK_URL not set — Slack payload (not sent):")
        print(json.dumps(payload, indent=2)[:2000])
        return False
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Slack alert posted (status %s).", resp.status_code)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Slack POST failed (%s); payload was:\n%s", exc,
                       json.dumps(payload)[:1000])
        return False
