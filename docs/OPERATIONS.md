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
1. Review the Slack message
2. Download the CSV file(s)
3. Open Google Ads Editor
4. Import CSV (Account > Import > From file)
5. Review changes in the editor
6. Post to Google Ads

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
| Zero Conversions | 7+ days, $20+/day spend | CRITICAL |
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
- Verify thresholds match market conditions
- Adjust budget baselines based on actual performance
- Update voice guidelines if brand evolves

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
