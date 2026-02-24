"""Main Lambda handler for the weekly PMax asset review.

Triggered every Monday at 6:00 AM Mountain Time via EventBridge.

Steps:
1. Load config and credentials from Parameter Store
2. Determine current season and thresholds
3. Collect data from Google Ads API
4. Save raw data to DynamoDB
5. Analyze and flag underperformers
6. Generate replacement copy via Claude API
7. Calculate budget performance
8. Check emergency conditions
9. Build CSV files
10. Send Slack notification
"""

import logging
import traceback
import sys
import os

# Add project root to path for Lambda packaging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CAMPAIGNS as FALLBACK_CAMPAIGNS, logger
from src.campaign_config import load_campaigns_with_fallback
from config.thresholds import get_season_name, get_seasonal_budget, get_thresholds
from database.queries import (
    get_budget_history,
    get_graveyard_assets,
    get_latest_asset_records,
    save_asset_performance,
    save_budget_performance,
    save_to_graveyard,
)
from src.analyzer import (
    AssetAnalyzer,
    calculate_budget_recommendation,
    check_emergency_conditions,
)
from src.copy_generator import CopyGenerator
from src.csv_builder import CSVBuilder
from src.data_collector import GoogleAdsCollector
from src.shopify_collector import ShopifyCollector
from src.slack_notifier import SlackNotifier
from utils.aws_helpers import (
    get_anthropic_api_key,
    get_google_ads_credentials,
    get_shopify_credentials,
    get_slack_credentials,
)
from utils.date_helpers import get_current_month, get_lookback_date, get_today_mountain


