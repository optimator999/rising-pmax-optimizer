"""Lambda handler for image asset operations.

Supports actions: bootstrap, upload, gap_analysis, analyze.
Invoked manually or via future automation triggers.
"""

import logging
import traceback
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CAMPAIGNS, logger
from src.campaign_config import load_config, save_config, sync_google_ads_settings, get_campaigns_dict
from src.image_manager import ImageManager
from utils.aws_helpers import get_anthropic_api_key, get_google_ads_credentials


def lambda_handler(event, context):
    """Route image operations based on event action."""
    action = event.get("action", "")
    logger.info("Image ops invoked: action=%s", action)

    try:
        if action == "bootstrap":
            return _handle_bootstrap(event)
        elif action == "upload":
            return _handle_upload(event)
        elif action == "gap_analysis":
            return _handle_gap_analysis(event)
        elif action == "analyze":
            return _handle_analyze(event)
        elif action == "sync_config":
            return _handle_sync_config(event)
        else:
            return {
                "statusCode": 400,
                "body": {
                    "error": f"Unknown action: {action}",
                    "valid_actions": ["bootstrap", "upload", "gap_analysis", "analyze", "sync_config"],
                },
            }

    except Exception as e:
        logger.error("Image ops FAILED: %s", e)
        logger.error(traceback.format_exc())
        return {
            "statusCode": 500,
            "body": {"error": str(e)},
        }


def _handle_bootstrap(event):
    """Pull all images from Google Ads, analyze, and register."""
    campaigns = event.get("campaigns", list(CAMPAIGNS.keys()))

    anthropic_key = get_anthropic_api_key()
    google_creds = get_google_ads_credentials()

    from src.data_collector import GoogleAdsCollector
    collector = GoogleAdsCollector(google_creds)

    # Load full S3 config for campaign strategy context
    campaign_config = load_config()
    campaigns_dict = get_campaigns_dict(campaign_config)

    manager = ImageManager(
        anthropic_api_key=anthropic_key,
        google_ads_collector=collector,
        campaigns=campaigns_dict,
        campaign_config=campaign_config,
    )

    results = manager.bootstrap_from_google_ads(campaigns)

    return {
        "statusCode": 200,
        "body": {
            "action": "bootstrap",
            "results": results,
        },
    }


def _handle_upload(event):
    """Register an image already uploaded to S3."""
    s3_key = event.get("s3_key")
    if not s3_key:
        return {
            "statusCode": 400,
            "body": {"error": "s3_key is required"},
        }

    anthropic_key = get_anthropic_api_key()

    # Build campaign context if campaign_name provided
    campaign_context = None
    campaign_name = event.get("campaign_name")
    if campaign_name:
        campaign_config = load_config()
        manager = ImageManager(anthropic_api_key=anthropic_key, campaign_config=campaign_config)
        campaign_context = manager._get_campaign_context(campaign_name)
    else:
        manager = ImageManager(anthropic_api_key=anthropic_key)

    # Download from S3
    image_bytes = manager.download_from_s3(s3_key)
    content_type = "image/png" if s3_key.endswith(".png") else "image/jpeg"
    filename = s3_key.split("/")[-1]

    entry = manager.register_image(
        image_bytes=image_bytes,
        content_type=content_type,
        filename_original=filename,
        source="manual_upload",
        campaign_context=campaign_context,
    )

    return {
        "statusCode": 200,
        "body": {
            "action": "upload",
            "image_id": entry["image_id"],
            "content_category": entry.get("content_category"),
            "ai_description": entry.get("ai_description"),
            "eligible_slots": entry.get("eligible_slots"),
            "campaign_fit_score": entry.get("campaign_fit_score"),
            "campaign_fit_notes": entry.get("campaign_fit_notes"),
        },
    }


def _handle_gap_analysis(event):
    """Run composition gap analysis for one or more campaigns."""
    campaigns = event.get("campaigns", list(CAMPAIGNS.keys()))
    anthropic_key = get_anthropic_api_key()

    # Load full S3 config for campaign strategy context
    campaign_config = load_config()
    campaigns_dict = get_campaigns_dict(campaign_config)

    manager = ImageManager(
        anthropic_api_key=anthropic_key,
        campaigns=campaigns_dict,
        campaign_config=campaign_config,
    )

    results = {}
    for campaign_name in campaigns:
        analysis = manager.gap_analysis(campaign_name)
        results[campaign_name] = {
            "total_images": analysis["total_images"],
            "recommendations": analysis["recommendations"],
            "smart_recs": analysis.get("smart_recs", False),
            "composition": analysis["composition"],
            "formatted": manager.format_gap_analysis(analysis),
        }

    return {
        "statusCode": 200,
        "body": {
            "action": "gap_analysis",
            "results": results,
        },
    }


