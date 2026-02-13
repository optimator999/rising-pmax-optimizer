# Operations Guide

## Weekly Workflow

### Monday 6:00 AM MT (Automatic)
1. `rising-weekly-review` Lambda runs
2. You receive a Slack DM with:
   - Budget performance summary
   - Flagged assets with stats and kill reasons
   - Replacement suggestions
   - CSV file(s) attached

### Your Actions (Monday, ~7 minutes)

**Shoulder/Peak (Mar-Oct):**
1. Review the Slack message
2. Download the CSV file(s)
3. Open Google Ads Editor
4. Import CSV (Account > Import > From file)
5. Review changes in the editor
6. Post to Google Ads

**Deep Winter/Low Season (Nov-Feb):**
1. Review the monitoring report in Slack
2. Note budget/ROAS trends
3. No asset changes needed - the system is in monitor-only mode

### Thursday 6:00 AM MT (Automatic)
1. `rising-verify-upload` Lambda runs
2. Compares recommendations to live Google Ads data
3. Sends verification report to Slack
4. Updates database with actual status

## Monitoring

### CloudWatch Logs
Both Lambda functions log to CloudWatch:
- `/aws/lambda/rising-weekly-review`
- `/aws/lambda/rising-verify-upload`

### What to Watch
- Lambda execution errors
- Google Ads API rate limit warnings
- Claude API failures (system degrades gracefully)
- DynamoDB throttling (unlikely with on-demand billing)
- Slack delivery failures

### All Errors Go to Slack
The system never fails silently. Any error during execution triggers a Slack DM with the error message and stack trace.

## Manual Lambda Invocation

Test the weekly review:
```bash
aws lambda invoke \
  --function-name rising-weekly-review \
  --payload '{}' \
  response.json

cat response.json
```

Test verification:
```bash
aws lambda invoke \
  --function-name rising-verify-upload \
  --payload '{}' \
  response.json

cat response.json
```

## Emergency Alerts

The system sends immediate alerts for:

| Alert | Trigger | Severity |
|-------|---------|----------|
| CTR Collapse | 50%+ drop week-over-week | HIGH |
| Budget Runaway | 2x budget with low ROAS | HIGH |
| Market Ceiling | <80% utilization at >$100/day | INFO |

## Budget Recommendations

When you receive a budget recommendation:

### INCREASE
1. Verify ROAS is sustainable (not a one-week spike)
2. Go to Google Ads > Campaign > Settings > Budget
3. Increase by the recommended amount
4. Monitor for 3-4 days

### HOLD
No action needed.

### DECREASE
1. Investigate why ROAS dropped
2. Reduce budget as recommended
3. Fix underlying issues before scaling back

### PAUSE
1. Reduce to $10/day immediately
2. Diagnose the problem
3. Don't resume until issue is resolved

## Maintenance

### Monthly
- Review graveyard patterns (what copy keeps failing?)
- Check AWS bill (target: <$10/month)
- Adjust thresholds if needed in `config/thresholds.py`

### Seasonally
- Review transition into new season (thresholds change automatically)
- Verify budget baselines match actual performance
- Update voice guidelines if brand evolves

## Seasonal Strategy

Rising Fishing is a seasonal business. The PMax optimizer adjusts its behavior based on the time of year.

### Why Off-Season is Monitor-Only

In deep winter (Jan-Feb) and low season (Nov-Dec), PMax text assets get very few impressions. With a $25/day budget, most spend goes to Shopping placements (product images), not text ads. Individual text assets might get 50-150 impressions over 60 days - not enough for statistically meaningful CTR analysis. Flagging and replacing assets based on low-volume data risks:

- Removing copy that would perform well in higher-volume seasons
- Wasting Claude API calls on replacements that won't get tested
- Creating churn that disrupts Google's machine learning optimization

### Season Definitions

| Season | Months | Annual Demand | Asset Changes | Min Impressions |
|--------|--------|--------------|---------------|-----------------|
| Deep Winter | Jan-Feb | 2-3% | Monitor only | 150 (tracking) |
| Shoulder | Mar-Apr | 7-10% | Active | 500 |
| Peak | May-Aug | 10-13% | Active | 500 |
| Shoulder | Sep-Oct | 8-9% | Active | 500 |
| Low Season | Nov-Dec | 6-7% | Monitor only | 150 (tracking) |

