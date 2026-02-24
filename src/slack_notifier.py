"""Slack DM delivery for weekly reviews and alerts."""

import logging
import traceback
from typing import Any, Dict, List, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config.thresholds import get_monthly_demand, get_season_name
from utils.date_helpers import format_date, get_today_mountain

logger = logging.getLogger("rising-pmax.slack")


class SlackNotifier:
    """Sends review messages and alerts via Slack DM."""

    def __init__(self, bot_token: str, user_id: str):
        """Initialize Slack client."""
        self.client = WebClient(token=bot_token)
        self.user_id = user_id
        logger.info("Slack notifier initialized for user %s", user_id)

    def send_review(
        self,
        month: int,
        flagged_assets: List[Dict[str, Any]],
        replacements: Dict[str, Dict[str, str]],
        csv_files: List[str],
        all_budget_data: Optional[Dict[str, Dict[str, Any]]] = None,
        emergency_alerts: Optional[List[Dict[str, Any]]] = None,
        asset_changes_enabled: bool = True,
        preview_mode: bool = False,
        all_sitelinks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> bool:
        """Send the full weekly review as a Slack DM.

        Returns True on success, False on failure.
        """
        try:
            message = self._format_review_message(
                month, flagged_assets, replacements, all_budget_data,
                emergency_alerts, asset_changes_enabled, preview_mode,
                all_sitelinks,
            )

            # Send main message
            response = self.client.chat_postMessage(
                channel=self.user_id,
                text=message,
                mrkdwn=True,
            )

            thread_ts = response["ts"]

            # Upload CSV files in the same thread
            for csv_path in csv_files:
                self._upload_file(csv_path, thread_ts)

            logger.info("Weekly review sent successfully")
            return True

        except SlackApiError as e:
            logger.error("Slack API error: %s", e.response["error"])
            return False
        except Exception as e:
            logger.error("Failed to send review: %s", e)
            return False

    def send_error(self, error_message: str, stack_trace: str = "") -> bool:
        """Send an error notification to Slack."""
        today = get_today_mountain()
        text = (
            f"*ERROR - Weekly Review Failed*\n\n"
            f"Timestamp: {today}\n"
            f"Error: {error_message}\n"
        )
        if stack_trace:
            text += f"```\n{stack_trace[:2000]}\n```\n"
        text += "\nAction required: Manual investigation"

        try:
            self.client.chat_postMessage(
                channel=self.user_id,
                text=text,
                mrkdwn=True,
            )
            return True
        except Exception as e:
            logger.error("Failed to send error to Slack: %s", e)
            return False

    def send_audit_report(self, report_text: str) -> bool:
        """Send campaign health audit report as Slack DM.

        Returns True on success, False on failure.
        """
        try:
            self.client.chat_postMessage(
                channel=self.user_id,
                text=report_text,
                mrkdwn=True,
            )
            logger.info("Audit report sent to Slack")
            return True
        except SlackApiError as e:
            logger.error("Slack API error sending audit: %s", e.response["error"])
            return False
        except Exception as e:
            logger.error("Failed to send audit report: %s", e)
            return False

    def send_emergency_alerts(self, alerts: List[Dict[str, Any]]) -> bool:
        """Send emergency alert messages."""
        for alert in alerts:
            severity_emoji = {
                "CRITICAL": "🚨",
                "HIGH": "⚠️",
                "INFO": "📊",
            }.get(alert.get("severity", ""), "❗")

            text = f"{severity_emoji} *{alert['title']}*\n\n"
            text += f"{alert['message']}\n\n"
            text += "*Recommended actions:*\n"
            for action in alert.get("actions", []):
                text += f"  - {action}\n"

            if alert.get("auto_action"):
                text += f"\n*Auto-action:* {alert['auto_action']}\n"

            try:
                self.client.chat_postMessage(
                    channel=self.user_id,
                    text=text,
                    mrkdwn=True,
                )
            except SlackApiError as e:
                logger.error("Failed to send emergency alert: %s", e)
                return False

        return True

    def _format_review_message(
        self,
        month: int,
        flagged_assets: List[Dict[str, Any]],
        replacements: Dict[str, Dict[str, str]],
        all_budget_data: Optional[Dict[str, Dict[str, Any]]] = None,
        emergency_alerts: Optional[List[Dict[str, Any]]] = None,
        asset_changes_enabled: bool = True,
        preview_mode: bool = False,
        all_sitelinks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> str:
        """Build the full weekly review Slack message."""
        today = format_date(get_today_mountain())
        season = get_season_name(month)
        demand = get_monthly_demand(month)
        all_budget_data = all_budget_data or {}

        if preview_mode:
            title = f"🔍 *PREVIEW — Weekly Asset Review - {today}*"
        elif asset_changes_enabled:
            title = f"📊 *Weekly Asset Review - {today}*"
        else:
            title = f"📊 *Weekly Monitoring Report - {today}*"

        lines = [
            title,
            "",
            f"Season: {season.replace('_', ' ').title()} ({demand}% annual demand)",
        ]

        if preview_mode and not asset_changes_enabled:
            lines.append("Mode: PREVIEW (off-season — showing what *would* be flagged)")
        elif not asset_changes_enabled:
            lines.append("Mode: Monitor Only (no asset changes in off-season)")

        # Per-campaign budget sections
        for campaign_name, budget_data in all_budget_data.items():
            lines.extend(
                self._format_budget_section(budget_data, season, campaign_name)
            )

        # Sitelink performance sections
        all_sitelinks = all_sitelinks or {}
        for campaign_name, sitelinks in all_sitelinks.items():
            if sitelinks:
                lines.extend(
                    self._format_sitelink_section(sitelinks, campaign_name)
                )

        # Emergency alerts
        if emergency_alerts:
            lines.append("")
            lines.append("━" * 35)
            lines.append("🚨 *EMERGENCY ALERTS*")
            lines.append("━" * 35)
            for alert in emergency_alerts:
                lines.append(f"*{alert['title']}*: {alert['message']}")

        # Split flagged assets into text vs image
        image_types = {"MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE"}
        text_flagged = [a for a in flagged_assets if a.get("asset_type") not in image_types]
        image_flagged = [a for a in flagged_assets if a.get("asset_type") in image_types]

        # Determine whether to show flagged assets
        show_flags = asset_changes_enabled or preview_mode

        # Asset section - different format for monitor-only seasons
        if not show_flags:
            lines.append("")
            lines.append("━" * 35)
            lines.append("📋 *ASSET STATUS*")
            lines.append("━" * 35)
            lines.append("")
            lines.append("Asset changes paused for off-season.")
            lines.append("Budget and ROAS monitoring continues.")
            lines.append("Asset optimization resumes in shoulder season (March).")
            lines.append("")
            lines.append("Image monitoring active. No flags in off-season.")
        else:
            # Group text flagged assets by campaign
            campaigns_with_flags = {}
            for asset in text_flagged:
                cn = asset.get("campaign_name", "Unknown")
                campaigns_with_flags.setdefault(cn, []).append(asset)

            preview_tag = "PREVIEW " if preview_mode else ""

            if not text_flagged:
                lines.append("")
                lines.append("━" * 35)
                lines.append(f"🎯 *{preview_tag}ASSET PERFORMANCE*")
                lines.append("━" * 35)
                lines.append("")
                lines.append("No assets flagged for replacement this week.")
            else:
                for campaign_name, campaign_assets in campaigns_with_flags.items():
                    lines.append("")
                    lines.append("━" * 35)
                    lines.append(f"🎯 *{preview_tag}ASSET PERFORMANCE — {campaign_name}*")
                    lines.append("━" * 35)
                    if preview_mode:
                        lines.append("_No action taken — preview only_")
                    lines.append("")

                    total_cost = sum(float(a.get("cost", 0)) for a in campaign_assets)
                    lines.append(f"*{len(campaign_assets)} assets would be flagged for replacement*"
                                 if preview_mode else
                                 f"*{len(campaign_assets)} assets flagged for replacement*")
                    lines.append(f"Total cost on flagged assets: ${total_cost:,.2f}")
                    lines.append("")

                    for i, asset in enumerate(campaign_assets, 1):
                        asset_id = asset.get("asset_id", "")
                        replacement = replacements.get(asset_id)

                        lines.append(
                            f"❌ *ASSET {i}:* {asset.get('asset_text', '')}"
                        )
                        lines.append(
                            f"Type: {asset.get('asset_type', '')}"
                        )
                        lines.append(
                            f"Stats: {asset.get('impressions', 0)} impr | "
                            f"{asset.get('ctr', 0)}% CTR | "
                            f"${float(asset.get('cost', 0)):,.2f} spent"
                        )
                        lines.append(f"Kill reason: {asset.get('kill_reason', '')}")

                        if preview_mode:
                            lines.append("ℹ️ Preview — no replacement generated")
                        elif replacement:
                            lines.append(f"✅ *REPLACEMENT:* {replacement['text']}")
                            lines.append(f"Strategy: {replacement.get('strategy', '')}")
                        else:
                            lines.append("⚠️ No replacement generated (Claude API issue)")

                        lines.append("")

                if not preview_mode:
                    lines.append("━" * 35)
                    lines.append("")
                    lines.append(f"📎 CSV file(s) attached below.")
                    lines.append("Import into Google Ads Editor to apply changes.")
                    lines.append("Review within 3 days for tracking.")

            # Image performance section (per campaign)
            image_by_campaign = {}
            for asset in image_flagged:
                cn = asset.get("campaign_name", "Unknown")
                image_by_campaign.setdefault(cn, []).append(asset)

            for campaign_name, campaign_images in image_by_campaign.items():
                lines.append("")
                lines.append("━" * 35)
                lines.append(f"🖼️ *{preview_tag}IMAGE PERFORMANCE — {campaign_name}*")
                lines.append("━" * 35)
                if preview_mode:
                    lines.append("_No action taken — preview only_")
                lines.append("")
                lines.append(f"{len(campaign_images)} images below CTR threshold")
                lines.append("")

                for i, asset in enumerate(campaign_images, 1):
                    lines.append(
                        f"❌ *IMAGE {i}:* {asset.get('asset_text', asset.get('asset_name', ''))}"
                    )
                    lines.append(f"   Type: {asset.get('asset_type', '')}")
                    lines.append(
                        f"   Stats: {asset.get('impressions', 0):,} impr | "
                        f"{asset.get('ctr', 0)}% CTR | "
                        f"${float(asset.get('cost', 0)):,.2f} spent"
                    )
                    lines.append("   Action: Replace in Google Ads > Assets")
                    lines.append("")

        return "\n".join(lines)

    def _format_budget_section(
        self, budget_data: Dict[str, Any], season: str, campaign_name: str = ""
    ) -> List[str]:
        """Format the budget performance section of the message."""
        lookback_start = budget_data.get("lookback_start", "")
        lookback_end = budget_data.get("lookback_end", "")
        lookback_days = budget_data.get("lookback_days", "")

        header = f"💰 *BUDGET — {campaign_name}*" if campaign_name else "💰 *BUDGET PERFORMANCE*"
        lines = [
            "",
            "━" * 35,
            header,
            "━" * 35,
            "",
            f"Period: {lookback_start} to {lookback_end} ({lookback_days} days)",
            "",
            f"Current budget: ${float(budget_data.get('daily_budget_target', 0)):,.0f}/day",
            f"Actual spend: ${float(budget_data.get('actual_daily_spend_avg', 0)):,.2f}/day "
            f"({float(budget_data.get('budget_utilization_percent', 0)):.1f}% utilization)",
            f"Total spend: ${float(budget_data.get('total_spend', 0)):,.2f}",
            f"Campaign CTR: {float(budget_data.get('campaign_ctr', 0)):.2f}% "
            f"({budget_data.get('campaign_clicks', 0):,} clicks / "
            f"{budget_data.get('campaign_impressions', 0):,} impr)",
            f"Revenue (Shopify): ${float(budget_data.get('total_revenue', 0)):,.2f}",
            f"Shopify orders (Google-attributed): {budget_data.get('shopify_orders', 0)}",
            f"Google share of Shopify revenue: {float(budget_data.get('shopify_google_share_pct', 0)):.1f}%",
            f"ROAS (Shopify): {float(budget_data.get('roas_percent', 0)):,.0f}%",
            f"  7-day ROAS: {float(budget_data.get('roas_7d_percent', 0)):,.0f}%"
            f"  |  14-day ROAS: {float(budget_data.get('roas_14d_percent', 0)):,.0f}%",
            "",
            f"Target ROAS: {float(budget_data.get('target_roas_percent', 0)):,.0f}%",
        ]

        rec = budget_data.get("recommendation", "hold").upper()
        reason = budget_data.get("recommendation_reason", "")
        rec_budget = budget_data.get("recommended_daily_budget")

        lines.append("")
        lines.append(f"📈 *RECOMMENDATION: {rec}*")
        lines.append(reason)
        if rec_budget and rec != "HOLD":
            lines.append(f"Recommended budget: ${float(rec_budget):,.0f}/day")

        return lines

    def _format_sitelink_section(
        self, sitelinks: List[Dict[str, Any]], campaign_name: str
    ) -> List[str]:
        """Format the sitelink performance section of the message."""
        lines = [
            "",
            "━" * 35,
            f"🔗 *SITELINKS — {campaign_name}*",
            "━" * 35,
            "_Lifetime metrics (not date-segmented)_",
            "",
        ]

        # Sort by clicks descending
        sorted_sitelinks = sorted(sitelinks, key=lambda s: s.get("clicks", 0), reverse=True)

        for sl in sorted_sitelinks:
            clicks = sl.get("clicks", 0)
            impressions = sl.get("impressions", 0)
            ctr = sl.get("ctr", 0)
            cost = float(sl.get("cost", 0))
            conversions = sl.get("conversions", 0)

            lines.append(f"*{sl['asset_text']}*")
            lines.append(
                f"  {impressions:,} impr | {clicks:,} clicks | "
                f"{ctr:.1f}% CTR | ${cost:,.2f} spent | {conversions:.0f} conv"
            )

        return lines

    def _upload_file(self, file_path: str, thread_ts: str) -> None:
        """Upload a file to the Slack DM thread."""
        filename = file_path.split("/")[-1]
        campaign = filename.split("_replacements_")[0].replace("_", " ").title()

        self.client.files_upload_v2(
            channel=self.user_id,
            file=file_path,
            filename=filename,
            initial_comment=f"CSV for {campaign}",
            thread_ts=thread_ts,
        )
        logger.info("Uploaded %s to Slack", filename)
