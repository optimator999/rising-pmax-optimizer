"""Analysis engine for flagging underperforming assets."""

import logging
from typing import Any, Dict, List, Optional

from config.thresholds import get_season_name, get_thresholds, get_monthly_demand
from utils.date_helpers import days_since, get_current_month

logger = logging.getLogger("rising-pmax.analyzer")

# Patterns known to fail based on historical data
KNOWN_FAILURE_PATTERNS = [
    "innovative",
    "premier",
    "top-of-the-line",
    "unmatched",
    "experience",
    "destination",
    "serious anglers",
    "monsters",
]


class AssetAnalyzer:
    """Analyzes asset performance and flags underperformers."""

    def __init__(self, month: Optional[int] = None):
        """Initialize with optional month override (for testing)."""
        self.month = month or get_current_month()
        self.season = get_season_name(self.month)
        self.thresholds = get_thresholds(self.month)
        self.demand = get_monthly_demand(self.month)
        logger.info(
            "Analyzer initialized: season=%s, month=%d, demand=%.1f%%",
            self.season,
            self.month,
            self.demand,
        )

    def is_new_asset(self, asset: Dict[str, Any]) -> bool:
        """Check if an asset is too new to judge.

        An asset is considered new if:
        - It has been active for fewer than patience_days, AND
        - It has fewer than patience_impressions
        """
        date_added = asset.get("date_added")
        if not date_added:
            return False

        age_days = days_since(date_added)
        impressions = int(asset.get("impressions", 0))
        patience_days = self.thresholds["new_asset_patience_days"]
        patience_impr = self.thresholds["new_asset_patience_impressions"]

        is_new = age_days < patience_days and impressions < patience_impr

        if is_new:
            logger.debug(
                "Asset '%s' is new (%d days, %d impr)",
                asset.get("asset_text", "?"),
                age_days,
                impressions,
            )
        return is_new

    def should_kill(self, asset: Dict[str, Any]) -> Optional[str]:
        """Apply kill criteria and return reason if asset should be killed.

        Returns None if asset should be kept, or a reason string if it should die.
        """
        asset_type = asset.get("asset_type", "HEADLINE")
        impressions = int(asset.get("impressions", 0))
        ctr = float(asset.get("ctr", 0.0))

        min_impressions = self.thresholds["min_impressions"]

        # Not enough data to judge
        if impressions < min_impressions:
            return None

        # CTR-only flagging (conversion data is unreliable at asset level in PMax)
        ctr_key = {
            "HEADLINE": "min_ctr_headline",
            "LONG_HEADLINE": "min_ctr_long_headline",
            "DESCRIPTION": "min_ctr_description",
            "MARKETING_IMAGE": "min_ctr_marketing_image",
            "SQUARE_MARKETING_IMAGE": "min_ctr_square_marketing_image",
            "PORTRAIT_MARKETING_IMAGE": "min_ctr_portrait_marketing_image",
        }.get(asset_type)

        if ctr_key:
            min_ctr = self.thresholds[ctr_key]
            if ctr < min_ctr:
                return (
                    f"CTR {ctr:.2f}% below {self.season} threshold "
                    f"{min_ctr:.1f}% for {asset_type} ({impressions} impressions)"
                )

        return None

    def diagnose_failure(
        self, asset: Dict[str, Any], graveyard: List[Dict[str, Any]]
    ) -> str:
        """Determine why an asset failed for copy generation guidance.

        Categories:
        - voice: Used hype/marketing language
        - angle: Wrong value proposition
        - specificity: Too vague or generic
        - length: Poor use of character limit
        """
        asset_type = asset.get("asset_type", "")

        # Image assets: skip text-based analysis
        if asset_type in (
            "MARKETING_IMAGE",
            "SQUARE_MARKETING_IMAGE",
            "PORTRAIT_MARKETING_IMAGE",
        ):
            return "visual_fatigue: Image underperforming. Consider replacing with fresh creative."

        text = asset.get("asset_text", "").lower()

        # Check for voice violations (hype language)
        for pattern in KNOWN_FAILURE_PATTERNS:
            if pattern.lower() in text:
                return f"voice: Contains hype language ('{pattern}'). Rising voice is calm and direct."

        # Check for vagueness
        word_count = len(text.split())
        if word_count <= 2 and asset_type == "LONG_HEADLINE":
            return "specificity: Too short/vague for a long headline. Needs concrete detail."

        # Check for gatekeeping or exclusionary language
        gatekeeping_words = ["serious", "elite", "professional", "expert", "advanced"]
        for word in gatekeeping_words:
            if word in text:
                return f"voice: Gatekeeping language ('{word}'). Rising is inclusive."

        # Check if similar pattern exists in graveyard
        for grave in graveyard:
            grave_text = grave.get("asset_text", "").lower()
            # Simple similarity: share 50%+ words
            asset_words = set(text.split())
            grave_words = set(grave_text.split())
            if asset_words and grave_words:
                overlap = len(asset_words & grave_words) / len(asset_words)
                if overlap > 0.5:
                    return (
                        f"angle: Similar to previously killed asset "
                        f"'{grave.get('asset_text', '')}'. Try a different approach."
                    )

        # Default: low CTR means the copy isn't connecting
        return "angle: Low engagement. Try a more direct, product-focused approach."

    def flag_underperformers(
        self,
        assets: List[Dict[str, Any]],
        graveyard: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Flag ALL assets meeting kill criteria (no quotas).

        Returns list of flagged assets with kill_reason and diagnosis added.
        """
        graveyard = graveyard or []
        flagged = []

        for asset in assets:
            # Skip new assets (patience period)
            if self.is_new_asset(asset):
                continue

            # Skip already killed/paused
            if asset.get("status") in ("killed", "paused"):
                continue

            kill_reason = self.should_kill(asset)
            if kill_reason:
                asset["kill_reason"] = kill_reason
                asset["diagnosis"] = self.diagnose_failure(asset, graveyard)
                flagged.append(asset)
                logger.info(
                    "Flagged: '%s' - %s",
                    asset.get("asset_text", "?"),
                    kill_reason,
                )

        logger.info(
            "Flagged %d of %d assets for replacement", len(flagged), len(assets)
        )
        return flagged


def calculate_budget_recommendation(
    current_daily_budget: float,
    actual_daily_spend_avg: float,
    current_roas: float,
    target_roas: float,
    season: str,
) -> Dict[str, Any]:
    """Calculate budget recommendation based on ROAS performance.

    Returns:
        Dict with action, recommended_budget, reason, market_ceiling_detected
    """
    if current_daily_budget <= 0:
        return {
            "action": "hold",
            "recommended_budget": 10.0,
            "reason": "No budget set. Start with $10/day.",
            "market_ceiling_detected": False,
        }

    utilization = (actual_daily_spend_avg / current_daily_budget) * 100

    # Market ceiling detection
    if utilization < 80 and current_daily_budget > 100:
        return {
            "action": "hold",
            "recommended_budget": current_daily_budget,
            "reason": (
                f"Market ceiling detected. Only spending "
                f"${actual_daily_spend_avg:.0f}/day of "
                f"${current_daily_budget:.0f} budget. "
                f"Cannot efficiently scale further."
            ),
            "market_ceiling_detected": True,
        }

    if target_roas <= 0:
        target_roas = 200.0

    roas_performance = (current_roas / target_roas) - 1

    if roas_performance >= 0.10:
        increase = current_daily_budget * 0.20
        return {
            "action": "increase",
            "recommended_budget": round(current_daily_budget + increase, 2),
            "reason": (
                f"ROAS {current_roas:.0f}% exceeds target {target_roas:.0f}% "
                f"by {roas_performance * 100:.0f}%. Recommend +20% budget increase."
            ),
            "market_ceiling_detected": False,
        }

    if roas_performance >= -0.10:
        return {
            "action": "hold",
            "recommended_budget": current_daily_budget,
            "reason": (
                f"ROAS {current_roas:.0f}% on target ({target_roas:.0f}%). "
                f"Hold budget steady."
            ),
            "market_ceiling_detected": False,
        }

    if roas_performance >= -0.30:
        decrease = current_daily_budget * 0.20
        return {
            "action": "decrease",
            "recommended_budget": max(round(current_daily_budget - decrease, 2), 10.0),
            "reason": (
                f"ROAS {current_roas:.0f}% is "
                f"{abs(roas_performance) * 100:.0f}% below target {target_roas:.0f}%. "
                f"Recommend -20% budget decrease."
            ),
            "market_ceiling_detected": False,
        }

    # Critical: ROAS >30% below target
    return {
        "action": "pause",
        "recommended_budget": 10.0,
        "reason": (
            f"ROAS {current_roas:.0f}% critically low (target {target_roas:.0f}%). "
            f"Recommend reducing to maintenance mode ($10/day) until performance recovers."
        ),
        "market_ceiling_detected": False,
    }


def check_emergency_conditions(
    budget_data: Dict[str, Any],
    asset_data: List[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Check for emergency conditions that trigger immediate alerts.

    Returns list of alert dicts (empty if no emergencies).
    """
    alerts = []
    history = history or []
    daily_spend = float(budget_data.get("actual_daily_spend_avg", 0))

    # Alert 1: CTR collapse
    if history and len(history) >= 2:
        current_ctr = float(budget_data.get("avg_ctr", 0))
        recent_ctrs = [float(h.get("avg_ctr", 0)) for h in history[:4] if h.get("avg_ctr")]
        if recent_ctrs:
            avg_recent_ctr = sum(recent_ctrs) / len(recent_ctrs)
            if avg_recent_ctr > 0 and current_ctr < (avg_recent_ctr * 0.5):
                alerts.append(
                    {
                        "severity": "HIGH",
                        "title": "CTR Dropped 50%+ Week-Over-Week",
                        "message": (
                            f"Average CTR fell from {avg_recent_ctr:.2f}% "
                            f"to {current_ctr:.2f}%"
                        ),
                        "actions": [
                            "Check if Google changed ad policies",
                            "Review competitive landscape",
                            "Verify landing page is loading",
                            "Consider emergency asset refresh",
                        ],
                    }
                )

    # Alert 3: Budget runaway
    target_budget = float(budget_data.get("daily_budget_target", 0))
    roas = float(budget_data.get("roas_percent", 0))
    target_roas = float(budget_data.get("target_roas_percent", 200))

    if target_budget > 0 and daily_spend > (target_budget * 2) and roas < target_roas:
        alerts.append(
            {
                "severity": "HIGH",
                "title": "Spending 2x Budget with Low ROAS",
                "message": (
                    f"Spending ${daily_spend:.2f}/day (target ${target_budget:.2f}) "
                    f"at {roas:.0f}% ROAS (target {target_roas:.0f}%)"
                ),
                "actions": [
                    "Reduce daily budget cap immediately",
                    "Review audience expansion settings",
                    "Check placement performance",
                    "Tighten targeting if possible",
                ],
                "auto_action": f"Set daily budget to ${target_budget:.2f}",
            }
        )

    # Alert 4: Market ceiling
    utilization = float(budget_data.get("budget_utilization_percent", 100))
    if utilization < 80 and target_budget > 100:
        alerts.append(
            {
                "severity": "INFO",
                "title": "Market Ceiling Detected",
                "message": (
                    f"Only spending ${daily_spend:.2f} of ${target_budget:.2f} "
                    f"budget ({utilization:.0f}% utilization)"
                ),
                "actions": [
                    "Stop increasing budget - market cannot absorb more",
                    "Consider expanding to new campaigns (geo-targeting, different products)",
                    "Diversify to Meta, Klaviyo, or other channels",
                    "This is your efficient spend ceiling",
                ],
            }
        )

    return alerts
