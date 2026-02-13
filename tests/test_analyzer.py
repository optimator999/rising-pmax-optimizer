"""Tests for the asset analyzer using historical data."""

import csv
import os
import sys
from typing import Any, Dict, List

import pytest

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from src.analyzer import AssetAnalyzer, calculate_budget_recommendation
from database.queries import generate_asset_id

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "test_data")


def load_test_csv(filename: str) -> List[Dict[str, Any]]:
    """Load a test CSV file into a list of asset dicts."""
    filepath = os.path.join(TEST_DATA_DIR, filename)
    assets = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asset = {
                "asset_id": generate_asset_id(
                    row["asset_text"], row["campaign_name"]
                ),
                "asset_text": row["asset_text"],
                "asset_type": row["asset_type"].upper().replace(" ", "_"),
                "campaign_name": row["campaign_name"],
                "impressions": int(row["impressions"]),
                "clicks": int(row["clicks"]),
                "ctr": float(row["ctr"]),
                "conversions": float(row["conversions"]),
                "cost": float(row["cost"]),
                "cpa": float(row["cpa"]),
                "status": row["status"],
                "date_added": "2025-05-06",  # All old enough to judge
            }
            # Map csv types
            type_map = {
                "HEADLINE": "HEADLINE",
                "LONG_HEADLINE": "LONG_HEADLINE",
                "DESCRIPTION": "DESCRIPTION",
            }
            asset["asset_type"] = type_map.get(
                asset["asset_type"], asset["asset_type"]
            )
            assets.append(asset)
    return assets


class TestAnalyzerNovember:
    """Test analyzer with November 2025 data (low season)."""

    def setup_method(self):
        self.assets = load_test_csv("november_2025.csv")
        self.analyzer = AssetAnalyzer(month=11)

    def test_season_detected_correctly(self):
        assert self.analyzer.season == "low_season"

    def test_does_not_flag_headline_above_ctr_threshold(self):
        """Headlines with CTR above 2.0% should NOT be flagged (CTR-only)."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        flagged_texts = [a["asset_text"] for a in flagged]
        # "Nets That Land Monsters" has 2.38% CTR - above 2.0% threshold
        # Previously flagged for zero conversions, but now CTR-only
        assert "Nets That Land Monsters" not in flagged_texts

    def test_flags_low_ctr_long_headlines(self):
        """Long headlines with CTR below 1.0% should be flagged."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        flagged_texts = [a["asset_text"] for a in flagged]
        # These all have CTR < 1.0%
        assert "Experience Unmatched Quality and Innovation" in flagged_texts
        assert (
            "Top-of-the-Line Fly Fishing Gear and Accessories" in flagged_texts
        )
        assert (
            "Rising Fishing: Your Premier Fly Fishing Destination."
            in flagged_texts
        )

    def test_does_not_flag_top_performers(self):
        """Top performing assets should not be flagged."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        flagged_texts = [a["asset_text"] for a in flagged]
        assert "Rising Fishing" not in flagged_texts
        assert "USA Made Fly Fishing Nets" not in flagged_texts
        assert "Fly Fishing Nets" not in flagged_texts

    def test_flags_all_qualifying_assets(self):
        """No quota system - all underperformers should be flagged."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        # Should flag multiple assets, not just top N
        assert len(flagged) >= 4

    def test_kill_reason_populated(self):
        """Each flagged asset should have a kill_reason."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        for asset in flagged:
            assert asset.get("kill_reason"), (
                f"Missing kill_reason for '{asset['asset_text']}'"
            )

    def test_diagnosis_populated(self):
        """Each flagged asset should have a diagnosis."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        for asset in flagged:
            assert asset.get("diagnosis"), (
                f"Missing diagnosis for '{asset['asset_text']}'"
            )


class TestAnalyzerJanuary:
    """Test analyzer with January 2026 data (deep winter)."""

    def setup_method(self):
        self.assets = load_test_csv("january_2026.csv")
        self.analyzer = AssetAnalyzer(month=1)

    def test_season_detected_correctly(self):
        assert self.analyzer.season == "deep_winter"

    def test_relaxed_thresholds_in_winter(self):
        """Deep winter has more lenient thresholds."""
        thresholds = self.analyzer.thresholds
        assert thresholds["min_impressions"] == 150
        assert thresholds["min_ctr_headline"] == 2.0
        assert "max_cost_zero_conv" not in thresholds

    def test_asset_changes_disabled_in_winter(self):
        """Deep winter should be monitor-only (no asset changes)."""
        thresholds = self.analyzer.thresholds
        assert thresholds["asset_changes_enabled"] is False

    def test_flags_worst_performers(self):
        """Even with relaxed thresholds, worst offenders get flagged."""
        flagged = self.analyzer.flag_underperformers(self.assets)
        flagged_texts = [a["asset_text"] for a in flagged]
        # These should still be flagged even with relaxed thresholds
        assert (
            "Rising Fishing: Your Premier Fly Fishing Destination."
            in flagged_texts
        )

    def test_all_zero_conversions(self):
        """In January all assets had 0 conversions - system should handle this."""
        total_conv = sum(float(a.get("conversions", 0)) for a in self.assets)
        assert total_conv == 0.0


