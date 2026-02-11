"""Common DynamoDB query patterns for Rising PMax Optimizer."""

import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr, Key

from config.settings import get_dynamodb_resource

logger = logging.getLogger("rising-pmax.queries")


def _get_table(table_name: str):
    return get_dynamodb_resource().Table(table_name)


def generate_asset_id(asset_text: str, campaign_name: str) -> str:
    """Generate deterministic asset ID from text + campaign."""
    raw = f"{asset_text}|{campaign_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# --- Asset Performance ---


def save_asset_performance(asset: Dict[str, Any]) -> None:
    """Save or update an asset performance record."""
    table = _get_table("rising_asset_performance")
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "asset_id": asset["asset_id"],
        "report_date": asset["report_date"],
        "asset_text": asset["asset_text"],
        "asset_type": asset["asset_type"],
        "campaign_name": asset["campaign_name"],
        "impressions": Decimal(str(asset.get("impressions", 0))),
        "clicks": Decimal(str(asset.get("clicks", 0))),
        "ctr": Decimal(str(asset.get("ctr", 0.0))),
        "conversions": Decimal(str(asset.get("conversions", 0.0))),
        "cost": Decimal(str(asset.get("cost", 0.0))),
        "cpa": Decimal(str(asset.get("cpa", 0.0))),
        "status": asset.get("status", "active"),
        "date_added": asset.get("date_added"),
        "date_killed": asset.get("date_killed"),
        "kill_reason": asset.get("kill_reason"),
        "replacement_reason": asset.get("replacement_reason"),
        "replaced_by": asset.get("replaced_by"),
        "replaces": asset.get("replaces"),
        "approval_status": asset.get("approval_status"),
        "approval_date": asset.get("approval_date"),
        "upload_status": asset.get("upload_status"),
        "google_ads_asset_id": asset.get("google_ads_asset_id"),
        "updated_at": now,
    }

    # Set created_at only on first write
    if "created_at" not in asset:
        item["created_at"] = now

    # Remove None values (DynamoDB doesn't accept None)
    item = {k: v for k, v in item.items() if v is not None}

    table.put_item(Item=item)
    logger.debug("Saved asset %s for %s", asset["asset_id"], asset["report_date"])


