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
        budget_data: Optional[Dict[str, Any]] = None,
        emergency_alerts: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send the full weekly review as a Slack DM.

        Returns True on success, False on failure.
        """
        try:
            message = self._format_review_message(
                month, flagged_assets, replacements, budget_data, emergency_alerts
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
        budget_data: Optional[Dict[str, Any]] = None,
        emergency_alerts: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build the full weekly review Slack message."""
        today = format_date(get_today_mountain())
        season = get_season_name(month)
        demand = get_monthly_demand(month)

        lines = [
            f"📊 *Weekly Asset Review - {today}*",
            "",
            f"Season: {season.replace('_', ' ').title()} ({demand}% annual demand)",
        ]

        # Budget section
        if budget_data:
            lines.extend(self._format_budget_section(budget_data, season))

        # Emergency alerts
        if emergency_alerts:
            lines.append("")
            lines.append("━" * 35)
            lines.append("🚨 *EMERGENCY ALERTS*")
            lines.append("━" * 35)
            for alert in emergency_alerts:
                lines.append(f"*{alert['title']}*: {alert['message']}")

        lines.append("")
        lines.append("━" * 35)
        lines.append("🎯 *ASSET PERFORMANCE*")
        lines.append("━" * 35)
        lines.append("")

        if not flagged_assets:
            lines.append("No assets flagged for replacement this week.")
        else:
            total_cost = sum(float(a.get("cost", 0)) for a in flagged_assets)
            lines.append(f"*{len(flagged_assets)} assets flagged for replacement*")
            lines.append(f"Total cost on flagged assets: ${total_cost:.2f}")
            lines.append("")

            for i, asset in enumerate(flagged_assets, 1):
                asset_id = asset.get("asset_id", "")
                replacement = replacements.get(asset_id)

                lines.append(
                    f"❌ *ASSET {i}:* {asset.get('asset_text', '')}"
                )
                lines.append(
                    f"Campaign: {asset.get('campaign_name', '')} | "
                    f"Type: {asset.get('asset_type', '')}"
                )
                lines.append(
                    f"Stats: {asset.get('impressions', 0)} impr | "
                    f"{asset.get('ctr', 0)}% CTR | "
                    f"${float(asset.get('cost', 0)):.2f} spent"
                )
                lines.append(f"Kill reason: {asset.get('kill_reason', '')}")

                if replacement:
                    lines.append(f"✅ *REPLACEMENT:* {replacement['text']}")
                    lines.append(f"Strategy: {replacement.get('strategy', '')}")
                else:
                    lines.append("⚠️ No replacement generated (Claude API issue)")

                lines.append("")
                lines.append("━" * 35)
                lines.append("")

            lines.append(f"📎 CSV file(s) attached below.")
            lines.append("Import into Google Ads Editor to apply changes.")
            lines.append("Review within 3 days for tracking.")

        return "\n".join(lines)

    def _format_budget_section(
        self, budget_data: Dict[str, Any], season: str
    ) -> List[str]:
        """Format the budget performance section of the message."""
        lookback_start = budget_data.get("lookback_start", "")
        lookback_end = budget_data.get("lookback_end", "")
        lookback_days = budget_data.get("lookback_days", "")

        lines = [
            "",
            "━" * 35,
            "💰 *BUDGET PERFORMANCE*",
            "━" * 35,
            "",
            f"Period: {lookback_start} to {lookback_end} ({lookback_days} days)",
            "",
            f"Current budget: ${float(budget_data.get('daily_budget_target', 0)):.0f}/day",
            f"Actual spend: ${float(budget_data.get('actual_daily_spend_avg', 0)):.2f}/day "
            f"({float(budget_data.get('budget_utilization_percent', 0)):.1f}% utilization)",
            f"Total spend: ${float(budget_data.get('total_spend', 0)):.2f}",
            f"Campaign CTR: {float(budget_data.get('campaign_ctr', 0)):.2f}% "
            f"({budget_data.get('campaign_clicks', 0):,} clicks / "
            f"{budget_data.get('campaign_impressions', 0):,} impr)",
            f"Revenue (Shopify): ${float(budget_data.get('total_revenue', 0)):.2f}",
            f"Shopify orders (Google-attributed): {budget_data.get('shopify_orders', 0)}",
            f"Google share of Shopify revenue: {float(budget_data.get('shopify_google_share_pct', 0)):.1f}%",
            f"ROAS (Shopify): {float(budget_data.get('roas_percent', 0)):.0f}%",
            "",
            f"Target ROAS: {float(budget_data.get('target_roas_percent', 0)):.0f}%",
        ]

        rec = budget_data.get("recommendation", "hold").upper()
        reason = budget_data.get("recommendation_reason", "")
        rec_budget = budget_data.get("recommended_daily_budget")

        lines.append("")
        lines.append(f"📈 *RECOMMENDATION: {rec}*")
        lines.append(reason)
        if rec_budget and rec != "HOLD":
            lines.append(f"Recommended budget: ${float(rec_budget):.0f}/day")

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
