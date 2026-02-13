"""Campaign configuration management via S3 JSON.

Loads/saves campaign config from s3://rising-pmax/config/campaigns.json.
Combines manual strategy fields, Google Ads settings (auto-synced),
and image profiles into a single config file.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3

from config.settings import CAMPAIGNS as SETTINGS_CAMPAIGNS
from config.settings import S3_IMAGE_BUCKET

logger = logging.getLogger("rising-pmax.campaign-config")

S3_CONFIG_KEY = "config/campaigns.json"
SCHEMA_VERSION = 1
STALE_THRESHOLD_HOURS = 24


def load_config() -> Dict[str, Any]:
    """Load campaign config from S3.

    Falls back to building initial config from settings.py if the
    S3 file doesn't exist yet.
    """
    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=S3_IMAGE_BUCKET, Key=S3_CONFIG_KEY)
        config = json.loads(response["Body"].read().decode("utf-8"))
        logger.info("Loaded campaign config from s3://%s/%s", S3_IMAGE_BUCKET, S3_CONFIG_KEY)
        return config
    except s3.exceptions.NoSuchKey:
        logger.info("No config in S3, building initial config from settings.py")
        return _build_initial_config()
    except Exception as e:
        logger.warning("Failed to load config from S3: %s — falling back to settings.py", e)
        return _build_initial_config()


def save_config(config: Dict[str, Any]) -> None:
    """Write campaign config JSON to S3."""
    s3 = boto3.client("s3")
    config["last_synced_at"] = datetime.utcnow().isoformat() + "Z"
    body = json.dumps(config, indent=2, default=str)
    s3.put_object(
        Bucket=S3_IMAGE_BUCKET,
        Key=S3_CONFIG_KEY,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Saved campaign config to s3://%s/%s", S3_IMAGE_BUCKET, S3_CONFIG_KEY)


def sync_google_ads_settings(config: Dict[str, Any], collector) -> Dict[str, Any]:
    """Pull fresh Google Ads settings for each campaign and update config.

    Only overwrites the google_ads_settings section — manual and
    image_profile sections are preserved.
    """
    for campaign_name, campaign_data in config.get("campaigns", {}).items():
        campaign_id = campaign_data.get("campaign_id")
        if not campaign_id:
            logger.warning("No campaign_id for '%s', skipping sync", campaign_name)
            continue

        try:
            settings = collector.get_campaign_settings(campaign_id)
            campaign_data["google_ads_settings"] = settings
            logger.info("Synced Google Ads settings for '%s'", campaign_name)
        except Exception as e:
            logger.error("Failed to sync settings for '%s': %s", campaign_name, e)

    return config


def get_campaigns_dict(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Convert S3 config into the CAMPAIGNS dict format the rest of the codebase expects.

    Returns a dict matching the structure in config/settings.py:
    {
        "Campaign Name": {
            "campaign_id": "...",
            "asset_group": "...",
            "slug": "...",
            "image_profile": { ... },
        }
    }
    """
    campaigns = {}
    for campaign_name, data in config.get("campaigns", {}).items():
        campaigns[campaign_name] = {
            "campaign_id": data.get("campaign_id", ""),
            "asset_group": data.get("asset_group", campaign_name),
            "slug": data.get("slug", ""),
            "image_profile": data.get("image_profile", {}),
        }
    return campaigns


def is_stale(config: Dict[str, Any]) -> bool:
    """Check if config is older than STALE_THRESHOLD_HOURS."""
    last_synced = config.get("last_synced_at")
    if not last_synced:
        return True
    try:
        synced_dt = datetime.fromisoformat(last_synced.replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=synced_dt.tzinfo)
        return (now - synced_dt) > timedelta(hours=STALE_THRESHOLD_HOURS)
    except (ValueError, TypeError):
        return True


def load_campaigns_with_fallback(collector=None) -> Dict[str, Dict[str, Any]]:
    """Load campaigns from S3 config, auto-sync if stale, fallback to settings.py.

    Convenience function for Lambda handlers.
    """
    try:
        config = load_config()
        if collector and is_stale(config):
            logger.info("Config is stale (>%dh), syncing Google Ads settings", STALE_THRESHOLD_HOURS)
            config = sync_google_ads_settings(config, collector)
            save_config(config)
        return get_campaigns_dict(config)
    except Exception as e:
        logger.warning("Failed to load from S3 config: %s — using settings.py", e)
        return SETTINGS_CAMPAIGNS


_SEED_MANUAL = {
    "Core Brand": {
        "description": (
            "Core Brand is Rising's primary paid performance driver. The campaign "
            "promotes landing nets, tools, and hats to a broad fly fishing audience "
            "through Performance Max. The strategic goal is to profitably expand "
            "Rising's reach — growing awareness and sales while maintaining strong "
            "ROAS. Product mix and campaign structure are under active evaluation."
        ),
        "goal": "Profitably expand reach — grow sales while maintaining target ROAS",
        "target_audience": "Broad fly fishing audience — anglers of all experience levels",
        "key_products": ["landing nets", "tools", "hats"],
        "tone_notes": "Calm, honest, grounded. No hype. Speak like a crew around a tailgate or campfire.",
        "updated_by": "scott",
    },
    "Replacement Nets": {
        "description": (
            "Replacement Nets is a high-margin campaign that fills revenue gaps "
            "during low and shoulder seasons when Core Brand spend scales back. "
            "Replacement nets are a strong-selling product with consistent demand. "
            "The campaign runs through Performance Max targeting the same broad fly "
            "fishing audience, focused on maintaining profitable ROAS year-round."
        ),
        "goal": "Maintain profitable revenue during low and shoulder seasons",
        "target_audience": "Broad fly fishing audience — anglers of all experience levels",
        "key_products": ["replacement nets"],
        "tone_notes": "Calm, honest, grounded. No hype. Speak like a crew around a tailgate or campfire.",
        "updated_by": "scott",
    },
}


def _build_initial_config() -> Dict[str, Any]:
    """Seed config from settings.py CAMPAIGNS dict."""
    config = {
        "schema_version": SCHEMA_VERSION,
        "last_synced_at": None,
        "campaigns": {},
    }

    now = datetime.utcnow().isoformat() + "Z"

    for campaign_name, campaign_data in SETTINGS_CAMPAIGNS.items():
        seed = _SEED_MANUAL.get(campaign_name, {})
        config["campaigns"][campaign_name] = {
            "campaign_id": campaign_data.get("campaign_id", ""),
            "asset_group": campaign_data.get("asset_group", campaign_name),
            "slug": campaign_data.get("slug", ""),
            "manual": {
                "description": seed.get("description", ""),
                "goal": seed.get("goal", ""),
                "target_audience": seed.get("target_audience", ""),
                "key_products": seed.get("key_products", []),
                "tone_notes": seed.get("tone_notes", ""),
                "updated_at": now if seed else None,
                "updated_by": seed.get("updated_by"),
            },
            "google_ads_settings": {},
            "image_profile": campaign_data.get("image_profile", {}),
        }

    return config
