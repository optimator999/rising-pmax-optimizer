"""Image asset management for Rising PMax Optimizer.

Handles S3 storage, Claude Vision analysis, image registry operations,
gap analysis, and bootstrapping from Google Ads.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3
import requests

from config.settings import CAMPAIGNS, S3_IMAGE_BUCKET
from database.queries import (
    get_all_images,
    get_image,
    get_images_for_campaign,
    save_image,
)

logger = logging.getLogger("rising-pmax.image-manager")

CONTENT_CATEGORIES = [
    "product_hero",
    "product_detail",
    "product_in_use",
    "lifestyle_with_product",
    "lifestyle_no_product",
]

IMAGE_FIELD_TYPES = {
    "MARKETING_IMAGE",
    "SQUARE_MARKETING_IMAGE",
    "PORTRAIT_MARKETING_IMAGE",
}



ASSET_GROUP_QUERY = """
SELECT
  asset_group.id,
  asset_group.name,
  asset_group.resource_name,
  asset_group.status
FROM asset_group
WHERE campaign.resource_name = '{campaign_resource}'
"""

IMAGE_ASSET_QUERY = """
SELECT
  asset_group_asset.asset,
  asset_group_asset.field_type,
  asset.id,
  asset.name,
  asset.image_asset.full_size.url,
  asset.image_asset.full_size.width_pixels,
  asset.image_asset.full_size.height_pixels,
  asset.image_asset.file_size
FROM asset_group_asset
WHERE asset_group.resource_name = '{asset_group_resource}'
  AND asset.type = 'IMAGE'
  AND asset_group_asset.status = 'ENABLED'
  AND asset_group_asset.field_type IN (
    'MARKETING_IMAGE', 'SQUARE_MARKETING_IMAGE', 'PORTRAIT_MARKETING_IMAGE'
  )
"""

VISION_ANALYSIS_PROMPT = """Analyze this image for a fly fishing brand's Google Ads Performance Max campaign.
Return ONLY a JSON object with exactly these fields (no markdown, no explanation):

{
  "content_category": one of "product_hero", "product_detail", "product_in_use",
                      "lifestyle_with_product", "lifestyle_no_product",
  "product_visible": true/false,
  "human_present": true/false,
  "scene_type": one of "river", "lake", "workshop", "studio", "outdoor_other",
  "background_complexity": one of "simple", "moderate", "complex",
  "text_overlay": true/false,
  "product_frame_ratio": one of "tight", "medium", "wide", "none",
  "lighting": one of "natural_outdoor", "studio", "warm", "cool",
  "seasonal_relevance": list from ["spring", "summer", "fall", "winter", "all_season"],
  "description": one sentence describing the image content,
  "crop_eligibility": {
    "MARKETING_IMAGE": "native" or "crop_viable" or "not_recommended",
    "SQUARE_MARKETING_IMAGE": "native" or "crop_viable" or "not_recommended",
    "PORTRAIT_MARKETING_IMAGE": "native" or "crop_viable" or "not_recommended"
  }
}

The product is a handcrafted fly fishing landing net made from wood.
If a net-shaped object is visible, product_visible is true.

For content_category, follow this priority:
1. Image shows specs, dimensions, color swatches, feature callouts, or text overlays describing the product -> product_detail
2. Close-up of materials, craftsmanship, or construction details -> product_detail
3. Product is main subject on simple background with no text or specs -> product_hero
4. Person actively using the product -> product_in_use
5. Product visible in a broader scene -> lifestyle_with_product
6. No product visible -> lifestyle_no_product

For crop_eligibility, assess whether the image can be cropped to each aspect ratio
(landscape 1.91:1, square 1:1, portrait 4:5) while keeping the main subject
visible and well-composed. Use "native" if the image already matches that ratio."""

CAMPAIGN_CONTEXT_ADDENDUM = """

Additionally, evaluate this image in the context of the following campaign strategy:

Campaign: {campaign_name}
Description: {description}
Goal: {goal}
Target Audience: {target_audience}
Key Products: {key_products}
Tone: {tone_notes}

Add these two fields to your JSON response:
- "campaign_fit_score": integer 1-5 rating how well this image fits the campaign's strategy, audience, and tone (5 = perfect fit, 1 = poor fit)
- "campaign_fit_notes": one sentence explaining the score"""

SMART_RECS_PROMPT = """You are an image strategy advisor for a fly fishing brand's Google Ads Performance Max campaigns.

