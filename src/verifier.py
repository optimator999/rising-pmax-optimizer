"""Upload verification - checks if recommended changes were applied."""

import logging
from typing import Any, Dict, List, Optional

from database.queries import (
    get_latest_asset_records,
    update_asset_status,
)

logger = logging.getLogger("rising-pmax.verifier")


class UploadVerifier:
    """Verifies that recommended asset changes were uploaded to Google Ads."""

    def __init__(self, google_ads_collector, campaign_name: str, campaign_id: str):
        """Initialize verifier with a Google Ads collector and campaign info."""
        self.collector = google_ads_collector
        self.campaign_name = campaign_name
        self.campaign_id = campaign_id

    def get_current_asset_status(self) -> Dict[str, Dict[str, Any]]:
        """Query live Google Ads data and return dict keyed by asset text.

        Returns mapping of asset_text -> {status, impressions, etc.}
        """
        assets = self.collector.collect_for_campaign(
            campaign_name=self.campaign_name,
            campaign_id=self.campaign_id,
            lookback_days=7,
        )

        return {a["asset_text"]: a for a in assets}

    def compare_to_recommendations(
        self,
        live_data: Dict[str, Dict[str, Any]],
        flagged_assets: List[Dict[str, Any]],
        replacements: Dict[str, Dict[str, str]],
    ) -> Dict[str, Any]:
        """Check if recommended uploads happened.

        Returns verification report dict.
        """
        report = {
            "total_recommendations": len(flagged_assets),
            "paused_successfully": 0,
            "added_successfully": 0,
            "paused_failed": [],
            "added_failed": [],
            "manual_edits_detected": [],
        }

        for asset in flagged_assets:
            asset_text = asset.get("asset_text", "")
            asset_id = asset.get("asset_id", "")

            # Check if the flagged asset was paused
            if asset_text in live_data:
                # Still active - pause wasn't applied
                report["paused_failed"].append(asset_text)
            else:
                report["paused_successfully"] += 1

            # Check if the replacement was added
            replacement = replacements.get(asset_id)
            if replacement:
                replacement_text = replacement["text"]
                if replacement_text in live_data:
                    report["added_successfully"] += 1
                else:
                    # Check for manual edits (similar text present)
                    manual_edit = self._find_similar_asset(
                        replacement_text, live_data
                    )
                    if manual_edit:
                        report["manual_edits_detected"].append(
                            {
                                "expected": replacement_text,
                                "found": manual_edit,
                            }
                        )
                        report["added_successfully"] += 1
                    else:
                        report["added_failed"].append(replacement_text)

        return report

    def update_database(self, report: Dict[str, Any], flagged_assets: List[Dict]) -> None:
        """Update DynamoDB with verification results."""
        for asset in flagged_assets:
            asset_id = asset.get("asset_id", "")
            report_date = asset.get("report_date", "")
            asset_text = asset.get("asset_text", "")

            if asset_text not in report.get("paused_failed", []):
                # Successfully paused
                update_asset_status(
                    asset_id=asset_id,
                    report_date=report_date,
                    status="paused",
                    kill_reason=asset.get("kill_reason"),
                )

        logger.info(
            "Database updated: %d paused, %d added, %d manual edits",
            report["paused_successfully"],
            report["added_successfully"],
            len(report["manual_edits_detected"]),
        )

    def generate_verification_report(self, report: Dict[str, Any]) -> str:
        """Generate a human-readable verification report."""
        lines = [
            "📋 *Upload Verification Report*",
            "",
            f"Total recommendations: {report['total_recommendations']}",
            f"Paused successfully: {report['paused_successfully']}",
            f"Added successfully: {report['added_successfully']}",
        ]

        if report["paused_failed"]:
            lines.append("")
            lines.append("⚠️ *Not paused (still active):*")
            for text in report["paused_failed"]:
                lines.append(f"  - {text}")

        if report["added_failed"]:
            lines.append("")
            lines.append("⚠️ *Not added:*")
            for text in report["added_failed"]:
                lines.append(f"  - {text}")

        if report["manual_edits_detected"]:
            lines.append("")
            lines.append("✏️ *Manual edits detected:*")
            for edit in report["manual_edits_detected"]:
                lines.append(f"  - Expected: \"{edit['expected']}\"")
                lines.append(f"    Found: \"{edit['found']}\"")

        if not report["paused_failed"] and not report["added_failed"]:
            lines.append("")
            lines.append("✅ All changes applied successfully!")
        elif report["paused_failed"] or report["added_failed"]:
            lines.append("")
            lines.append(
                "⚠️ Some changes were not applied. "
                "Please review and upload manually."
            )

        return "\n".join(lines)

    def _find_similar_asset(
        self, expected_text: str, live_data: Dict[str, Dict[str, Any]]
    ) -> Optional[str]:
        """Find a live asset that's similar to expected text (manual edit detection).

        Uses simple word overlap to detect if user edited the replacement
        before uploading.
        """
        expected_words = set(expected_text.lower().split())
        if not expected_words:
            return None

        for live_text in live_data:
            live_words = set(live_text.lower().split())
            if not live_words:
                continue
            overlap = len(expected_words & live_words) / len(expected_words)
            if overlap >= 0.6:
                return live_text

        return None
