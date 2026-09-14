"""CLI entrypoint: connectors → analyzer → alerts.

Demo (no credentials needed):
    python -m src.main --demo --report

With live connectors (reads .env, falls back per-platform to demo data):
    python -m src.main --report

Then launch the dashboard:
    streamlit run src/dashboard.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow `python -m src.main` and `python src/main.py`

from src.alerts import (  # noqa: E402
    evaluate_findings,
    print_console_summary,
    send_slack_alerts,
    write_alerts_md,
)
from src.analyzer import analyze  # noqa: E402
from src.connectors import fetch_all  # noqa: E402
from src.demo_data import generate_demo_data  # noqa: E402
from src.recommender import recommend  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("data-cost-guardian")


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Data Cost Guardian — AI FinOps agent")
    p.add_argument("--demo", action="store_true",
                   help="use synthetic demo data (default when no credentials found)")
    p.add_argument("--report", action="store_true",
                   help="write alerts.md Markdown report")
    p.add_argument("--days", type=int, default=None,
                   help="override demo/connector window (days)")
    p.add_argument("--threshold", type=float, default=None,
                   help="override alert threshold percent (e.g. 50)")
    p.add_argument("--no-recommendations", action="store_true",
                   help="skip the v2 recommendations layer")
    p.add_argument("--config", default=str(ROOT / "config.yaml"),
                   help="path to config.yaml")
    return p.parse_args(argv)


def has_any_credentials() -> bool:
    keys = ("SNOWFLAKE_ACCOUNT", "DATABRICKS_HOST", "OPENAI_API_KEY")
    return any(__import__("os").getenv(k) for k in keys)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    config = load_config(Path(args.config))

    if args.days:
        config.setdefault("demo", {})["days"] = args.days
    if args.threshold is not None:
        config.setdefault("alerts", {})["threshold_pct"] = args.threshold
        import os
        os.environ["ALERT_THRESHOLD_PCT"] = str(args.threshold)
    elif __import__("os").getenv("ALERT_THRESHOLD_PCT"):
        config.setdefault("alerts", {})["threshold_pct"] = float(
            __import__("os").getenv("ALERT_THRESHOLD_PCT"))

    days = int(config.get("demo", {}).get("days", 90))
    seed = int(config.get("demo", {}).get("seed", 42))

    demo_df = generate_demo_data(days=days, seed=seed)
    use_demo = args.demo or not has_any_credentials()

    if use_demo:
        df = demo_df
        source_notes = ["all platforms: demo (synthetic, seed=%d, %d days)" % (seed, days)]
        logger.info("Demo mode: synthetic data for %d days.", days)
    else:
        df, source_notes = fetch_all(config, demo_df)
        for n in source_notes:
            logger.info("Source — %s", n)

    findings = analyze(df, config)

    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    spend_csv = data_dir / "daily_spend.csv"
    findings_json = data_dir / "findings.json"
    recommendations_json = data_dir / "recommendations.json"
    df.to_csv(spend_csv, index=False)
    findings_json.write_text(json.dumps(findings, indent=2))
    logger.info("Wrote %s (%d rows) and %s.", spend_csv, len(df), findings_json)

    rec_enabled = config.get("recommendations", {}).get("enabled", True)
    if rec_enabled and not args.no_recommendations:
        recommendations = recommend(df, config)
    else:
        recommendations = []
        logger.info("Recommendations layer skipped.")
    recommendations_json.write_text(json.dumps(recommendations, indent=2))
    logger.info("Wrote %s (%d recommendations).",
                recommendations_json, len(recommendations))

    alerts = evaluate_findings(findings, config)
    print_console_summary(alerts, findings, recommendations)

    if args.report:
        md_path = write_alerts_md(alerts, findings, source_notes,
                                   ROOT / "alerts.md", recommendations)
        print(f"Report written to {md_path}")

    send_slack_alerts(alerts, findings)

    print("Next step → launch the dashboard:")
    print("    streamlit run src/dashboard.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
