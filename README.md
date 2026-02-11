# Rising PMax Optimizer

Automated Google Ads Performance Max asset optimization system for Rising Fishing.

## What It Does

1. **Collects** Google Ads PMax asset performance data weekly
2. **Analyzes** assets against seasonal thresholds
3. **Flags** underperformers and generates Rising-voice replacement copy via Claude API
4. **Delivers** recommendations via Slack DM with Google Ads Editor CSV
5. **Verifies** manual uploads and tracks performance over time

**Time commitment:** ~7 minutes/week (review Slack message + upload CSV)

## Quick Start

### Prerequisites

- Python 3.12+
- AWS account with CLI configured
- Google Ads API credentials ([setup guide](docs/API_SETUP.md))
- Slack workspace with bot app ([setup guide](docs/API_SETUP.md#slack-app-setup))
- Anthropic API key

### Local Development

```bash
cd rising-pmax-optimizer

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy env template
cp .env.example .env

# Run tests
pytest tests/ -v
```

### Deploy to AWS

```bash
# 1. Build and package
./deployment/deploy.sh

# 2. Apply Terraform
cd deployment/terraform
terraform apply tfplan

# 3. Add secrets to Parameter Store
aws ssm put-parameter --name /Google_Ads/DEVELOPER_TOKEN --value "YOUR_TOKEN" --type SecureString
aws ssm put-parameter --name /Google_Ads/CLIENT_ID --value "YOUR_ID" --type SecureString
aws ssm put-parameter --name /Google_Ads/CLIENT_SECRET --value "YOUR_SECRET" --type SecureString
aws ssm put-parameter --name /Google_Ads/REFRESH_TOKEN --value "YOUR_TOKEN" --type SecureString
aws ssm put-parameter --name /Google_Ads/CUSTOMER_ID --value "1234567890" --type SecureString
aws ssm put-parameter --name /Slack/TOKEN --value "xoxb-YOUR-TOKEN" --type SecureString
aws ssm put-parameter --name /Slack/PMAX_CHANNEL --value "U0XXXXXXX" --type String
aws ssm put-parameter --name /Anthropic/API_KEY --value "sk-ant-YOUR-KEY" --type SecureString

# 4. Test
aws lambda invoke --function-name rising-weekly-review --payload '{}' response.json
cat response.json
```

## Architecture

- **Lambda Functions:** `rising-weekly-review` (Monday 6am MT), `rising-verify-upload` (Thursday 6am MT)
- **Database:** DynamoDB (3 tables: asset_performance, asset_graveyard, budget_performance)
- **APIs:** Google Ads, Claude Sonnet 4, Slack
- **Secrets:** AWS Parameter Store (all encrypted)
- **Scheduling:** EventBridge cron rules

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full details.

## Project Structure

```
rising-pmax-optimizer/
├── config/
│   ├── settings.py          # Environment config, API clients
│   └── thresholds.py        # Seasonal thresholds and budgets
├── src/
│   ├── data_collector.py    # Google Ads API client
│   ├── analyzer.py          # Flagging logic + budget recommendations
│   ├── copy_generator.py    # Claude API for replacements
│   ├── csv_builder.py       # Google Ads Editor CSV format
│   ├── slack_notifier.py    # Slack DM delivery
│   └── verifier.py          # Upload verification
├── lambda_functions/
│   ├── weekly_review.py     # Monday Lambda handler
│   └── verify_upload.py     # Thursday Lambda handler
├── database/
│   ├── schema.py            # DynamoDB table definitions
│   └── queries.py           # Common query patterns
├── utils/
│   ├── aws_helpers.py       # Parameter Store, retry logic
│   └── date_helpers.py      # Timezone, seasonality
├── tests/
│   ├── test_data/           # Historical CSV data
│   └── test_analyzer.py     # Analysis tests
└── deployment/
    ├── terraform/main.tf    # Infrastructure as code
    ├── build_layer.sh       # Lambda layer builder
    └── deploy.sh            # Full deployment script
```

## Seasonal Thresholds

| Season | Months | Min Impressions | Min CTR (Headline) | Max Cost (0 conv) |
|--------|--------|-----------------|--------------------|--------------------|
| Deep Winter | Jan-Feb | 150 | 2.0% | $30 |
| Low Season | Nov-Dec | 150 | 2.0% | $30 |
| Shoulder | Mar-Apr, Sep-Oct | 300 | 3.0% | $40 |
| Peak | May-Aug | 500 | 4.0% | $50 |

## Costs

- **AWS:** <$10/month (DynamoDB on-demand, Lambda free tier)
- **Anthropic:** <$5/month (~$0.03/week for ~10 replacements)
- **Google Ads API:** Free
- **Slack API:** Free
