"""Seasonal thresholds and budget baselines for Rising PMax Optimizer."""

from typing import Dict, Any

THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "deep_winter": {
        "months": [1, 2],
        "min_impressions": 150,
        "min_ctr_headline": 2.0,
        "min_ctr_long_headline": 1.0,
        "min_ctr_description": 3.0,

        "lookback_days": 60,
        "new_asset_patience_days": 60,
        "new_asset_patience_impressions": 500,
    },
    "low_season": {
        "months": [11, 12],
        "min_impressions": 150,
        "min_ctr_headline": 2.0,
        "min_ctr_long_headline": 1.0,
        "min_ctr_description": 3.0,

        "lookback_days": 60,
        "new_asset_patience_days": 60,
        "new_asset_patience_impressions": 500,
    },
    "shoulder_season": {
        "months": [3, 4, 9, 10],
        "min_impressions": 300,
        "min_ctr_headline": 3.0,
        "min_ctr_long_headline": 2.0,
        "min_ctr_description": 4.0,

        "lookback_days": 30,
        "new_asset_patience_days": 60,
        "new_asset_patience_impressions": 500,
    },
    "peak_season": {
        "months": [5, 6, 7, 8],
        "min_impressions": 500,
        "min_ctr_headline": 4.0,
        "min_ctr_long_headline": 2.5,
        "min_ctr_description": 5.0,

        "lookback_days": 30,
        "new_asset_patience_days": 60,
        "new_asset_patience_impressions": 500,
    },
}

SEASONAL_BUDGETS: Dict[str, Dict[str, Any]] = {
    "deep_winter": {
        "recommended_daily": 10.0,
        "max_daily": 30.0,
        "target_roas": 150.0,
        "notes": "Maintenance mode. Expect near-zero conversions. Focus on brand awareness.",
    },
    "low_season": {
        "recommended_daily": 30.0,
        "max_daily": 75.0,
        "target_roas": 200.0,
        "notes": "Limited activity. Good time to test new assets with low stakes.",
    },
    "shoulder_season": {
        "recommended_daily": 100.0,
        "max_daily": 300.0,
        "target_roas": 200.0,
        "notes": "Demand building. Scale aggressively if hitting targets.",
    },
    "peak_season": {
        "recommended_daily": 150.0,
        "max_daily": 900.0,
        "target_roas": 200.0,
        "notes": "Prime time. Scale to market ceiling based on ROAS performance.",
    },
}

# Monthly demand as % of annual
SEASONALITY_CURVE = {
    1: 2.0,
    2: 3.0,
    3: 7.0,
    4: 10.0,
    5: 13.0,
    6: 13.0,
    7: 12.0,
    8: 10.0,
    9: 9.0,
    10: 8.0,
    11: 7.0,
    12: 6.0,
}


def get_season_name(month: int) -> str:
    """Return the season name for a given month."""
    for season_name, config in THRESHOLDS.items():
        if month in config["months"]:
            return season_name
    raise ValueError(f"No season defined for month {month}")


def get_thresholds(month: int) -> Dict[str, Any]:
    """Return the threshold config for a given month."""
    season = get_season_name(month)
    return THRESHOLDS[season]


def get_seasonal_budget(month: int) -> Dict[str, Any]:
    """Return budget config for a given month."""
    season = get_season_name(month)
    return SEASONAL_BUDGETS[season]


def get_monthly_demand(month: int) -> float:
    """Return % of annual demand for a given month."""
    return SEASONALITY_CURVE.get(month, 0.0)
