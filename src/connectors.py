"""Cost connectors for Snowflake, Databricks, and AI APIs.

Each connector tries the real API first and falls back to ``None`` (the caller
substitutes demo data) with a clear log message — so the repo always runs with
zero credentials. The real SQL is kept as module constants to document exactly
what would run in production.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Snowflake: real production queries ───────────────────────────────────────
SNOWFLAKE_METERING_SQL = """
SELECT
    DATE(START_TIME)      AS day,
    WAREHOUSE_NAME        AS warehouse,
    SUM(CREDITS_USED)     AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE START_TIME >= DATEADD(day, -{days}, CURRENT_DATE())
GROUP BY 1, 2
ORDER BY 1, 2
"""

SNOWFLAKE_QUERY_HISTORY_SQL = """
SELECT
    DATE(START_TIME)      AS day,
    USER_NAME             AS user_name,
    WAREHOUSE_NAME        AS warehouse,
    COUNT(*)              AS query_count,
    SUM(CREDITS_USED_CLOUD_SERVICES) AS cs_credits
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE START_TIME >= DATEADD(day, -{days}, CURRENT_DATE())
GROUP BY 1, 2, 3
ORDER BY cs_credits DESC
LIMIT 500
"""

# ── Databricks: system.billing.usage ─────────────────────────────────────────
DATABRICKS_USAGE_SQL = """
SELECT
    DATE(usage_date)                                   AS day,
    COALESCE(cluster_id, 'serverless')                 AS cluster_name,
    sku_name,
    SUM(usage_quantity * list_price)                   AS cost_usd