def get_asset_history(
    asset_id: str, start_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get performance history for a single asset."""
    table = _get_table("rising_asset_performance")

    key_condition = Key("asset_id").eq(asset_id)
    if start_date:
        key_condition = key_condition & Key("report_date").gte(start_date)

    response = table.query(KeyConditionExpression=key_condition)
    return response.get("Items", [])


def get_active_assets(campaign_name: str) -> List[Dict[str, Any]]:
    """Get all active assets for a campaign using GSI."""
    table = _get_table("rising_asset_performance")

    response = table.query(
        IndexName="campaign-status-index",
        KeyConditionExpression=(
            Key("campaign_name").eq(campaign_name) & Key("status").eq("active")
        ),
    )
    return response.get("Items", [])


def get_latest_asset_records(campaign_name: str) -> List[Dict[str, Any]]:
    """Get the most recent record for each asset in a campaign.

    Scans for all items matching the campaign, then deduplicates
    by asset_id keeping the latest report_date.
    """
    table = _get_table("rising_asset_performance")

    response = table.scan(
        FilterExpression=Attr("campaign_name").eq(campaign_name),
    )
    items = response.get("Items", [])

    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression=Attr("campaign_name").eq(campaign_name),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    # Deduplicate: keep latest report_date per asset_id
    latest: Dict[str, Dict[str, Any]] = {}
    for item in items:
        aid = item["asset_id"]
        if aid not in latest or item["report_date"] > latest[aid]["report_date"]:
            latest[aid] = item

    return list(latest.values())


def update_asset_status(
    asset_id: str,
    report_date: str,
    status: str,
    kill_reason: Optional[str] = None,
    replaced_by: Optional[str] = None,
) -> None:
    """Update the status of an asset record."""
    table = _get_table("rising_asset_performance")
    now = datetime.utcnow().isoformat() + "Z"

    update_expr = "SET #s = :status, updated_at = :now"
    expr_values: Dict[str, Any] = {":status": status, ":now": now}
    expr_names = {"#s": "status"}

    if kill_reason:
        update_expr += ", kill_reason = :kr"
        expr_values[":kr"] = kill_reason
    if replaced_by:
        update_expr += ", replaced_by = :rb"
        expr_values[":rb"] = replaced_by
    if status in ("killed", "paused"):
        update_expr += ", date_killed = :dk"
        expr_values[":dk"] = now[:10]

    table.update_item(
        Key={"asset_id": asset_id, "report_date": report_date},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )
    logger.info("Updated asset %s to status %s", asset_id, status)


# --- Graveyard ---


def save_to_graveyard(asset: Dict[str, Any]) -> None:
    """Save a killed/paused asset to the graveyard for learning."""
    table = _get_table("rising_asset_graveyard")
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "campaign_name": asset["campaign_name"],
        "date_killed": asset.get("date_killed", now[:10]),
        "asset_id": asset["asset_id"],
        "asset_text": asset["asset_text"],
        "asset_type": asset["asset_type"],
        "impressions": Decimal(str(asset.get("impressions", 0))),
        "clicks": Decimal(str(asset.get("clicks", 0))),
        "ctr": Decimal(str(asset.get("ctr", 0.0))),
        "conversions": Decimal(str(asset.get("conversions", 0.0))),
        "cost": Decimal(str(asset.get("cost", 0.0))),
        "kill_reason": asset.get("kill_reason", "unknown"),
        "created_at": now,
    }

    table.put_item(Item=item)
    logger.info("Saved asset '%s' to graveyard", asset["asset_text"])


def get_graveyard_assets(campaign_name: str) -> List[Dict[str, Any]]:
    """Get all killed assets for a campaign (for learning)."""
    table = _get_table("rising_asset_graveyard")

    response = table.query(
        KeyConditionExpression=Key("campaign_name").eq(campaign_name),
    )
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("campaign_name").eq(campaign_name),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    return items


# --- Budget Performance ---


def save_budget_performance(data: Dict[str, Any]) -> None:
    """Save weekly budget performance record."""
    table = _get_table("rising_budget_performance")
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "campaign_name": data["campaign_name"],
        "week_ending": data["week_ending"],
        "week_starting": data.get("week_starting"),
        "season": data.get("season"),
        "daily_budget_target": Decimal(str(data.get("daily_budget_target", 0))),
        "actual_daily_spend_avg": Decimal(
            str(data.get("actual_daily_spend_avg", 0))
        ),
        "total_spend": Decimal(str(data.get("total_spend", 0))),
        "total_revenue": Decimal(str(data.get("total_revenue", 0))),
        "conversions": Decimal(str(data.get("conversions", 0))),
        "roas_percent": Decimal(str(data.get("roas_percent", 0))),
        "target_roas_percent": Decimal(str(data.get("target_roas_percent", 0))),
        "budget_utilization_percent": Decimal(
            str(data.get("budget_utilization_percent", 0))
        ),
        "recommendation": data.get("recommendation"),
        "recommended_daily_budget": Decimal(
            str(data.get("recommended_daily_budget", 0))
        ),
        "recommendation_reason": data.get("recommendation_reason"),
        "market_ceiling_detected": data.get("market_ceiling_detected", False),
        "created_at": now,
    }

    item = {k: v for k, v in item.items() if v is not None}
    table.put_item(Item=item)
    logger.info(
        "Saved budget performance for %s week ending %s",
        data["campaign_name"],
        data["week_ending"],
    )


def get_budget_history(
    campaign_name: str, weeks: int = 8
) -> List[Dict[str, Any]]:
    """Get recent budget performance history."""
    table = _get_table("rising_budget_performance")

    response = table.query(
        KeyConditionExpression=Key("campaign_name").eq(campaign_name),
        ScanIndexForward=False,
        Limit=weeks,
    )
    return response.get("Items", [])