def lambda_handler(event, context):
    """Main Lambda handler for weekly review."""
    logger.info("Weekly review started")

    slack_notifier = None

    try:
        # Check for preview mode (run analysis even in off-season, no side effects)
        preview_mode = event.get("preview_mode", False)
        if preview_mode:
            logger.info("PREVIEW MODE: Will flag assets but skip graveyard/replacements/CSV")

        # Step 1: Load credentials
        logger.info("Step 1: Loading credentials")
        google_creds = get_google_ads_credentials()
        slack_creds = get_slack_credentials()
        shopify_creds = get_shopify_credentials()
        anthropic_key = get_anthropic_api_key()

        slack_notifier = SlackNotifier(
            bot_token=slack_creds["token"],
            user_id=slack_creds["channel"],
        )

        # Step 2: Determine season
        logger.info("Step 2: Determining season and thresholds")
        month = get_current_month()
        season = get_season_name(month)
        thresholds = get_thresholds(month)
        seasonal_budget = get_seasonal_budget(month)
        today = get_today_mountain()

        asset_changes_enabled = thresholds.get("asset_changes_enabled", True)
        logger.info(
            "Season: %s, Month: %d, asset_changes: %s",
            season, month, asset_changes_enabled,
        )

        # Step 3: Collect data from Google Ads
        logger.info("Step 3: Collecting Google Ads data")
        collector = GoogleAdsCollector(google_creds)

        # Load campaigns from S3 config (auto-syncs if stale, falls back to settings.py)
        CAMPAIGNS = load_campaigns_with_fallback(collector)

        all_flagged = []
        all_replacements = {}
        all_csv_files = []
        all_budget_data = {}
        all_emergency_alerts = []
        all_sitelinks = {}

        for campaign_name, campaign_config in CAMPAIGNS.items():
            campaign_id = campaign_config.get("campaign_id")
            if not campaign_id:
                logger.warning(
                    "No campaign_id for '%s', skipping", campaign_name
                )
                continue

            logger.info("Processing campaign: %s", campaign_name)

            # Skip paused campaigns
            settings = collector.get_campaign_settings(campaign_id)
            status = settings.get("campaign_status", "")
            if status == "PAUSED":
                logger.info("Campaign '%s' is PAUSED, skipping", campaign_name)
                continue

            lookback = thresholds["lookback_days"]
            lookback_start = get_lookback_date(lookback)

            # Collect text asset performance
            assets = collector.collect_for_campaign(
                campaign_name=campaign_name,
                campaign_id=campaign_id,
                start_date=lookback_start,
                end_date=today,
            )

            # Collect image asset performance
            image_assets = collector.collect_images_for_campaign(
                campaign_name=campaign_name,
                campaign_id=campaign_id,
                start_date=lookback_start,
                end_date=today,
            )

            # Separate sitelinks from text assets (different query level)
            sitelinks = [a for a in assets if a.get("asset_type") == "SITELINK"]
            assets = [a for a in assets if a.get("asset_type") != "SITELINK"]
            all_sitelinks[campaign_name] = sitelinks

            # Step 4: Save raw data to DynamoDB
            logger.info("Step 4: Saving %d text + %d image assets to DynamoDB", len(assets), len(image_assets))
            for asset in assets:
                asset["report_date"] = today
                save_asset_performance(asset)
            for img_asset in image_assets:
                img_asset["report_date"] = today
                save_asset_performance(img_asset)

            # Step 5 & 6: Analyze and flag underperformers, generate replacements
            # Run in active seasons, or in preview mode (any season)
            replacements = {}
            flagged = []
            flagged_images = []
            claude_error = None
            if asset_changes_enabled or preview_mode:
                mode_label = "PREVIEW " if preview_mode and not asset_changes_enabled else ""
                logger.info("Step 5: %sAnalyzing assets", mode_label)
                graveyard = get_graveyard_assets(campaign_name)
                analyzer = AssetAnalyzer(month=month)
                flagged = analyzer.flag_underperformers(assets, graveyard)
                all_flagged.extend(flagged)

                # Flag underperforming images (no replacement generation)
                flagged_images = analyzer.flag_underperformers(image_assets, graveyard)
                all_flagged.extend(flagged_images)
                logger.info(
                    "%sFlagged %d text + %d image assets for campaign '%s'",
                    mode_label, len(flagged), len(flagged_images), campaign_name,
                )

                # Skip replacements in preview mode
                if asset_changes_enabled and not preview_mode:
                    logger.info("Step 6: Generating replacements for %d text assets", len(flagged))
                    if flagged:
                        try:
                            generator = CopyGenerator(api_key=anthropic_key)
                            replacements = generator.generate_replacements(flagged, graveyard)
                        except Exception as e:
                            claude_error = str(e)
                            logger.error("Claude API failed: %s", e, exc_info=True)
                    all_replacements.update(replacements)
                else:
                    logger.info("Step 6: Skipping replacements (%s)",
                                "preview mode" if preview_mode else "off-season")
            else:
                logger.info(
                    "Steps 5-6: Skipping asset flagging/replacement (%s is monitor-only)",
                    season,
                )

            # Step 7: Calculate budget performance (Shopify ROAS)
            logger.info("Step 7: Calculating budget performance with Shopify revenue")

            # Get campaign-level metrics from Google Ads
            campaign_metrics = collector.get_campaign_metrics(
                campaign_id, start_date=lookback_start, end_date=today
            )
            total_spend = campaign_metrics["total_spend"]
            campaign_ctr = campaign_metrics["ctr"]
            campaign_clicks = campaign_metrics["clicks"]
            campaign_impressions = campaign_metrics["impressions"]
            actual_daily_avg = total_spend / lookback if lookback > 0 else 0

            # Get actual campaign budget from Google Ads
            daily_budget_target = collector.get_campaign_budget(campaign_id)
            if daily_budget_target <= 0:
                daily_budget_target = seasonal_budget["recommended_daily"]
                logger.warning("Using seasonal budget fallback: $%.2f", daily_budget_target)

            target_roas = seasonal_budget["target_roas"]
            utilization = (
                (actual_daily_avg / daily_budget_target * 100)
                if daily_budget_target > 0
                else 0
            )

            # Get true revenue from Shopify (last non-direct click attribution)
            shopify = ShopifyCollector(
                store_url=shopify_creds["store_url"],
                access_token=shopify_creds["access_token"],
            )
            shopify_revenue = shopify.get_google_attributed_revenue(
                start_date=lookback_start,
                end_date=today,
                campaign_name=campaign_name,
            )
            total_revenue = shopify_revenue["total_revenue"]
            shopify_orders = shopify_revenue["order_count"]

            roas = (total_revenue / total_spend * 100) if total_spend > 0 else 0

            # 7-day and 14-day ROAS
            roas_7d = 0
            roas_14d = 0
            start_7d = get_lookback_date(7)
            start_14d = get_lookback_date(14)

            metrics_7d = collector.get_campaign_metrics(
                campaign_id, start_date=start_7d, end_date=today
            )
            revenue_7d = shopify.get_google_attributed_revenue(
                start_date=start_7d, end_date=today, campaign_name=campaign_name,
            )
            if metrics_7d["total_spend"] > 0:
                roas_7d = revenue_7d["total_revenue"] / metrics_7d["total_spend"] * 100

            metrics_14d = collector.get_campaign_metrics(
                campaign_id, start_date=start_14d, end_date=today
            )
            revenue_14d = shopify.get_google_attributed_revenue(
                start_date=start_14d, end_date=today, campaign_name=campaign_name,
            )
            if metrics_14d["total_spend"] > 0:
                roas_14d = revenue_14d["total_revenue"] / metrics_14d["total_spend"] * 100

            budget_rec = calculate_budget_recommendation(
                current_daily_budget=daily_budget_target,
                actual_daily_spend_avg=actual_daily_avg,
                current_roas=roas,
                target_roas=target_roas,
                season=season,
            )

            budget_data = {
                "campaign_name": campaign_name,
                "week_ending": today,
                "report_date": today,
                "lookback_start": lookback_start,
                "lookback_end": today,
                "lookback_days": lookback,
                "season": season,
                "daily_budget_target": daily_budget_target,
                "actual_daily_spend_avg": round(actual_daily_avg, 2),
                "total_spend": round(total_spend, 2),
                "campaign_ctr": campaign_ctr,
                "campaign_clicks": campaign_clicks,
                "campaign_impressions": campaign_impressions,
                "total_revenue": round(total_revenue, 2),
                "shopify_orders": shopify_orders,
                "shopify_google_share_pct": shopify_revenue.get("google_share_pct", 0),
                "roas_percent": round(roas, 1),
                "roas_7d_percent": round(roas_7d, 1),
                "roas_14d_percent": round(roas_14d, 1),
                "roas_source": "shopify",
                "target_roas_percent": target_roas,
                "budget_utilization_percent": round(utilization, 1),
                "recommendation": budget_rec["action"],
                "recommended_daily_budget": budget_rec["recommended_budget"],
                "recommendation_reason": budget_rec["reason"],
                "market_ceiling_detected": budget_rec["market_ceiling_detected"],
            }
            all_budget_data[campaign_name] = budget_data

            save_budget_performance(budget_data)

            # Step 8: Check emergency conditions
            logger.info("Step 8: Checking emergency conditions")
            history = get_budget_history(campaign_name, weeks=4)
            emergencies = check_emergency_conditions(
                budget_data, assets, history
            )
            all_emergency_alerts.extend(emergencies)

            # Step 9: Save flagged assets to graveyard and build CSV
            # Skip in preview mode (no permanent side effects)
            if asset_changes_enabled and not preview_mode:
                for asset in flagged:
                    asset["date_killed"] = today
                    save_to_graveyard(asset)
                for img_asset in flagged_images:
                    img_asset["date_killed"] = today
                    save_to_graveyard(img_asset)

                logger.info("Step 9: Building CSV (text assets only)")
                if flagged:
                    csv_builder = CSVBuilder()
                    rows = csv_builder.build_google_ads_csv(
                        flagged_assets=flagged,
                        replacements=replacements,
                        campaign_name=campaign_name,
                        asset_group=campaign_config["asset_group"],
                    )
                    csv_path = csv_builder.save_csv(
                        rows=rows,
                        campaign_slug=campaign_config["slug"],
                    )
                    all_csv_files.append(csv_path)
            elif preview_mode:
                logger.info("Step 9: Skipping graveyard/CSV (preview mode)")

        # Step 10: Send Slack notification
        logger.info("Step 10: Sending Slack notification")

        # Send emergency alerts first (if any)
        if all_emergency_alerts:
            slack_notifier.send_emergency_alerts(all_emergency_alerts)

        # Send the weekly review with per-campaign budget data
        slack_notifier.send_review(
            month=month,
            flagged_assets=all_flagged,
            replacements=all_replacements,
            csv_files=all_csv_files,
            all_budget_data=all_budget_data,
            emergency_alerts=all_emergency_alerts,
            asset_changes_enabled=asset_changes_enabled,
            preview_mode=preview_mode,
            all_sitelinks=all_sitelinks,
        )

        result = {
            "statusCode": 200,
            "body": {
                "season": season,
                "assets_analyzed": sum(
                    len(collector.collect_for_campaign(cn, cc.get("campaign_id", ""), 7))
                    for cn, cc in CAMPAIGNS.items()
                    if cc.get("campaign_id")
                ) if False else "see logs",  # Avoid re-fetching
                "assets_flagged": len(all_flagged),
                "replacements_generated": len(all_replacements),
                "csv_files": len(all_csv_files),
                "emergency_alerts": len(all_emergency_alerts),
                "budget_recommendations": {
                    k: v.get("recommendation")
                    for k, v in all_budget_data.items()
                },
                "claude_error": claude_error,
                "preview_mode": preview_mode,
            },
        }

        logger.info("Weekly review completed: %s", result)
        return result

    except Exception as e:
        logger.error("Weekly review FAILED: %s", e)
        tb = traceback.format_exc()
        logger.error(tb)

        # Try to notify via Slack
        if slack_notifier:
            slack_notifier.send_error(str(e), tb)

        return {
            "statusCode": 500,
            "body": {"error": str(e)},
        }