### Monitor-Only Seasons (Nov-Feb)

**What runs:** Data collection, DynamoDB storage, budget monitoring, ROAS tracking, emergency alerts.

**What doesn't run:** Asset flagging, replacement generation, CSV building, graveyard writes.

**Slack message format:** "Weekly Monitoring Report" with budget/ROAS dashboard only. No asset recommendations.

**Your action:** Review budget trends. No asset changes needed.

### Active Seasons (Mar-Oct)

**What runs:** Full pipeline - data collection, analysis, flagging, Claude-generated replacements, CSV export, Slack review.

**Min impressions threshold:** 500. Assets need meaningful volume before being judged.

**Your action:** Review flagged assets, import CSV into Google Ads Editor, post changes.

## Break-Even ROAS Analysis

### Inputs

- **Blended gross margin:** 64% (DTC + Pro + International channels, excluding Dealer)
- **Channel tags:** `channel:dtc`, `channel:pro`, `channel:int` (PMax drives these three)
- **Excluded:** `channel:dealer` (wholesale, not ad-driven)

### Formula

```
Break-even ROAS = 1 / Gross Margin = 1 / 0.64 = 156.25%
```

At break-even, gross profit from ad-driven revenue exactly covers the ad spend:

```
Ad Spend = Revenue × Gross Margin
Revenue  = Ad Spend / 0.64
ROAS     = Revenue / Ad Spend = 1 / 0.64 = 1.5625
```

### Worked Example

| | Amount |
|---|--------|
| Ad Spend | $100.00 |
| Revenue needed (break-even) | $156.25 |
| COGS (36% of revenue) | -$56.25 |
| Gross Profit | $100.00 |
| Minus Ad Spend | -$100.00 |
| **Net Contribution** | **$0.00** |

### Target ROAS by Season

| Season | Target ROAS | vs Break-Even (156%) | Net Margin on Ad Spend |
|--------|------------|----------------------|----------------------|
| Deep Winter | 160% | +4% above | +2.4% |
| Low Season | 200% | +44% above | +28% |
| Shoulder | 200% | +44% above | +28% |
| Peak | 200% | +44% above | +28% |

**156% is the absolute floor.** Below that, every ad dollar loses money. The system's budget recommendation engine will flag DECREASE or PAUSE when ROAS drops toward this threshold.

### If Margin Changes

If gross margin changes (e.g., new product mix, supplier pricing), recalculate:

| Gross Margin | Break-Even ROAS |
|-------------|----------------|
| 55% | 182% |
| 60% | 167% |
| **64%** | **156%** |
| 70% | 143% |

Update `target_roas` in `config/thresholds.py` if the floor shifts.

### Transition Points

- **March 1:** System automatically switches from monitor-only to active. First Monday in March will include asset recommendations. Expect higher flag counts as the analyzer catches up on winter underperformers.
- **November 1:** System switches to monitor-only. Last active review is the final Monday in October.

### Updating Thresholds
Edit `config/thresholds.py` and redeploy:
```bash
cd deployment
./deploy.sh
cd terraform
terraform apply tfplan
```

## Troubleshooting

### Lambda Times Out
- Check Google Ads API response time in CloudWatch
- Increase Lambda timeout in Terraform (currently 5 min)
- Check for network issues

### No Slack Message Received
- Verify Slack token is valid: `aws ssm get-parameter --name /Slack/TOKEN --with-decryption`
- Verify user ID: `aws ssm get-parameter --name /Slack/PMAX_CHANNEL`
- Check CloudWatch logs for Slack API errors
- Ensure the bot app is still installed in workspace

### Google Ads API Errors
- Check if refresh token expired (re-authorize if needed)
- Verify developer token is approved
- Check API rate limits in CloudWatch logs

### Claude API Errors
- System continues without replacements (graceful degradation)
- Check API key validity
- Verify Anthropic account has credits
