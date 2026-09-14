# 💰 Data Cost Guardian — Spend Report

_Generated 2026-09-11T00:05:55.003022+00:00 · 90 days · 9 services tracked_

## Key metrics

- **Today:** $904.68
- **vs 7d avg:** $768.28 (🔺 +17.8%)
- **vs 30d avg:** $774.38 (🔺 +16.8%)
- **Week over week:** -6.3% ($5,314 vs $5,670)
- **Month over month:** +3.5% ($23,255 vs $22,467)

## Alerts

### 🚨 [CRITICAL] INGEST_WH spend spike: $140 (+126% vs 30d avg)

snowflake · INGEST_WH cost $140.49 on 2026-09-06, vs a trailing-30d average of $62.07 (+126.3%, z=4.2).

### 🚨 [CRITICAL] ml-cluster spend spike: $531 (+265% vs 30d avg)

databricks · ml-cluster cost $531.12 on 2026-08-28, vs a trailing-30d average of $145.40 (+265.3%, z=9.33).

### 🚨 [CRITICAL] gpt-4o spend spike: $174 (+549% vs 30d avg)

ai · gpt-4o cost $174.02 on 2026-08-20, vs a trailing-30d average of $26.82 (+548.8%, z=19.47).

## 💡 Recommendations — projected savings

_Total projected monthly savings (top 5): **$2,795**_

### 💰 Save ~$1,002/mo — COMPUTE_WH holds a steady $222.64/day baseline — evaluate committed-use / reserved pricing

- **Service:** snowflake · COMPUTE_WH · **Confidence:** medium · **Effort:** low
- **Evidence:** weekday_daily_avg_usd=222.64; weekday_cv=0.07; est_monthly_usd=6,679.21; assumption=15% commitment discount (assumption — validate against your actual contract); savings_math=$6,679.21 x 15% discount
- **Why:** COMPUTE_WH's weekday spend barely moves (CV 0.07), so the baseline is predictable enough to pre-commit. Downsize or eliminate idle capacity first — commitments lock in whatever you buy.

### 💰 Save ~$631/mo — ANALYTICS_WH holds a steady $140.25/day baseline — evaluate committed-use / reserved pricing

- **Service:** snowflake · ANALYTICS_WH · **Confidence:** medium · **Effort:** low
- **Evidence:** weekday_daily_avg_usd=140.25; weekday_cv=0.07; est_monthly_usd=4,207.57; assumption=15% commitment discount (assumption — validate against your actual contract); savings_math=$4,207.57 x 15% discount
- **Why:** ANALYTICS_WH's weekday spend barely moves (CV 0.07), so the baseline is predictable enough to pre-commit. Downsize or eliminate idle capacity first — commitments lock in whatever you buy.

### 💰 Save ~$509/mo — etl-cluster holds a steady $113.01/day baseline — evaluate committed-use / reserved pricing

- **Service:** databricks · etl-cluster · **Confidence:** medium · **Effort:** low
- **Evidence:** weekday_daily_avg_usd=113.01; weekday_cv=0.08; est_monthly_usd=3,390.29; assumption=15% commitment discount (assumption — validate against your actual contract); savings_math=$3,390.29 x 15% discount
- **Why:** etl-cluster's weekday spend barely moves (CV 0.08), so the baseline is predictable enough to pre-commit. Downsize or eliminate idle capacity first — commitments lock in whatever you buy.

### 💰 Save ~$331/mo — INGEST_WH holds a steady $73.45/day baseline — evaluate committed-use / reserved pricing

- **Service:** snowflake · INGEST_WH · **Confidence:** medium · **Effort:** low
- **Evidence:** weekday_daily_avg_usd=73.45; weekday_cv=0.05; est_monthly_usd=2,203.50; assumption=15% commitment discount (assumption — validate against your actual contract); savings_math=$2,203.50 x 15% discount
- **Why:** INGEST_WH's weekday spend barely moves (CV 0.05), so the baseline is predictable enough to pre-commit. Downsize or eliminate idle capacity first — commitments lock in whatever you buy.

### 💰 Save ~$323/mo — DEV_WH burns $37.64/day on weekends — add scheduled pause windows

- **Service:** snowflake · DEV_WH · **Confidence:** high · **Effort:** low
- **Evidence:** weekend_daily_avg_usd=37.64; weekend_share_of_spend=11.4%; assumption=pausing every weekend saves ~8.57 weekend-days/month; savings_math=$37.64 x 8.57 weekend-days/mo
- **Why:** Weekend spend is 11% of DEV_WH's total — unusual for a dev/test resource nobody queries on Saturdays. Scheduled suspend/resume windows remove it almost entirely.

## Top cost drivers (trailing 30d)

| Service | Platform | 30d cost | Share | Δ vs prev 30d |
|---|---|---|---|---|
| COMPUTE_WH | snowflake | $5,701 | 24.5% | +0.6% |
| ml-cluster | databricks | $4,706 | 20.2% | +10.9% |
| ANALYTICS_WH | snowflake | $3,631 | 15.6% | +0.7% |
| etl-cluster | databricks | $2,902 | 12.5% | +0.4% |
| DEV_WH | snowflake | $2,638 | 11.3% | +0.7% |

## Data sources

- all platforms: demo (synthetic, seed=42, 90 days)

---
_Data Cost Guardian · demo mode uses synthetic data unless live connectors are configured._
