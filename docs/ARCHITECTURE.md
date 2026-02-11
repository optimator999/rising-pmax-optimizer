# Architecture

## System Overview

```
EventBridge (Monday 6am MT)
    │
    ▼
Lambda: weekly_review
    │
    ├── Google Ads API → Collect asset data
    ├── DynamoDB → Save performance records
    ├── Analyzer → Flag underperformers
    ├── Claude API → Generate replacements
    ├── CSV Builder → Google Ads Editor format
    └── Slack API → Send DM with CSV attachment

EventBridge (Thursday 6am MT)
    │
    ▼
Lambda: verify_upload
    │
    ├── DynamoDB → Load last week's recommendations
    ├── Google Ads API → Query current live data
    ├── Compare → Verify uploads happened
    ├── DynamoDB → Update verification status
    └── Slack API → Send verification report
```

## Data Flow

1. **Collection:** Google Ads API returns daily per-asset metrics for the lookback period
2. **Aggregation:** Daily rows are summed into per-asset totals (impressions, clicks, cost, conversions)
3. **Storage:** Aggregated records saved to DynamoDB with `asset_id + report_date` as composite key
4. **Analysis:** Each asset checked against seasonal CTR thresholds and cost-per-zero-conversion limits
5. **Generation:** Flagged assets sent to Claude API with graveyard context for voice-consistent replacements
6. **Delivery:** Slack DM with formatted summary + CSV attachment per campaign
7. **Verification:** Thursday check compares recommendations against live Google Ads data

## DynamoDB Tables

### `rising_asset_performance`
- **Key:** `asset_id` (HASH) + `report_date` (RANGE)
- **GSI:** `campaign-status-index` (campaign_name HASH + status RANGE)
- **Purpose:** Time-series asset performance tracking

### `rising_asset_graveyard`
- **Key:** `campaign_name` (HASH) + `date_killed` (RANGE)
- **Purpose:** Store killed assets for Claude API learning context

### `rising_budget_performance`
- **Key:** `campaign_name` (HASH) + `week_ending` (RANGE)
- **Purpose:** Weekly budget metrics and recommendations

## Security

- All secrets stored in AWS Parameter Store as SecureString (KMS encrypted)
- Lambda IAM role follows least privilege principle
- No credentials in code, environment variables, or logs
- Parameter Store paths:
  - `/Google_Ads/*` (5 parameters)
  - `/Slack/*` (2 parameters)
  - `/Anthropic/*` (1 parameter)

## Error Handling

- All external API calls retry 3 times with exponential backoff
- If Claude API fails, analysis still sent without replacements (graceful degradation)
- All errors reported to Slack DM (never fail silently)
- Lambda timeout: 5 min (weekly review), 3 min (verification)

## Budget Monitoring

The system tracks ROAS performance weekly and recommends budget changes:

- **INCREASE:** ROAS 10%+ above target → +20% budget
- **HOLD:** ROAS within +/-10% of target → maintain budget
- **DECREASE:** ROAS 10-30% below target → -20% budget
- **PAUSE:** ROAS >30% below target → reduce to $10/day maintenance
- **MARKET CEILING:** Budget utilization <80% at >$100/day → stop scaling

Emergency alerts fire for: zero conversion streaks, CTR collapse, budget runaway, and market ceiling detection.
