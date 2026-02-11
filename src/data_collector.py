"""Google Ads API client for collecting Performance Max asset data.

Uses the Google Ads REST API directly to avoid gRPC binary dependencies in Lambda.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests

from database.queries import generate_asset_id

logger = logging.getLogger("rising-pmax.collector")

GOOGLE_ADS_API_VERSION = "v23"
BASE_URL = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"
TOKEN_URL = "https://oauth2.googleapis.com/token"

CAMPAIGN_BUDGET_QUERY = """
SELECT
  campaign.id,
  campaign_budget.amount_micros
FROM campaign
WHERE campaign.id = {campaign_id}
"""

CAMPAIGN_COST_QUERY = """
SELECT
  campaign.id,
  segments.date,
  metrics.cost_micros,
  metrics.clicks,
  metrics.impressions
FROM campaign
WHERE
  campaign.id = {campaign_id}
  AND segments.date >= '{start_date}'
  AND segments.date <= '{end_date}'
"""

ASSET_QUERY_TEMPLATE = """
SELECT
  asset_group_asset.asset,
  asset_group_asset.field_type,
  asset_group_asset.status,
  asset.text_asset.text,
  asset.name,
  segments.date,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.conversions_value,
  metrics.cost_micros
FROM asset_group_asset
WHERE
  campaign.id = {campaign_id}
  AND segments.date >= '{start_date}'
  AND segments.date <= '{end_date}'
  AND asset_group_asset.field_type IN ('HEADLINE', 'DESCRIPTION', 'LONG_HEADLINE')