FROM system.billing.usage
WHERE usage_date >= CURRENT_DATE() - INTERVAL {days} DAYS
GROUP BY 1, 2, 3
ORDER BY 1, 2
"""


class SnowflakeConnector:
    """Warehouse credit metering + query history from ACCOUNT_USAGE."""

    def __init__(self, credit_price_usd: float = 3.0) -> None:
        self.credit_price_usd = credit_price_usd

    def _connect(self):  # -> snowflake.connector connection
        try:
            import snowflake.connector
        except ImportError as exc:
            raise RuntimeError("snowflake-connector-python is not installed") from exc
        missing = [k for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
                   if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"missing env vars: {', '.join(missing)}")
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
            role=os.getenv("SNOWFLAKE_ROLE"),
        )

    def fetch(self, days: int = 90) -> pd.DataFrame | None:
        """Return daily cost rows or None (caller falls back to demo data)."""
        try:
            conn = self._connect()
        except RuntimeError as exc:
            logger.warning("Snowflake: %s — using demo data instead.", exc)
            return None
        try:
            cur = conn.cursor()
            cur.execute(SNOWFLAKE_METERING_SQL.format(days=days))
            rows = cur.fetchall()
            cols = [c[0].lower() for c in cur.description]
            df = pd.DataFrame(rows, columns=cols)
            df["platform"] = "snowflake"
            df["service"] = df["warehouse"]
            df["cost_usd"] = df["credits"] * self.credit_price_usd
            df["date"] = pd.to_datetime(df["day"])
            logger.info("Snowflake: fetched %d warehouse-day rows.", len(df))
            return df[["date", "platform", "service", "cost_usd"]]
        except Exception as exc:  # noqa: BLE001 — connector failure must not kill the run
            logger.warning("Snowflake query failed (%s) — using demo data instead.", exc)
            return None
        finally:
            conn.close()


class DatabricksConnector:
    """DBU spend from system.billing.usage via the Databricks SDK."""

    def fetch(self, days: int = 90) -> pd.DataFrame | None:
        try:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service import sql as sql_service
        except ImportError:
            logger.warning("Databricks: databricks-sdk not installed — using demo data instead.")
            return None
        if not (os.getenv("DATABRICKS_HOST") and os.getenv("DATABRICKS_TOKEN")):
            logger.warning("Databricks: DATABRICKS_HOST/TOKEN not set — using demo data instead.")
            return None
        try:
            w = WorkspaceClient()
            # A SQL warehouse id is required; reuse one if provided, else list and pick.
            warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
            if not warehouse_id:
                warehouses = list(w.warehouses.list())
                warehouse_id = warehouses[0].id if warehouses else None
            if not warehouse_id:
                raise RuntimeError("no SQL warehouse available")
            stmt = w.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=DATABRICKS_USAGE_SQL.format(days=days),
                wait_timeout="30s",
            )
            result = w.statement_execution.get_statement(stmt.statement_id)
            rows = [
                (r[0], r[1], r[2], float(r[3]))
                for chunk in (result.result.data_array or [] for _ in [0])
                for r in chunk
            ]
            df = pd.DataFrame(rows, columns=["day", "cluster_name", "sku_name", "cost_usd"])
            df["platform"] = "databricks"
            df["service"] = df["cluster_name"]
            df["date"] = pd.to_datetime(df["day"])
            logger.info("Databricks: fetched %d cluster-day rows.", len(df))
            return df[["date", "platform", "service", "cost_usd"]]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Databricks query failed (%s) — using demo data instead.", exc)
            return None


class AICostConnector:
    """LLM/API spend: OpenAI usage stub + CSV import.

    OpenAI has no public cost API, so the real path here is a stub that
    documents the intended call; CSV import is the supported real path.
    Expected CSV columns: date, model, tokens_in, tokens_out, cost
    """

    CSV_COLUMNS = ["date", "model", "tokens_in", "tokens_out", "cost"]

    def fetch_openai(self, days: int = 90) -> pd.DataFrame | None:
        import requests  # noqa: F401  (documents the intended dependency)

        logger.warning(
            "AI cost: OpenAI exposes no public cost API — "
            "export usage to CSV and point ai.csv_path at it (see README). "
            "Using demo data instead."
        )
        # Intended real call (kept as documentation, not executed):
        #   GET https://api.openai.com/v1/organization/usage/completions
        #   headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
        #   params={"start_time": ..., "bucket_width": "1d"}
        return None

    def load_csv(self, path: str | Path) -> pd.DataFrame | None:
        path = Path(path)
        if not path.exists():
            logger.info("AI cost: no CSV at %s — using demo data instead.", path)
            return None
        try:
            df = pd.read_csv(path)
            missing = [c for c in self.CSV_COLUMNS if c not in df.columns]
            if missing:
                logger.warning("AI cost CSV missing columns %s — using demo data.", missing)
                return None
            df["platform"] = "ai"
            df["service"] = df["model"]
            df["cost_usd"] = df["cost"].astype(float)
            df["date"] = pd.to_datetime(df["date"])
            logger.info("AI cost: loaded %d rows from %s.", len(df), path)
            return df[["date", "platform", "service", "cost_usd"]]
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI cost CSV unreadable (%s) — using demo data.", exc)
            return None


def fetch_all(config: dict, demo_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Fetch from every enabled platform; fill gaps with demo data.

    Returns (combined DataFrame, list of human-readable source notes).
    """
    frames: list[pd.DataFrame] = []
    notes: list[str] = []
    days = int(config.get("demo", {}).get("days", 90))

    plats = config.get("platforms", {})

    def _use(frame: pd.DataFrame | None, label: str, source: str) -> None:
        if frame is not None and not frame.empty:
            frames.append(frame)
            notes.append(f"{label}: live ({source})")
        else:
            part = demo_df[demo_df["platform"] == label].copy()
            frames.append(part)
            notes.append(f"{label}: demo fallback")

    if plats.get("snowflake", {}).get("enabled", True):
        price = float(plats.get("snowflake", {}).get("credit_price_usd", 3.0))
        _use(SnowflakeConnector(credit_price_usd=price).fetch(days), "snowflake",
             "ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY")
    if plats.get("databricks", {}).get("enabled", True):
        _use(DatabricksConnector().fetch(days), "databricks", "system.billing.usage")
    if plats.get("ai", {}).get("enabled", True):
        ai = AICostConnector()
        csv_path = plats.get("ai", {}).get("csv_path", "data/ai_costs.csv")
        live = ai.load_csv(csv_path)
        if live is None:
            live = ai.fetch_openai(days)
        _use(live, "ai", f"csv:{csv_path}")

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    return combined.sort_values(["date", "platform", "service"]).reset_index(drop=True), notes
