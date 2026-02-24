"""Test that bootstrap queries return ENABLED images per asset group, not all format crops."""

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.image_manager import ASSET_GROUP_QUERY, IMAGE_ASSET_QUERY


def test_asset_group_query_filters_by_campaign_resource():
    """ASSET_GROUP_QUERY should parameterize on campaign resource name."""
    query = ASSET_GROUP_QUERY.format(
        campaign_resource="customers/4219553056/campaigns/22483972722"
    )
    assert "customers/4219553056/campaigns/22483972722" in query
    # Status is selected for reading, but not filtered in the query — filtering is done in Python
    assert "asset_group.status" in query


def test_image_query_filters_enabled_by_asset_group():
    """IMAGE_ASSET_QUERY should filter by asset_group.resource_name and status = ENABLED."""
    query = IMAGE_ASSET_QUERY.format(
        asset_group_resource="customers/4219553056/assetGroups/6571498020"
    )
    assert "customers/4219553056/assetGroups/6571498020" in query
    assert "asset_group_asset.status = 'ENABLED'" in query
    assert "asset.type = 'IMAGE'" in query
    # Should NOT use the old campaign.id filter
    assert "campaign.id" not in query
    # Should NOT use the old != REMOVED filter
    assert "!= 'REMOVED'" not in query


def test_bootstrap_queries_enabled_asset_groups():
    """Bootstrap should first get asset groups, then query only ENABLED ones."""
    from src.image_manager import ImageManager

    # Mock collector
    mock_collector = MagicMock()
    mock_collector.client_customer_id = "4219553056"

    # Asset group query returns 3 groups, only 1 ENABLED
    mock_collector._search.side_effect = [
        # First call: asset group lookup
        [
            {"assetGroup": {"resourceName": "customers/4219553056/assetGroups/6571460471", "name": "Replacement Net", "status": "REMOVED", "id": "6571460471"}},
            {"assetGroup": {"resourceName": "customers/4219553056/assetGroups/6571497930", "name": "On the Water", "status": "REMOVED", "id": "6571497930"}},
            {"assetGroup": {"resourceName": "customers/4219553056/assetGroups/6571498020", "name": "Core Brand", "status": "ENABLED", "id": "6571498020"}},
        ],
        # Second call: image assets for the one ENABLED group — returns 20, not 60
        [
            _make_image_row(f"customers/4219553056/assets/{i}", f"image_{i}.jpg", "MARKETING_IMAGE")
            for i in range(20)
        ],
    ]

    campaigns = {
        "Core Brand": {
            "campaign_id": "22483972722",
            "asset_group": "Core Brand",
            "slug": "core_brand",
            "image_profile": {},
        }
    }

    with patch("src.image_manager.requests.get") as mock_get, \
         patch("src.image_manager.requests.post") as mock_post, \
         patch("src.image_manager.save_image"), \
         patch("src.image_manager.get_all_images", return_value=[]), \
         patch("src.image_manager.get_images_for_campaign", return_value=[]):

        # Mock image download
        mock_response = MagicMock()
        mock_response.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG
        mock_response.headers = {"content-type": "image/png"}
        mock_response.ok = True
        mock_get.return_value = mock_response

        # Mock Claude Vision analysis
        mock_vision = MagicMock()
        mock_vision.ok = True
        mock_vision.json.return_value = {
            "content": [{"text": '{"content_category": "product_hero", "product_visible": true, "human_present": false, "scene_type": "studio", "background_complexity": "simple", "text_overlay": false, "product_frame_ratio": "tight", "lighting": "studio", "seasonal_relevance": ["all_season"], "description": "test", "crop_eligibility": {}}'}]
        }
        mock_post.return_value = mock_vision

        manager = ImageManager(
            anthropic_api_key="test-key",
            google_ads_collector=mock_collector,
            campaigns=campaigns,
        )
        results = manager.bootstrap_from_google_ads(["Core Brand"])

    # Should have queried asset groups, then images for the 1 ENABLED group
    assert mock_collector._search.call_count == 2

    # Should have found 20 images, not 60
    assert results["total"] == 20
    assert results["by_campaign"]["Core Brand"]["new"] == 20


def _make_image_row(asset_resource, name, field_type):
    return {
        "assetGroupAsset": {
            "asset": asset_resource,
            "fieldType": field_type,
        },
        "asset": {
            "name": name,
            "imageAsset": {
                "fullSize": {
                    "url": f"https://example.com/{name}",
                    "widthPixels": 1200,
                    "heightPixels": 628,
                },
                "fileSize": "50000",
            },
        },
    }
