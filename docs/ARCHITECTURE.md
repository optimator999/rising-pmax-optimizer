# Architecture

## System Overview

```
EventBridge (Monday 6am MT)
    │
    ▼
Lambda: weekly_review
    │
    ├── Google Ads API → Collect text + image asset data
    ├── Shopify GraphQL → Get attributed revenue
    ├── DynamoDB → Save performance records
    ├── Analyzer → Flag underperformers (CTR-only, active seasons)
    ├── Claude API → Generate text replacements (active seasons)
    ├── CSV Builder → Google Ads Editor format (text assets, active seasons)
    └── Slack API → Send review with text + image performance sections

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

Manual / On-demand
    │
    ▼
Lambda: image_ops
    │
    ├── bootstrap → Google Ads API → Download images → Claude Vision → S3 + Registry
    ├── upload → S3 → Claude Vision → Registry
    ├── gap_analysis → Registry → Campaign profile comparison → Recommendations
    └── analyze → S3 → Claude Vision → Registry (re-analyze existing image)
```

## Data Flow

### Text Assets (Weekly)
1. **Collection:** Google Ads API returns daily per-asset metrics for the lookback period
2. **Aggregation:** Daily rows are summed into per-asset totals (impressions, clicks, cost, conversions)
3. **Storage:** Aggregated records saved to DynamoDB with `asset_id + report_date` as composite key
4. **Analysis:** Each asset checked against seasonal CTR thresholds (CTR-only; conversion data is unreliable at the asset level in PMax)
5. **Generation:** Flagged text assets sent to Claude API with graveyard context for voice-consistent replacements
6. **Delivery:** Slack DM with formatted summary + CSV attachment per campaign
7. **Verification:** Thursday check compares recommendations against live Google Ads data

### Image Assets (Weekly)
1. **Collection:** Google Ads API returns daily per-image metrics (MARKETING_IMAGE, SQUARE_MARKETING_IMAGE, PORTRAIT_MARKETING_IMAGE)
2. **Aggregation:** Daily rows summed by `asset_resource` (stable ID) into per-image totals
3. **Storage:** Aggregated records saved to `rising_asset_performance` (same table as text)
4. **Analysis:** Each image checked against 1.0% CTR threshold (all seasons)
5. **No generation:** Flagged images reported with manual action note. No Claude replacement, no CSV
6. **Delivery:** Separate image performance section in Slack per campaign

### Image Registry (On-demand)
1. **Bootstrap:** Pull existing images from Google Ads API, download, upload to S3, analyze with Claude Vision, register in DynamoDB with Google Ads mapping
2. **Upload:** New image uploaded to S3, analyzed with Claude Vision, registered with AI metadata
3. **Gap Analysis:** Compare campaign's current image composition against ideal profile, recommend uploads
4. **Performance Writeback:** Weekly review writes CTR/impressions back to registry entries

## DynamoDB Tables

### `rising_asset_performance`
- **Key:** `asset_id` (HASH) + `report_date` (RANGE)
- **GSI:** `campaign-status-index` (campaign_name HASH + status RANGE)
- **Purpose:** Time-series asset performance tracking (text + image assets)

### `rising_asset_graveyard`
- **Key:** `campaign_name` (HASH) + `date_killed` (RANGE)
- **Purpose:** Store killed assets for Claude API learning context

### `rising_budget_performance`
- **Key:** `campaign_name` (HASH) + `week_ending` (RANGE)
- **Purpose:** Weekly budget metrics and recommendations

### `rising_image_registry`
- **Key:** `image_id` (HASH)
- **Purpose:** Image metadata, AI analysis, Google Ads mapping, performance tracking
- **Key fields:** `content_category`, `eligible_slots`, `google_ads_assets`, `performance_by_campaign`

## S3 Bucket

### `rising-pmax`
- **Purpose:** Image asset storage
- **Structure:** `images/{image_id}.{jpg|png}` — flat by ID, no folder hierarchy
- **Versioning:** Enabled
- **Encryption:** AES-256 server-side
- **Access:** Private (no public access)