class TestAnalyzerLowSeason:
    """Test analyzer with low season (Nov-Dec) - monitor only."""

    def setup_method(self):
        self.analyzer = AssetAnalyzer(month=11)

    def test_asset_changes_disabled_in_low_season(self):
        """Low season should be monitor-only (no asset changes)."""
        thresholds = self.analyzer.thresholds
        assert thresholds["asset_changes_enabled"] is False


class TestAnalyzerShoulderSeason:
    """Test analyzer with shoulder season thresholds."""

    def setup_method(self):
        self.analyzer = AssetAnalyzer(month=3)

    def test_season_detected_correctly(self):
        assert self.analyzer.season == "shoulder_season"

    def test_asset_changes_enabled_in_shoulder(self):
        """Shoulder season should have asset changes enabled."""
        thresholds = self.analyzer.thresholds
        assert thresholds["asset_changes_enabled"] is True
        assert thresholds["min_impressions"] == 500


class TestAnalyzerPeakSeason:
    """Test analyzer with peak season thresholds."""

    def setup_method(self):
        self.analyzer = AssetAnalyzer(month=6)

    def test_season_detected_correctly(self):
        assert self.analyzer.season == "peak_season"

    def test_stricter_thresholds(self):
        thresholds = self.analyzer.thresholds
        assert thresholds["min_impressions"] == 500
        assert thresholds["min_ctr_headline"] == 4.0
        assert thresholds["min_ctr_description"] == 5.0

    def test_asset_changes_enabled_in_peak(self):
        """Peak season should have asset changes enabled."""
        thresholds = self.analyzer.thresholds
        assert thresholds["asset_changes_enabled"] is True


class TestNewAssetProtection:
    """Test that new assets get patience period."""

    def test_new_asset_not_flagged(self):
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "New Test Asset",
            "asset_type": "HEADLINE",
            "impressions": 50,
            "clicks": 0,
            "ctr": 0.0,
            "conversions": 0,
            "cost": 5.0,
            "status": "active",
            "date_added": "2026-02-01",  # Very recent
        }
        assert analyzer.is_new_asset(asset) is True

    def test_old_asset_not_protected(self):
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "Old Asset",
            "asset_type": "HEADLINE",
            "impressions": 1000,
            "clicks": 10,
            "ctr": 1.0,
            "conversions": 0,
            "cost": 50.0,
            "status": "active",
            "date_added": "2025-01-01",  # Old
        }
        assert analyzer.is_new_asset(asset) is False


class TestDiagnosis:
    """Test failure diagnosis logic."""

    def setup_method(self):
        self.analyzer = AssetAnalyzer(month=11)

    def test_diagnose_hype_language(self):
        asset = {
            "asset_text": "Innovative Premium Fishing Nets",
            "asset_type": "HEADLINE",
            "ctr": 1.0,
            "conversions": 0,
        }
        diagnosis = self.analyzer.diagnose_failure(asset, [])
        assert "voice" in diagnosis.lower()

    def test_diagnose_gatekeeping(self):
        asset = {
            "asset_text": "For Serious Professional Anglers",
            "asset_type": "HEADLINE",
            "ctr": 1.0,
            "conversions": 0,
        }
        diagnosis = self.analyzer.diagnose_failure(asset, [])
        assert "voice" in diagnosis.lower() or "gatekeeping" in diagnosis.lower()

    def test_diagnose_low_engagement(self):
        asset = {
            "asset_text": "Nice Fishing Stuff",
            "asset_type": "HEADLINE",
            "ctr": 1.0,
        }
        diagnosis = self.analyzer.diagnose_failure(asset, [])
        assert "angle" in diagnosis.lower()


class TestBudgetRecommendation:
    """Test budget recommendation algorithm."""

    def test_increase_when_roas_above_target(self):
        result = calculate_budget_recommendation(
            current_daily_budget=100,
            actual_daily_spend_avg=95,
            current_roas=240,
            target_roas=200,
            season="peak_season",
        )
        assert result["action"] == "increase"
        assert result["recommended_budget"] == 120.0

    def test_hold_when_roas_on_target(self):
        result = calculate_budget_recommendation(
            current_daily_budget=100,
            actual_daily_spend_avg=95,
            current_roas=195,
            target_roas=200,
            season="peak_season",
        )
        assert result["action"] == "hold"
        assert result["recommended_budget"] == 100

    def test_decrease_when_roas_below_target(self):
        result = calculate_budget_recommendation(
            current_daily_budget=100,
            actual_daily_spend_avg=95,
            current_roas=160,
            target_roas=200,
            season="peak_season",
        )
        assert result["action"] == "decrease"
        assert result["recommended_budget"] == 80.0

    def test_pause_when_roas_critically_low(self):
        result = calculate_budget_recommendation(
            current_daily_budget=100,
            actual_daily_spend_avg=95,
            current_roas=50,
            target_roas=200,
            season="peak_season",
        )
        assert result["action"] == "pause"
        assert result["recommended_budget"] == 10.0

    def test_market_ceiling_detection(self):
        result = calculate_budget_recommendation(
            current_daily_budget=300,
            actual_daily_spend_avg=200,
            current_roas=250,
            target_roas=200,
            season="peak_season",
        )
        assert result["action"] == "hold"
        assert result["market_ceiling_detected"] is True

    def test_minimum_budget_floor(self):
        result = calculate_budget_recommendation(
            current_daily_budget=12,
            actual_daily_spend_avg=11,
            current_roas=160,
            target_roas=200,
            season="deep_winter",
        )
        assert result["recommended_budget"] >= 10.0