Campaign context:
- Name: {campaign_name}
- Description: {description}
- Goal: {goal}
- Target Audience: {target_audience}
- Key Products: {key_products}
- Tone: {tone_notes}

Current image composition gaps (categories that are underrepresented):
{gap_data}

For each underrepresented category, write ONE specific, actionable recommendation for what image to create or source. Incorporate the campaign's audience, products, and tone into each recommendation.

Return ONLY a JSON array of strings, one recommendation per underrepresented category. No markdown, no explanation.
Example: ["Shoot a close-up of the walnut net handle grain ...","Capture an angler mid-release on a river ..."]"""


class ImageManager:
    """Manages image assets in S3 and the image registry."""

    def __init__(self, anthropic_api_key: str, google_ads_collector=None, campaigns=None, campaign_config=None):
        self.s3 = boto3.client("s3")
        self.bucket = S3_IMAGE_BUCKET
        self.anthropic_key = anthropic_api_key
        self.collector = google_ads_collector
        self.campaigns = campaigns if campaigns is not None else CAMPAIGNS
        self.campaign_config = campaign_config
        logger.info("ImageManager initialized (bucket: %s)", self.bucket)

    # --- S3 Operations ---

    def upload_to_s3(
        self, image_bytes: bytes, image_id: str, content_type: str = "image/jpeg"
    ) -> str:
        """Upload image bytes to S3. Returns the S3 key."""
        ext = "png" if "png" in content_type else "jpg"
        s3_key = f"images/{image_id}.{ext}"

        self.s3.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body=image_bytes,
            ContentType=content_type,
            Metadata={
                "image_id": image_id,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        logger.info("Uploaded %s to s3://%s/%s", image_id, self.bucket, s3_key)
        return s3_key

    def download_from_s3(self, s3_key: str) -> bytes:
        """Download image bytes from S3."""
        response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
        return response["Body"].read()

    # --- Claude Vision Analysis ---

    def analyze_image(
        self, image_bytes: bytes, content_type: str = "image/jpeg", campaign_context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Analyze an image using Claude Vision. Returns structured metadata.

        When campaign_context is provided, also returns campaign_fit_score and
        campaign_fit_notes fields.
        """
        import base64

        media_type = content_type if content_type in ("image/jpeg", "image/png", "image/webp", "image/gif") else "image/jpeg"
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        prompt = VISION_ANALYSIS_PROMPT
        if campaign_context:
            prompt += CAMPAIGN_CONTEXT_ADDENDUM.format(
                campaign_name=campaign_context.get("campaign_name", ""),
                description=campaign_context.get("description", ""),
                goal=campaign_context.get("goal", ""),
                target_audience=campaign_context.get("target_audience", ""),
                key_products=", ".join(campaign_context.get("key_products", [])),
                tone_notes=campaign_context.get("tone_notes", ""),
            )

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            },
        )

        if not response.ok:
            logger.error("Claude Vision API error %d: %s", response.status_code, response.text[:500])
            raise RuntimeError(f"Claude Vision API {response.status_code}: {response.text[:500]}")

        result = response.json()
        text = result["content"][0]["text"]

        # Parse JSON from response (handle potential markdown wrapping)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        analysis = json.loads(text)
        logger.info("Image analyzed: category=%s, product=%s", analysis.get("content_category"), analysis.get("product_visible"))
        return analysis

    # --- Registration ---

    def register_image(
        self,
        image_bytes: bytes,
        content_type: str = "image/jpeg",
        filename_original: str = "",
        source: str = "manual_upload",
        google_ads_mapping: Optional[Dict[str, str]] = None,
        campaign_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Full pipeline: upload to S3, analyze with AI, save to registry.

        Returns the complete registry entry.
        """
        image_id = str(uuid.uuid4())[:8]
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        # Check for duplicate by hash
        existing = self._find_by_hash(image_hash)
        if existing:
            logger.info("Duplicate image detected (hash match), reusing %s", existing["image_id"])
            if google_ads_mapping:
                self._add_google_ads_mapping(existing, google_ads_mapping)
            return existing

        # Upload to S3
        s3_key = self.upload_to_s3(image_bytes, image_id, content_type)

        # Get image dimensions
        width, height = self._get_dimensions(image_bytes)
        aspect_ratio = self._classify_aspect_ratio(width, height)

        # Analyze with Claude Vision
        analysis = self.analyze_image(image_bytes, content_type, campaign_context=campaign_context)
        crop_eligibility = analysis.pop("crop_eligibility", {})

        # Mark native slot
        native_slot = {
            "landscape": "MARKETING_IMAGE",
            "square": "SQUARE_MARKETING_IMAGE",
            "portrait": "PORTRAIT_MARKETING_IMAGE",
        }.get(aspect_ratio)
        if native_slot and native_slot in crop_eligibility:
            crop_eligibility[native_slot] = "native"

        # Build registry entry
        now = datetime.utcnow().isoformat() + "Z"
        entry = {
            "image_id": image_id,
            "s3_key": s3_key,
            "filename_original": filename_original,
            "source": source,
            "image_hash": image_hash,
            "native_aspect_ratio": aspect_ratio,
            "width_px": width,
            "height_px": height,
            "file_size_bytes": len(image_bytes),
            # AI metadata
            "content_category": analysis.get("content_category"),
            "product_visible": analysis.get("product_visible"),
            "human_present": analysis.get("human_present"),
            "scene_type": analysis.get("scene_type"),
            "background_complexity": analysis.get("background_complexity"),
            "text_overlay": analysis.get("text_overlay"),
            "product_frame_ratio": analysis.get("product_frame_ratio"),
            "lighting": analysis.get("lighting"),
            "seasonal_relevance": analysis.get("seasonal_relevance", []),
            "ai_description": analysis.get("description"),
            "ai_analysis_model": "claude-sonnet-4-5-20250929",
            "ai_analyzed_at": now,
            # Campaign fit (present when analyzed with campaign context)
            "campaign_fit_score": analysis.get("campaign_fit_score"),
            "campaign_fit_notes": analysis.get("campaign_fit_notes"),
            # Crop eligibility
            "eligible_slots": crop_eligibility,
            # Google Ads mapping
            "google_ads_assets": [],
            # Lifecycle
            "status": "available",
            "created_at": now,
        }

        if google_ads_mapping:
            google_ads_mapping["date_linked"] = now
            entry["google_ads_assets"] = [google_ads_mapping]
            entry["status"] = "in_use"

        save_image(entry)
        logger.info("Registered image %s: %s (%s)", image_id, analysis.get("content_category"), aspect_ratio)
        return entry

    # --- Bootstrap from Google Ads ---

    def bootstrap_from_google_ads(self, campaigns: Optional[List[str]] = None) -> Dict[str, Any]:
        """Pull all image assets from Google Ads, analyze, and register.

        Queries by asset group (not campaign) with status=ENABLED so each
        source image is returned once, not once per format crop.

        Returns summary of bootstrap results.
        """
        if not self.collector:
            raise RuntimeError("GoogleAdsCollector required for bootstrap")

        campaigns = campaigns or list(self.campaigns.keys())
        results = {"total": 0, "new": 0, "duplicate": 0, "errors": 0, "by_campaign": {}}

        for campaign_name in campaigns:
            config = self.campaigns.get(campaign_name)
            if not config:
                logger.warning("Unknown campaign: %s", campaign_name)
                continue

            campaign_id = config["campaign_id"]
            logger.info("Bootstrapping images for campaign: %s", campaign_name)

            campaign_results = {"new": 0, "duplicate": 0, "errors": 0}

            try:
                # Step 1: Get enabled asset groups for this campaign
                customer_id = self.collector.client_customer_id
                campaign_resource = f"customers/{customer_id}/campaigns/{campaign_id}"
                ag_rows = self.collector._search(
                    ASSET_GROUP_QUERY.format(campaign_resource=campaign_resource)
                )
                enabled_groups = [
                    r for r in ag_rows
                    if r.get("assetGroup", {}).get("status") == "ENABLED"
                ]
                logger.info(
                    "Found %d enabled asset groups for %s",
                    len(enabled_groups), campaign_name,
                )

                # Step 2: For each enabled asset group, get ENABLED images
                live_asset_resources = set()
                for ag in enabled_groups:
                    ag_resource = ag["assetGroup"]["resourceName"]
                    ag_name = ag["assetGroup"]["name"]
                    raw_rows = self.collector._search(
                        IMAGE_ASSET_QUERY.format(asset_group_resource=ag_resource)
                    )
                    logger.info(
                        "Found %d ENABLED image assets in asset group '%s'",
                        len(raw_rows), ag_name,
                    )

                    for row in raw_rows:
                        results["total"] += 1
                        asset_resource = row.get("assetGroupAsset", {}).get("asset", "")
                        if asset_resource:
                            live_asset_resources.add(asset_resource)
                        try:
                            entry = self._process_bootstrap_row(row, campaign_name, ag_name)
                            if entry.get("_was_duplicate"):
                                campaign_results["duplicate"] += 1
                                results["duplicate"] += 1
                            else:
                                campaign_results["new"] += 1
                                results["new"] += 1
                        except Exception as e:
                            logger.error("Failed to process image asset: %s", e)
                            campaign_results["errors"] += 1
                            results["errors"] += 1

                # Reconcile: unlink images no longer in Google Ads
                unlinked = self._reconcile_campaign_mappings(campaign_name, live_asset_resources)
                campaign_results["unlinked"] = unlinked

            except Exception as e:
                logger.error("Failed to query images for %s: %s", campaign_name, e)
                campaign_results["errors"] += 1
                results["errors"] += 1

            results["by_campaign"][campaign_name] = campaign_results

        logger.info(
            "Bootstrap complete: %d total, %d new, %d duplicate, %d errors",
            results["total"], results["new"], results["duplicate"], results["errors"],
        )
        return results

    def _get_campaign_context(self, campaign_name: str) -> Optional[Dict[str, str]]:
        """Extract manual strategy section from campaign_config for use in analysis.

        Returns None if campaign_config is unavailable or manual section is empty.
        """
        if not self.campaign_config:
            return None

        campaign_data = self.campaign_config.get("campaigns", {}).get(campaign_name, {})
        manual = campaign_data.get("manual", {})

        if not manual or not manual.get("description"):
            return None

        return {
            "campaign_name": campaign_name,
            "description": manual.get("description", ""),
            "goal": manual.get("goal", ""),
            "target_audience": manual.get("target_audience", ""),
            "key_products": manual.get("key_products", []),
            "tone_notes": manual.get("tone_notes", ""),
        }

    def _process_bootstrap_row(
        self, row: Dict[str, Any], campaign_name: str, asset_group: str
    ) -> Dict[str, Any]:
        """Process a single Google Ads image asset row during bootstrap."""
        aga = row.get("assetGroupAsset", {})
        asset = row.get("asset", {})
        image_asset = asset.get("imageAsset", {})
        full_size = image_asset.get("fullSize", {})

        asset_resource = aga.get("asset", "")
        asset_name = asset.get("name", "")
        field_type = aga.get("fieldType", "")
        image_url = full_size.get("url", "")
        width = int(full_size.get("widthPixels", 0))
        height = int(full_size.get("heightPixels", 0))

        if not image_url:
            raise ValueError(f"No image URL for asset {asset_resource}")

        # Download the image
        resp = requests.get(image_url)
        resp.raise_for_status()
        image_bytes = resp.content
        content_type = resp.headers.get("content-type", "image/jpeg")

        # Check for duplicate
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        existing = self._find_by_hash(image_hash)

        google_ads_mapping = {
            "asset_resource": asset_resource,
            "campaign_name": campaign_name,
            "asset_group": asset_group,
            "field_type": field_type,
            "asset_name": asset_name,
            "image_url": image_url,
        }

        if existing:
            self._add_google_ads_mapping(existing, google_ads_mapping)
            existing["_was_duplicate"] = True
            return existing

        campaign_context = self._get_campaign_context(campaign_name)

        entry = self.register_image(
            image_bytes=image_bytes,
            content_type=content_type,
            filename_original=asset_name,
            source="google_ads_bootstrap",
            google_ads_mapping=google_ads_mapping,
            campaign_context=campaign_context,
        )
        entry["_was_duplicate"] = False
        return entry

    # --- Gap Analysis ---

    def gap_analysis(self, campaign_name: str) -> Dict[str, Any]:
        """Analyze image composition gaps for a campaign.

        Returns composition table, priority list, and recommendations.
        """
        config = self.campaigns.get(campaign_name)
        if not config or "image_profile" not in config:
            raise ValueError(f"No image profile for campaign: {campaign_name}")

        profile = config["image_profile"]
        images = get_images_for_campaign(campaign_name)
        total = len(images)

        # Count by category
        category_counts = {cat: 0 for cat in CONTENT_CATEGORIES}
        for image in images:
            cat = image.get("content_category", "")
            if cat in category_counts:
                category_counts[cat] += 1

        # Calculate gaps
        composition = {}
        for category in CONTENT_CATEGORIES:
            actual_pct = (category_counts[category] / total * 100) if total > 0 else 0
            target_pct = profile.get(category, 0) * 100
            delta = target_pct - actual_pct

            composition[category] = {
                "count": category_counts[category],
                "actual_pct": round(actual_pct, 1),
                "target_pct": round(target_pct, 1),
                "delta": round(delta, 1),
                "status": "over" if delta < -5 else "under" if delta > 5 else "on_target",
            }

        # Priority: largest positive delta = most underrepresented
        priority = sorted(
            composition.items(),
            key=lambda x: x[1]["delta"],
            reverse=True,
        )

        # Build recommendations
        smart_recs = False
        campaign_context = self._get_campaign_context(campaign_name) if self.campaign_config else None
        under_gaps = [(cat, data) for cat, data in priority if data["status"] == "under"]

        if campaign_context and under_gaps:
            try:
                recommendations = self._generate_smart_recommendations(campaign_context, under_gaps)
                smart_recs = True
            except Exception as e:
                logger.warning("Smart recommendations failed, falling back to generic: %s", e)
                recommendations = None

        if not smart_recs:
            recommendations = []
            for category, data in priority:
                if data["status"] == "under":
                    suggested = max(1, round(data["delta"] / 100 * max(total, 5)))
                    recommendations.append(
                        f"Upload {suggested} {category.replace('_', ' ')} image(s)"
                    )

        # Find available (not in use) images that could fill gaps
        all_images = get_all_images()
        candidates = {}
        under_categories = [cat for cat, d in priority if d["status"] == "under"]
        for cat in under_categories:
            matches = [
                img for img in all_images
                if img.get("content_category") == cat
                and img.get("status") == "available"
                and not any(
                    m.get("campaign_name") == campaign_name and not m.get("date_unlinked")
                    for m in img.get("google_ads_assets", [])
                )
            ]
            if matches:
                candidates[cat] = [
                    {"image_id": m["image_id"], "description": m.get("ai_description", "")}
                    for m in matches[:3]
                ]

        result = {
            "campaign_name": campaign_name,
            "total_images": total,
            "composition": composition,
            "priority": [{"category": cat, **data} for cat, data in priority],
            "recommendations": recommendations,
            "smart_recs": smart_recs,
            "candidates": candidates,
        }

        logger.info(
            "Gap analysis for %s: %d images, %d recommendations",
            campaign_name, total, len(recommendations),
        )
        return result

    def format_gap_analysis(self, analysis: Dict[str, Any]) -> str:
        """Format gap analysis results as a readable string for Slack/CLI."""
        campaign = analysis["campaign_name"]
        total = analysis["total_images"]
        lines = [
            f"*Image Composition Analysis — {campaign}*",
            f"{total} images in asset group",
            "",
            f"{'Category':<28} {'Actual':>7} {'Target':>7} {'Gap':>7}  Status",
            "─" * 65,
        ]

        for item in analysis["priority"]:
            cat = item["category"].replace("_", " ")
            actual = f"{item['actual_pct']:.0f}%"
            target = f"{item['target_pct']:.0f}%"
            delta = item["delta"]
            if item["status"] == "under":
                status = f"▼ UNDER ({delta:+.0f}%)"
            elif item["status"] == "over":
                status = f"▲ OVER ({delta:+.0f}%)"
            else:
                status = "✓ ON TARGET"
            lines.append(f"{cat:<28} {actual:>7} {target:>7} {delta:>+7.0f}%  {status}")

        if analysis["recommendations"]:
            lines.append("")
            header = "*Strategic Recommendations:*" if analysis.get("smart_recs") else "*Recommendations:*"
            lines.append(header)
            for rec in analysis["recommendations"]:
                lines.append(f"  • {rec}")

        if analysis["candidates"]:
            lines.append("")
            lines.append("*Available in repo:*")
            for cat, imgs in analysis["candidates"].items():
                for img in imgs:
                    lines.append(f"  • [{cat}] {img['image_id']}: {img['description']}")

        return "\n".join(lines)

    # --- Smart Recommendations ---

    def _generate_smart_recommendations(
        self, campaign_context: Dict[str, str], under_gaps: List[Tuple[str, Dict[str, Any]]]
    ) -> List[str]:
        """Generate strategy-aware recommendations using Claude.

        Sends campaign context and gap data to Claude and returns a list of
        actionable recommendation strings.
        """
        gap_lines = []
        for category, data in under_gaps:
            gap_lines.append(
                f"- {category.replace('_', ' ')}: {data['actual_pct']:.0f}% actual vs {data['target_pct']:.0f}% target (delta {data['delta']:+.0f}%)"
            )
        gap_data = "\n".join(gap_lines)

        prompt_text = SMART_RECS_PROMPT.format(
            campaign_name=campaign_context.get("campaign_name", ""),
            description=campaign_context.get("description", ""),
            goal=campaign_context.get("goal", ""),
            target_audience=campaign_context.get("target_audience", ""),
            key_products=", ".join(campaign_context.get("key_products", [])),
            tone_notes=campaign_context.get("tone_notes", ""),
            gap_data=gap_data,
        )

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt_text,
                    }
                ],
            },
        )

        if not response.ok:
            logger.error("Claude API error for smart recs %d: %s", response.status_code, response.text[:500])
            raise RuntimeError(f"Claude API {response.status_code}")

        result = response.json()
        text = result["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        recs = json.loads(text)
        logger.info("Generated %d smart recommendations", len(recs))
        return recs

    # --- Helpers ---

    def _get_dimensions(self, image_bytes: bytes) -> Tuple[int, int]:
        """Get image width and height from bytes without Pillow."""
        # Try PNG
        if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            width = int.from_bytes(image_bytes[16:20], 'big')
            height = int.from_bytes(image_bytes[20:24], 'big')
            return width, height

        # Try JPEG
        if image_bytes[:2] == b'\xff\xd8':
            i = 2
            while i < len(image_bytes) - 1:
                if image_bytes[i] != 0xFF:
                    break
                marker = image_bytes[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    height = int.from_bytes(image_bytes[i + 5:i + 7], 'big')
                    width = int.from_bytes(image_bytes[i + 7:i + 9], 'big')
                    return width, height
                length = int.from_bytes(image_bytes[i + 2:i + 4], 'big')
                i += 2 + length
            return 0, 0

        return 0, 0

    def _classify_aspect_ratio(self, width: int, height: int) -> str:
        """Classify image aspect ratio as landscape, square, or portrait."""
        if width == 0 or height == 0:
            return "unknown"
        ratio = width / height
        if ratio > 1.2:
            return "landscape"
        elif ratio < 0.85:
            return "portrait"
        return "square"

    def _find_by_hash(self, image_hash: str) -> Optional[Dict[str, Any]]:
        """Find an existing image by SHA-256 hash."""
        all_images = get_all_images()
        for image in all_images:
            if image.get("image_hash") == image_hash:
                return image
        return None

    def _reconcile_campaign_mappings(self, campaign_name: str, live_asset_resources: set) -> int:
        """Unlink registry mappings no longer present in Google Ads.

        Compares the set of asset_resources currently live in Google Ads
        against the registry. Any mapping for this campaign whose
        asset_resource is not in the live set gets date_unlinked stamped.

        Returns the number of mappings unlinked.
        """
        images = get_images_for_campaign(campaign_name)
        unlinked_count = 0
        now = datetime.utcnow().isoformat() + "Z"

        for image in images:
            updated = False
            for mapping in image.get("google_ads_assets", []):
                if (
                    mapping.get("campaign_name") == campaign_name
                    and not mapping.get("date_unlinked")
                    and mapping.get("asset_resource") not in live_asset_resources
                ):
                    mapping["date_unlinked"] = now
                    updated = True
                    unlinked_count += 1

            if updated:
                save_image(image)

        if unlinked_count:
            logger.info("Unlinked %d stale mappings for %s", unlinked_count, campaign_name)
        return unlinked_count

    def _add_google_ads_mapping(
        self, image: Dict[str, Any], mapping: Dict[str, str]
    ) -> None:
        """Add a Google Ads mapping to an existing image entry."""
        now = datetime.utcnow().isoformat() + "Z"
        mapping["date_linked"] = now

        existing_mappings = image.get("google_ads_assets", [])

        # Check if this asset_resource is already mapped
        for m in existing_mappings:
            if m.get("asset_resource") == mapping.get("asset_resource"):
                return

        existing_mappings.append(mapping)
        image["google_ads_assets"] = existing_mappings
        image["status"] = "in_use"
        save_image(image)
        logger.info(
            "Added Google Ads mapping to image %s: %s in %s",
            image["image_id"],
            mapping.get("asset_resource"),
            mapping.get("campaign_name"),
        )