def _handle_sync_config(event):
    """Sync campaign config: load from S3, merge manual overrides, pull Google Ads settings, save."""
    from datetime import datetime

    google_creds = get_google_ads_credentials()
    from src.data_collector import GoogleAdsCollector
    collector = GoogleAdsCollector(google_creds)

    # Load existing config (or seed from settings.py)
    config = load_config()

    # Merge manual overrides if provided
    manual_overrides = event.get("manual_overrides", {})
    now = datetime.utcnow().isoformat() + "Z"
    for campaign_name, overrides in manual_overrides.items():
        if campaign_name in config.get("campaigns", {}):
            manual = config["campaigns"][campaign_name].setdefault("manual", {})
            manual.update(overrides)
            manual["updated_at"] = now
            manual.setdefault("updated_by", "api")
            logger.info("Applied manual overrides for '%s'", campaign_name)
        else:
            logger.warning("Unknown campaign in manual_overrides: %s", campaign_name)

    # Sync Google Ads settings
    config = sync_google_ads_settings(config, collector)

    # Save back to S3
    save_config(config)

    return {
        "statusCode": 200,
        "body": {
            "action": "sync_config",
            "campaigns_synced": list(config.get("campaigns", {}).keys()),
            "manual_overrides_applied": list(manual_overrides.keys()),
            "last_synced_at": config.get("last_synced_at"),
        },
    }


def _handle_analyze(event):
    """Re-analyze a specific image (e.g., after model upgrade)."""
    image_id = event.get("image_id")
    if not image_id:
        return {
            "statusCode": 400,
            "body": {"error": "image_id is required"},
        }

    from database.queries import get_image, save_image

    image = get_image(image_id)
    if not image:
        return {
            "statusCode": 404,
            "body": {"error": f"Image not found: {image_id}"},
        }

    anthropic_key = get_anthropic_api_key()

    # Build campaign context if campaign_name provided
    campaign_context = None
    campaign_name = event.get("campaign_name")
    if campaign_name:
        campaign_config = load_config()
        manager = ImageManager(anthropic_api_key=anthropic_key, campaign_config=campaign_config)
        campaign_context = manager._get_campaign_context(campaign_name)
    else:
        manager = ImageManager(anthropic_api_key=anthropic_key)

    # Download and re-analyze
    image_bytes = manager.download_from_s3(image["s3_key"])
    content_type = "image/png" if image["s3_key"].endswith(".png") else "image/jpeg"
    analysis = manager.analyze_image(image_bytes, content_type, campaign_context=campaign_context)

    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"

    # Update metadata fields
    crop_eligibility = analysis.pop("crop_eligibility", {})
    image["content_category"] = analysis.get("content_category")
    image["product_visible"] = analysis.get("product_visible")
    image["human_present"] = analysis.get("human_present")
    image["scene_type"] = analysis.get("scene_type")
    image["background_complexity"] = analysis.get("background_complexity")
    image["text_overlay"] = analysis.get("text_overlay")
    image["product_frame_ratio"] = analysis.get("product_frame_ratio")
    image["lighting"] = analysis.get("lighting")
    image["seasonal_relevance"] = analysis.get("seasonal_relevance", [])
    image["ai_description"] = analysis.get("description")
    image["ai_analysis_model"] = "claude-sonnet-4-5-20250929"
    image["ai_analyzed_at"] = now
    image["eligible_slots"] = crop_eligibility
    image["campaign_fit_score"] = analysis.get("campaign_fit_score")
    image["campaign_fit_notes"] = analysis.get("campaign_fit_notes")

    save_image(image)

    return {
        "statusCode": 200,
        "body": {
            "action": "analyze",
            "image_id": image_id,
            "content_category": image["content_category"],
            "ai_description": image["ai_description"],
            "campaign_fit_score": image.get("campaign_fit_score"),
            "campaign_fit_notes": image.get("campaign_fit_notes"),
        },
    }