"""

FIELD_TYPE_MAP = {
    "HEADLINE": "HEADLINE",
    "DESCRIPTION": "DESCRIPTION",
    "LONG_HEADLINE": "LONG_HEADLINE",
}


class GoogleAdsCollector:
    """Collects asset performance data from Google Ads REST API."""

    def __init__(self, credentials: Dict[str, str]):
        self.customer_id = credentials["customer_id"]  # Manager (MCC) account
        self.client_customer_id = credentials.get(
            "client_customer_id", self.customer_id
        )  # Direct client account
        self.developer_token = credentials["developer_token"]
        self.client_id = credentials["client_id"]
        self.client_secret = credentials["client_secret"]
        self.refresh_token = credentials["refresh_token"]
        self._access_token = None
        logger.info(
            "Google Ads REST client initialized (manager: %s, client: %s)",
            self.customer_id,
            self.client_customer_id,
        )

    def _get_access_token(self) -> str:
        """Exchange refresh token for a fresh access token."""
        if self._access_token:
            return self._access_token

        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def _search(self, query: str) -> List[Dict[str, Any]]:
        """Execute a GAQL query via the REST API searchStream endpoint."""
        url = f"{BASE_URL}/customers/{self.client_customer_id}/googleAds:searchStream"
        headers = {
            "Authorization": f"Bearer {self._get_access_token()}",
            "developer-token": self.developer_token,
            "login-customer-id": self.customer_id,
            "Content-Type": "application/json",
        }
        body = {"query": query.strip()}

        resp = requests.post(url, headers=headers, json=body)

        if resp.status_code == 401:
            # Token expired, refresh and retry once
            self._access_token = None
            headers["Authorization"] = f"Bearer {self._get_access_token()}"
            resp = requests.post(url, headers=headers, json=body)

        if not resp.ok:
            error_detail = resp.text[:2000]
            logger.error(
                "Google Ads API error %d: %s", resp.status_code, error_detail
            )
            raise RuntimeError(
                f"Google Ads API {resp.status_code}: {error_detail}"
            )

        # searchStream returns a list of response chunks
        results = []
        for chunk in resp.json():
            for row in chunk.get("results", []):
                results.append(row)

        return results

    def get_asset_performance(
        self, campaign_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Query asset performance data for a campaign."""
        query = ASSET_QUERY_TEMPLATE.format(
            campaign_id=campaign_id,
            start_date=start_date,
            end_date=end_date,
        )

        rows = []
        try:
            raw_results = self._search(query)
            for row in raw_results:
                parsed = self._parse_row(row)
                if parsed:
                    rows.append(parsed)

            logger.info(
                "Collected %d asset-date rows for campaign %s", len(rows), campaign_id
            )

        except Exception as e:
            logger.error("Google Ads API error: %s", e)
            raise

        # Rate limit: max 1 request/sec per developer token
        time.sleep(1)
        return rows

    def _parse_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a REST API response row to a normalized dict."""
        try:
            aga = row.get("assetGroupAsset", {})
            asset = row.get("asset", {})
            metrics = row.get("metrics", {})
            segments = row.get("segments", {})

            field_type = aga.get("fieldType", "")
            field_type = FIELD_TYPE_MAP.get(field_type, field_type)

            # Skip non-text asset types
            text_asset = asset.get("textAsset", {})
            asset_text = text_asset.get("text", "")
            if not asset_text:
                return None

            cost_micros = int(metrics.get("costMicros", 0))

            return {
                "asset_resource": aga.get("asset", ""),
                "field_type": field_type,
                "asset_status": aga.get("status", ""),
                "asset_text": asset_text,
                "asset_name": asset.get("name", ""),
                "date": segments.get("date", ""),
                "impressions": int(metrics.get("impressions", 0)),
                "clicks": int(metrics.get("clicks", 0)),
                "conversions": float(metrics.get("conversions", 0)),
                "conversions_value": float(metrics.get("conversionsValue", 0)),
                "cost_micros": cost_micros,
                "cost": cost_micros / 1_000_000,
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to parse row: %s - %s", e, row)
            return None

    def get_campaign_metrics(self, campaign_id: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """Get campaign-level spend, clicks, impressions, and CTR for a date range."""
        query = CAMPAIGN_COST_QUERY.format(
            campaign_id=campaign_id,
            start_date=start_date,
            end_date=end_date,
        )
        try:
            results = self._search(query)
            total_cost_micros = sum(
                int(row.get("metrics", {}).get("costMicros", 0))
                for row in results
            )
            total_clicks = sum(
                int(row.get("metrics", {}).get("clicks", 0))
                for row in results
            )
            total_impressions = sum(
                int(row.get("metrics", {}).get("impressions", 0))
                for row in results
            )
            total_cost = total_cost_micros / 1_000_000
            ctr = round((total_clicks / total_impressions * 100) if total_impressions > 0 else 0.0, 2)

            logger.info(
                "Campaign %s metrics: $%.2f spend, %d clicks, %d impr, %.2f%% CTR (%s to %s)",
                campaign_id, total_cost, total_clicks, total_impressions, ctr,
                start_date, end_date,
            )
            time.sleep(1)
            return {
                "total_spend": total_cost,
                "clicks": total_clicks,
                "impressions": total_impressions,
                "ctr": ctr,
            }
        except Exception as e:
            logger.error("Failed to get campaign metrics: %s", e)
            raise

    def get_campaign_budget(self, campaign_id: str) -> float:
        """Get the actual daily budget for a campaign in dollars."""
        query = CAMPAIGN_BUDGET_QUERY.format(campaign_id=campaign_id)
        try:
            results = self._search(query)
            if results:
                budget = results[0].get("campaignBudget", {})
                amount_micros = int(budget.get("amountMicros", 0))
                daily_budget = amount_micros / 1_000_000
                logger.info(
                    "Campaign %s daily budget: $%.2f", campaign_id, daily_budget
                )
                return daily_budget
        except Exception as e:
            logger.error("Failed to get campaign budget: %s", e)
        return 0.0

    def collect_for_campaign(
        self,
        campaign_name: str,
        campaign_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """Collect, aggregate, and return asset data for one campaign."""
        raw_rows = self.get_asset_performance(campaign_id, start_date, end_date)

        # Aggregate by asset text
        aggregated: Dict[str, Dict[str, Any]] = {}

        for row in raw_rows:
            text = row["asset_text"]
            if text not in aggregated:
                aggregated[text] = {
                    "asset_id": generate_asset_id(text, campaign_name),
                    "asset_text": text,
                    "asset_type": row["field_type"],
                    "campaign_name": campaign_name,
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0.0,
                    "conversions_value": 0.0,
                    "cost": 0.0,
                    "status": "active",
                    "dates_seen": [],
                }
            agg = aggregated[text]
            agg["impressions"] += row["impressions"]
            agg["clicks"] += row["clicks"]
            agg["conversions"] += row["conversions"]
            agg["conversions_value"] += row["conversions_value"]
            agg["cost"] += row["cost"]
            agg["dates_seen"].append(row["date"])

        # Calculate derived metrics
        result = []
        for agg in aggregated.values():
            impr = agg["impressions"]
            agg["ctr"] = round((agg["clicks"] / impr * 100) if impr > 0 else 0.0, 2)
            agg["cpa"] = (
                round(agg["cost"] / agg["conversions"], 2)
                if agg["conversions"] > 0
                else 0.0
            )
            agg["date_added"] = min(agg["dates_seen"]) if agg["dates_seen"] else None
            del agg["dates_seen"]
            result.append(agg)

        logger.info(
            "Aggregated %d unique assets for campaign '%s'",
            len(result),
            campaign_name,
        )
        return result
