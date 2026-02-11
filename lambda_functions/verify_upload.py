"""Lambda handler for upload verification.

Triggered every Thursday at 6:00 AM Mountain Time via EventBridge.
Runs 3 days after the weekly review to check if changes were applied.

Steps:
1. Load last week's recommendations from DynamoDB
2. Query current Google Ads data
3. Compare and verify uploads
4. Update DynamoDB
5. Send verification report to Slack
"""

import logging
import traceback
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CAMPAIGNS, logger
from config.thresholds import get_thresholds
from database.queries import get_latest_asset_records
from src.data_collector import GoogleAdsCollector
from src.slack_notifier import SlackNotifier
from src.verifier import UploadVerifier
from utils.aws_helpers import get_google_ads_credentials, get_slack_credentials
from utils.date_helpers import get_current_month


def lambda_handler(event, context):
    """Lambda handler for upload verification."""
    logger.info("Upload verification started")

    slack_notifier = None

    try:
        # Load credentials
        google_creds = get_google_ads_credentials()
        slack_creds = get_slack_credentials()

        slack_notifier = SlackNotifier(
            bot_token=slack_creds["token"],
            user_id=slack_creds["channel"],
        )

        collector = GoogleAdsCollector(google_creds)
        month = get_current_month()
        thresholds = get_thresholds(month)

        verification_reports = []

        for campaign_name, campaign_config in CAMPAIGNS.items():
            campaign_id = campaign_config.get("campaign_id")
            if not campaign_id:
                logger.warning(
                    "No campaign_id for '%s', skipping", campaign_name
                )
                continue

            logger.info("Verifying campaign: %s", campaign_name)

            # Get last week's flagged assets from DynamoDB
            db_records = get_latest_asset_records(campaign_name)
            flagged_assets = [
                r for r in db_records
                if r.get("status") in ("killed", "paused", "flagged")
                and r.get("kill_reason")
            ]

            if not flagged_assets:
                logger.info("No flagged assets to verify for %s", campaign_name)
                continue

            # Build replacements dict from DB records
            replacements = {}
            for asset in flagged_assets:
                if asset.get("replaced_by"):
                    replacements[asset["asset_id"]] = {
                        "text": asset["replaced_by"],
                        "strategy": asset.get("replacement_reason", ""),
                    }

            # Verify uploads
            verifier = UploadVerifier(collector, campaign_name, campaign_id)
            live_data = verifier.get_current_asset_status()
            report = verifier.compare_to_recommendations(
                live_data, flagged_assets, replacements
            )

            # Update database
            verifier.update_database(report, flagged_assets)

            # Generate report text
            report_text = verifier.generate_verification_report(report)
            verification_reports.append(report_text)

            logger.info(
                "Verification for %s: %d paused, %d added, %d failed",
                campaign_name,
                report["paused_successfully"],
                report["added_successfully"],
                len(report["paused_failed"]) + len(report["added_failed"]),
            )

        # Send verification to Slack
        if verification_reports:
            full_report = "\n\n".join(verification_reports)
            slack_notifier.client.chat_postMessage(
                channel=slack_notifier.user_id,
                text=full_report,
                mrkdwn=True,
            )
            logger.info("Verification report sent to Slack")
        else:
            slack_notifier.client.chat_postMessage(
                channel=slack_notifier.user_id,
                text="📋 *Upload Verification*\n\nNo pending verifications this week.",
                mrkdwn=True,
            )

        return {
            "statusCode": 200,
            "body": {"campaigns_verified": len(verification_reports)},
        }

    except Exception as e:
        logger.error("Verification FAILED: %s", e)
        tb = traceback.format_exc()
        logger.error(tb)

        if slack_notifier:
            slack_notifier.send_error(f"Verification failed: {e}", tb)

        return {
            "statusCode": 500,
            "body": {"error": str(e)},
        }