class TestImageAssetFlagging:
    """Test image asset flagging and diagnosis."""

    def test_image_below_ctr_threshold_is_flagged(self):
        """Image with CTR below 1.0% should be flagged."""
        analyzer = AssetAnalyzer(month=6)  # peak season
        asset = {
            "asset_text": "lifestyle-river-shot-03",
            "asset_type": "MARKETING_IMAGE",
            "impressions": 1240,
            "clicks": 9,
            "ctr": 0.73,
            "conversions": 0,
            "cost": 12.50,
            "status": "active",
            "date_added": "2025-01-01",
        }
        reason = analyzer.should_kill(asset)
        assert reason is not None
        assert "CTR" in reason
        assert "MARKETING_IMAGE" in reason

    def test_image_above_ctr_threshold_not_flagged(self):
        """Image with CTR above 1.0% should NOT be flagged."""
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "product-net-walnut-01",
            "asset_type": "MARKETING_IMAGE",
            "impressions": 2100,
            "clicks": 67,
            "ctr": 3.19,
            "conversions": 2,
            "cost": 25.00,
            "status": "active",
            "date_added": "2025-01-01",
        }
        reason = analyzer.should_kill(asset)
        assert reason is None

    def test_image_diagnosis_returns_visual_fatigue(self):
        """Image diagnosis should return visual_fatigue message."""
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "lifestyle-river-shot-03",
            "asset_type": "MARKETING_IMAGE",
            "ctr": 0.73,
        }
        diagnosis = analyzer.diagnose_failure(asset, [])
        assert "visual_fatigue" in diagnosis
        assert "fresh creative" in diagnosis.lower()

    def test_square_image_diagnosis_returns_visual_fatigue(self):
        """Square image diagnosis should also return visual_fatigue."""
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "square-product-01",
            "asset_type": "SQUARE_MARKETING_IMAGE",
            "ctr": 0.50,
        }
        diagnosis = analyzer.diagnose_failure(asset, [])
        assert "visual_fatigue" in diagnosis

    def test_portrait_image_diagnosis_returns_visual_fatigue(self):
        """Portrait image diagnosis should also return visual_fatigue."""
        analyzer = AssetAnalyzer(month=6)
        asset = {
            "asset_text": "portrait-lifestyle-01",
            "asset_type": "PORTRAIT_MARKETING_IMAGE",
            "ctr": 0.40,
        }
        diagnosis = analyzer.diagnose_failure(asset, [])
        assert "visual_fatigue" in diagnosis

    def test_image_thresholds_present_in_all_seasons(self):
        """All seasons should have image CTR thresholds."""
        from config.thresholds import THRESHOLDS

        for season_name, config in THRESHOLDS.items():
            assert "min_ctr_marketing_image" in config, (
                f"Missing min_ctr_marketing_image in {season_name}"
            )
            assert "min_ctr_square_marketing_image" in config, (
                f"Missing min_ctr_square_marketing_image in {season_name}"
            )
            assert "min_ctr_portrait_marketing_image" in config, (
                f"Missing min_ctr_portrait_marketing_image in {season_name}"
            )
            assert config["min_ctr_marketing_image"] == 1.0
            assert config["min_ctr_square_marketing_image"] == 1.0
            assert config["min_ctr_portrait_marketing_image"] == 1.0

    def test_image_flagged_via_flag_underperformers(self):
        """Image assets should flow through flag_underperformers correctly."""
        analyzer = AssetAnalyzer(month=6)
        assets = [
            {
                "asset_text": "low-ctr-image",
                "asset_type": "MARKETING_IMAGE",
                "impressions": 1000,
                "clicks": 5,
                "ctr": 0.5,
                "conversions": 0,
                "cost": 10.00,
                "status": "active",
                "date_added": "2025-01-01",
            },
            {
                "asset_text": "high-ctr-image",
                "asset_type": "SQUARE_MARKETING_IMAGE",
                "impressions": 1000,
                "clicks": 30,
                "ctr": 3.0,
                "conversions": 1,
                "cost": 15.00,
                "status": "active",
                "date_added": "2025-01-01",
            },
        ]
        flagged = analyzer.flag_underperformers(assets)
        assert len(flagged) == 1
        assert flagged[0]["asset_text"] == "low-ctr-image"
        assert "visual_fatigue" in flagged[0]["diagnosis"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