## Campaign Image Profiles

Each campaign has an `image_profile` defining the ideal content category distribution:

| Category | Core Brand | Replacement Nets |
|----------|-----------|-----------------|
| product_hero | 20% | 25% |
| product_in_use | 30% | 25% |
| lifestyle_with_product | 30% | 15% |
| lifestyle_no_product | 10% | 5% |
| product_detail | 10% | 30% |

Core Brand (prospecting) favors lifestyle-with-product and product-in-use — new customers need to see the product in context. Replacement Nets (retention) favors product detail and product hero — existing customers need to see what's new.

## Data Sources

### ROAS Calculation
ROAS is calculated using **Shopify revenue** (not Google Ads conversion value). Google Ads over-counts revenue in PMax because it blends Shopping, Search, Display, and YouTube attribution. Shopify's "last non-direct click" attribution via `customerJourneySummary` GraphQL API provides the true revenue figure.

- **Revenue:** Shopify `currentSubtotalPriceSet - refunds` (net sales, excludes tax/shipping)
- **Spend:** Google Ads campaign-level `cost_micros` (total campaign spend, not just text assets)
- **ROAS:** `(Shopify Revenue / Google Ads Spend) * 100`

### Asset Flagging
Text assets are flagged using **CTR only**. Individual text asset conversion data is unreliable in PMax because most conversions come from Shopping placements (product images), not text ads. CTR thresholds vary by season and asset type (headline, long headline, description).

Image assets are flagged at a flat **1.0% CTR threshold** across all seasons. This is a conservative baseline — image CTR in PMax runs lower than text. Flagged images are reported for manual replacement.

### Seasonal Behavior
In deep winter (Jan-Feb) and low season (Nov-Dec), the system operates in **monitor-only mode**. Budget/ROAS tracking continues, but asset flagging, replacement generation, and CSV export are disabled. This prevents removing copy based on statistically insignificant low-volume data.

**Preview mode:** Invoke the weekly review Lambda with `{"preview_mode": true}` to run analysis in any season without side effects (no graveyard, no replacements, no CSV). Results are labeled as preview in Slack.

### Image Analysis
Images are analyzed once at upload/bootstrap time using Claude Vision (Sonnet). The analysis extracts structured metadata: content category, product visibility, scene type, background complexity, crop eligibility, and seasonal relevance. Analysis results are immutable — content doesn't change, only performance data updates weekly.

## Security

- All secrets stored in AWS Parameter Store as SecureString (KMS encrypted)
- Lambda IAM role follows least privilege principle
- No credentials in code, environment variables, or logs
- S3 bucket: private, no public access, server-side encryption
- Parameter Store paths:
  - `/Google_Ads/*` (6 parameters)
  - `/Slack/*` (2 parameters)
  - `/Anthropic/*` (1 parameter)
  - `/Shopify/*` (2 parameters)

## Error Handling

- All external API calls retry 3 times with exponential backoff
- If Claude API fails, analysis still sent without replacements (graceful degradation)
- If Claude Vision fails during bootstrap, individual image errors are logged and skipped
- All errors reported to Slack DM (never fail silently)
- Lambda timeout: 5 min (weekly review, image ops), 3 min (verification)

## Budget Monitoring

The system tracks ROAS performance weekly and recommends budget changes:

- **INCREASE:** ROAS 10%+ above target → +20% budget
- **HOLD:** ROAS within +/-10% of target → maintain budget
- **DECREASE:** ROAS 10-30% below target → -20% budget
- **PAUSE:** ROAS >30% below target → reduce to $10/day maintenance
- **MARKET CEILING:** Budget utilization <80% at >$100/day → stop scaling

Emergency alerts fire for: CTR collapse, budget runaway, and market ceiling detection. These alerts are active in all seasons, including monitor-only periods.
