"""Google Ads Editor CSV file builder."""

import csv
import io
import logging
import os
import tempfile
from typing import Any, Dict, List

from utils.date_helpers import get_today_mountain

logger = logging.getLogger("rising-pmax.csv_builder")

CSV_HEADERS = [
    "Action",
    "Campaign",
    "Ad Group",
    "Asset Group",
    "Asset Type",
    "Asset Text",
    "Status",
    "Labels",
]

# Map internal types to Google Ads Editor format
TYPE_MAP = {
    "HEADLINE": "Headline",
    "LONG_HEADLINE": "Long headline",
    "DESCRIPTION": "Description",
}


class CSVBuilder:
    """Builds Google Ads Editor compatible CSV files."""

    def build_google_ads_csv(
        self,
        flagged_assets: List[Dict[str, Any]],
        replacements: Dict[str, Dict[str, str]],
        campaign_name: str,
        asset_group: str,
    ) -> List[List[str]]:
        """Generate rows for Google Ads Editor CSV.

        For each flagged asset:
        1. PAUSE row for the current underperformer
        2. ADD row for the replacement (if one was generated)

        Returns list of row lists (including header).
        """
        today = get_today_mountain().replace("-", "_")
        rows = [CSV_HEADERS]

        for asset in flagged_assets:
            asset_id = asset.get("asset_id", "")
            asset_type = TYPE_MAP.get(asset.get("asset_type", ""), "Headline")

            # PAUSE the underperformer
            rows.append(
                self._format_row(
                    action="PAUSE",
                    campaign=campaign_name,
                    asset_group=asset_group,
                    asset_type=asset_type,
                    text=asset.get("asset_text", ""),
                    status="PAUSED",
                    label=f"killed_{today}",
                )
            )

            # ADD the replacement (if available)
            replacement = replacements.get(asset_id)
            if replacement:
                rows.append(
                    self._format_row(
                        action="ADD",
                        campaign=campaign_name,
                        asset_group=asset_group,
                        asset_type=asset_type,
                        text=replacement["text"],
                        status="ENABLED",
                        label=f"added_{today}",
                    )
                )

        logger.info(
            "Built %d CSV rows for campaign '%s'",
            len(rows) - 1,
            campaign_name,
        )
        return rows

    def _format_row(
        self,
        action: str,
        campaign: str,
        asset_group: str,
        asset_type: str,
        text: str,
        status: str,
        label: str,
    ) -> List[str]:
        """Format a single CSV row."""
        return [
            action,
            campaign,
            "",  # Ad Group is empty for Performance Max
            asset_group,
            asset_type,
            text,
            status,
            label,
        ]

    def save_csv(
        self,
        rows: List[List[str]],
        campaign_slug: str,
        output_dir: str = "/tmp",
    ) -> str:
        """Write CSV rows to a file and return the file path.

        Filename format: {campaign_slug}_replacements_{YYYY_MM_DD}.csv
        """
        today = get_today_mountain().replace("-", "_")
        filename = f"{campaign_slug}_replacements_{today}.csv"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)

        logger.info("Saved CSV to %s (%d data rows)", filepath, len(rows) - 1)
        return filepath

    def rows_to_string(self, rows: List[List[str]]) -> str:
        """Convert rows to CSV string (for Slack upload from memory)."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        return output.getvalue()
