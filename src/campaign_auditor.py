"""Campaign health audit for Rising PMax Optimizer.

Runs 20 checks across 5 categories, scores campaign health,
and generates a Slack report with findings and recommendations.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import requests

from config.thresholds import get_season_name, get_seasonal_budget
from database.queries import (
    get_budget_history,
    get_graveyard_assets,
    get_images_for_campaign,
    get_latest_asset_records,
)
from src.image_manager import CONTENT_CATEGORIES, _dedupe_campaign_images
from utils.date_helpers import get_current_month

logger = logging.getLogger("rising-pmax.auditor")

# Severity deductions for health score
SEVERITY_DEDUCTIONS = {
    "CRITICAL": 15,
    "WARNING": 5,
    "INFO": 0,
    "PASS": 0,
}

# Grade thresholds (checked in order, first match wins)
GRADE_SCALE = [
    (90, "A"),
    (75, "B"),
    (60, "C"),
    (40, "D"),
    (0, "F"),
]

AUDIT_SUMMARY_PROMPT = """You are a Google Ads Performance Max campaign health advisor. Given these audit findings for a fly fishing brand, write: (1) a 2-3 sentence executive summary of overall campaign health, and (2) a prioritized list of 3-5 specific actions to take. Be direct and specific. Return JSON: {{"summary": "...", "recommendations": ["...", "..."]}}

Audit findings:
{findings_json}"""


class CampaignAuditor:
    """Runs health checks on PMax campaigns and generates reports."""

    def __init__(self, campaign_config: Dict[str, Any], anthropic_api_key: str = None):
        self.campaign_config = campaign_config
        self.anthropic_key = anthropic_api_key
        self.month = get_current_month()
        self.season = get_season_name(self.month)
        self.seasonal_budget = get_seasonal_budget(self.month)

    def audit_all(self) -> Dict[str, Any]:
        """Run audit for all campaigns and generate cross-campaign summary."""
        campaigns = self.campaign_config.get("campaigns", {})
        campaign_results = {}
        all_findings = []

        for campaign_name in campaigns:
            result = self.audit_campaign(campaign_name)
            campaign_results[campaign_name] = result
            all_findings.extend(result["findings"])

        summary_data = self._generate_summary(all_findings, campaign_results)

        return {
            "campaigns": campaign_results,
            "summary": summary_data.get("summary", ""),
            "recommendations": summary_data.get("recommendations", []),
            "season": self.season,
            "month": self.month,
        }

    def audit_campaign(self, campaign_name: str) -> Dict[str, Any]:
        """Run all 20 checks for a single campaign."""
        campaign_data = self.campaign_config.get("campaigns", {}).get(campaign_name, {})

        findings = []
        findings.extend(self._check_config_completeness(campaign_name, campaign_data))
        findings.extend(self._check_google_ads_alignment(campaign_name, campaign_data))
        findings.extend(self._check_performance_trends(campaign_name))
        findings.extend(self._check_asset_health(campaign_name))
        findings.extend(self._check_image_composition(campaign_name, campaign_data))

        score, grade = self._calculate_score(findings)

        return {
            "campaign_name": campaign_name,
            "health_score": score,
            "grade": grade,
            "findings": findings,
        }

    # --- Category 1: Config Completeness (Checks 1-4) ---

    def _check_config_completeness(
        self, campaign_name: str, campaign_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        findings = []
        manual = campaign_data.get("manual", {})
        google_settings = campaign_data.get("google_ads_settings", {})

        # Check 1: Manual strategy fields populated
        required_fields = ["description", "goal", "target_audience", "key_products", "tone_notes"]
        missing = [f for f in required_fields if not manual.get(f)]

        if missing:
            findings.append({
                "check": "manual_strategy_fields",
                "category": "config_completeness",
                "severity": "WARNING",
                "message": f"Missing manual strategy fields: {', '.join(missing)}",
                "value": [f for f in required_fields if manual.get(f)],
                "expected": required_fields,
            })
        else:
            findings.append({
                "check": "manual_strategy_fields",
                "category": "config_completeness",
                "severity": "PASS",
                "message": "All manual strategy fields populated",
                "value": required_fields,
                "expected": required_fields,
            })

        # Check 2: Google Ads settings synced recently
        synced_at = google_settings.get("synced_at")

        if not synced_at:
            findings.append({
                "check": "google_ads_sync",
                "category": "config_completeness",
                "severity": "WARNING",
                "message": "Google Ads settings have never been synced",
                "value": None,
                "expected": "Synced within 48 hours",
            })
        else:
            try:
                synced_dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
                now = datetime.utcnow().replace(tzinfo=synced_dt.tzinfo)
                hours_ago = (now - synced_dt).total_seconds() / 3600

                if hours_ago > 48:
                    findings.append({
                        "check": "google_ads_sync",
                        "category": "config_completeness",
                        "severity": "WARNING",
                        "message": f"Google Ads settings last synced {hours_ago:.0f} hours ago",
                        "value": f"{hours_ago:.0f} hours",
                        "expected": "Within 48 hours",
                    })
                else:
                    findings.append({
                        "check": "google_ads_sync",
                        "category": "config_completeness",
                        "severity": "PASS",
                        "message": f"Google Ads settings synced {hours_ago:.0f} hours ago",
                        "value": f"{hours_ago:.0f} hours",
                        "expected": "Within 48 hours",
                    })
            except (ValueError, TypeError):
                findings.append({
                    "check": "google_ads_sync",
                    "category": "config_completeness",
                    "severity": "WARNING",
                    "message": f"Could not parse sync timestamp: {synced_at}",
                    "value": synced_at,
                    "expected": "Valid ISO timestamp within 48 hours",
                })

        # Check 3: Image profile defined and sums to ~1.0
        image_profile = campaign_data.get("image_profile", {})

        if not image_profile:
            findings.append({
                "check": "image_profile",
                "category": "config_completeness",
                "severity": "WARNING",
                "message": "No image profile defined",
                "value": None,
                "expected": "Image profile with values summing to ~1.0",
            })
        else:
            profile_sum = sum(float(v) for v in image_profile.values())
            if 0.95 <= profile_sum <= 1.05:
                findings.append({
                    "check": "image_profile",
                    "category": "config_completeness",
                    "severity": "PASS",
                    "message": f"Image profile defined, sums to {profile_sum:.2f}",
                    "value": profile_sum,
                    "expected": "0.95 - 1.05",
                })
            else:
                findings.append({
                    "check": "image_profile",
                    "category": "config_completeness",
                    "severity": "WARNING",
                    "message": f"Image profile sums to {profile_sum:.2f} (should be ~1.0)",
                    "value": profile_sum,
                    "expected": "0.95 - 1.05",
                })

        # Check 4: Campaign ID present and valid format
        campaign_id = campaign_data.get("campaign_id", "")

        if not campaign_id or not re.match(r"^\d+$", str(campaign_id)):
            findings.append({
                "check": "campaign_id",
                "category": "config_completeness",
                "severity": "CRITICAL",
                "message": f"Campaign ID missing or invalid: '{campaign_id}'",
                "value": campaign_id,
                "expected": "Non-empty string of digits",
            })
        else:
            findings.append({
                "check": "campaign_id",
                "category": "config_completeness",
                "severity": "PASS",
                "message": f"Campaign ID valid: {campaign_id}",
                "value": campaign_id,
                "expected": "Non-empty string of digits",
            })

        return findings

    # --- Category 2: Google Ads Alignment (Checks 5-10) ---

    def _check_google_ads_alignment(
        self, campaign_name: str, campaign_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        findings = []
        google_settings = campaign_data.get("google_ads_settings", {})

        if not google_settings:
            findings.append({
                "check": "google_ads_settings_missing",
                "category": "google_ads_alignment",
                "severity": "WARNING",
                "message": "No Google Ads settings available — run sync_config first",
                "value": None,
                "expected": "Synced Google Ads settings",
            })
            return findings

        # Check 5: Campaign status is ENABLED
        status = google_settings.get("campaign_status", "")
        if status == "ENABLED":
            findings.append({
                "check": "campaign_status",
                "category": "google_ads_alignment",
                "severity": "PASS",
                "message": "Campaign status is ENABLED",
                "value": status,
                "expected": "ENABLED",
            })
        else:
            findings.append({
                "check": "campaign_status",
                "category": "google_ads_alignment",
                "severity": "CRITICAL",
                "message": f"Campaign status is {status} — not serving ads",
                "value": status,
                "expected": "ENABLED",
            })

        # Check 6: Bidding strategy is MAXIMIZE_CONVERSION_VALUE
        bidding = google_settings.get("bidding_strategy_type", "")
        if bidding == "MAXIMIZE_CONVERSION_VALUE":
            findings.append({
                "check": "bidding_strategy",
                "category": "google_ads_alignment",
                "severity": "PASS",
                "message": "Bidding strategy is MAXIMIZE_CONVERSION_VALUE",
                "value": bidding,
                "expected": "MAXIMIZE_CONVERSION_VALUE",
            })
        else:
            findings.append({
                "check": "bidding_strategy",
                "category": "google_ads_alignment",
                "severity": "WARNING",
                "message": (
                    f"Bidding strategy is {bidding} — expected "
                    "MAXIMIZE_CONVERSION_VALUE for PMax with ROAS targets"
                ),
                "value": bidding,
                "expected": "MAXIMIZE_CONVERSION_VALUE",
            })

        # Check 7: Target ROAS is set and reasonable (100%-500%)
        target_roas = google_settings.get("target_roas")
        if target_roas is None:
            findings.append({
                "check": "target_roas",
                "category": "google_ads_alignment",
                "severity": "WARNING",
                "message": "Target ROAS is not set",
                "value": None,
                "expected": "100% - 500%",
            })
        else:
            # Google Ads API returns target_roas as a ratio (2.0 = 200%)
            raw = float(target_roas)
            target_roas_pct = raw * 100 if raw < 10 else raw
            if 100 <= target_roas_pct <= 500:
                findings.append({
                    "check": "target_roas",
                    "category": "google_ads_alignment",
                    "severity": "PASS",
                    "message": f"Target ROAS is {target_roas_pct:.0f}%",
                    "value": target_roas_pct,
                    "expected": "100% - 500%",
                })
            else:
                findings.append({
                    "check": "target_roas",
                    "category": "google_ads_alignment",
                    "severity": "WARNING",
                    "message": f"Target ROAS {target_roas_pct:.0f}% is outside reasonable range (100%-500%)",
                    "value": target_roas_pct,
                    "expected": "100% - 500%",
                })

        # Check 8: Budget exceeds seasonal minimum
        daily_budget = float(google_settings.get("daily_budget", 0))
        recommended = self.seasonal_budget["recommended_daily"]

        if daily_budget >= recommended:
            findings.append({
                "check": "budget_minimum",
                "category": "google_ads_alignment",
                "severity": "PASS",
                "message": f"Daily budget ${daily_budget:.0f} meets {self.season} minimum ${recommended:.0f}",
                "value": daily_budget,
                "expected": f">= ${recommended:.0f}",
            })
        else:
            findings.append({
                "check": "budget_minimum",
                "category": "google_ads_alignment",
                "severity": "WARNING",
                "message": f"Daily budget ${daily_budget:.0f} is below {self.season} recommended ${recommended:.0f}",
                "value": daily_budget,
                "expected": f">= ${recommended:.0f}",
            })

        # Check 9: Budget doesn't exceed seasonal max
        max_daily = self.seasonal_budget["max_daily"]

        if daily_budget <= max_daily:
            findings.append({
                "check": "budget_maximum",
                "category": "google_ads_alignment",
                "severity": "PASS",
                "message": f"Daily budget ${daily_budget:.0f} within {self.season} max ${max_daily:.0f}",
                "value": daily_budget,
                "expected": f"<= ${max_daily:.0f}",
            })
        else:
            findings.append({
                "check": "budget_maximum",
                "category": "google_ads_alignment",
                "severity": "INFO",
                "message": f"Daily budget ${daily_budget:.0f} exceeds {self.season} max ${max_daily:.0f}",
                "value": daily_budget,
                "expected": f"<= ${max_daily:.0f}",
            })

        # Check 10: Geo targeting is configured
        geo_targets = google_settings.get("geo_targets", [])
        if geo_targets:
            findings.append({
                "check": "geo_targeting",
                "category": "google_ads_alignment",
                "severity": "PASS",
                "message": f"Geo targeting configured ({len(geo_targets)} target(s))",
                "value": len(geo_targets),
                "expected": ">= 1 target",
            })
        else:
            findings.append({
                "check": "geo_targeting",
                "category": "google_ads_alignment",
                "severity": "WARNING",
                "message": "No geo targeting configured — campaign may serve globally",
                "value": 0,
                "expected": ">= 1 target",
            })

        return findings

    # --- Category 3: Performance Trends (Checks 11-14) ---

    def _check_performance_trends(self, campaign_name: str) -> List[Dict[str, Any]]:
        findings = []

        try:
            history = get_budget_history(campaign_name, weeks=8)
        except Exception as e:
            logger.warning("Could not load budget history for %s: %s", campaign_name, e)
            findings.append({
                "check": "budget_history_unavailable",
                "category": "performance_trends",
                "severity": "WARNING",
                "message": f"Could not load budget history: {e}",
                "value": None,
                "expected": "Budget history available",
            })
            return findings

        if not history:
            findings.append({
                "check": "budget_history_empty",
                "category": "performance_trends",
                "severity": "INFO",
                "message": "No budget history data available yet",
                "value": 0,
                "expected": ">= 1 week of data",
            })
            return findings

        # Check 11: ROAS vs seasonal target
        latest = history[0]  # Most recent (sorted descending)
        roas = float(latest.get("roas_percent", 0))
        target_roas = float(latest.get("target_roas_percent", 0))
        seasonal_target = self.seasonal_budget["target_roas"]

        # Use the higher of campaign target and seasonal target
        effective_target = max(target_roas, seasonal_target) if target_roas > 0 else seasonal_target

        if effective_target > 0 and roas > 0:
            gap_pct = ((effective_target - roas) / effective_target) * 100

            if gap_pct > 30:
                findings.append({
                    "check": "roas_vs_target",
                    "category": "performance_trends",
                    "severity": "CRITICAL",
                    "message": f"ROAS {roas:.0f}% is {gap_pct:.0f}% below target {effective_target:.0f}%",
                    "value": roas,
                    "expected": f">= {effective_target:.0f}%",
                })
            elif gap_pct > 10:
                findings.append({
                    "check": "roas_vs_target",
                    "category": "performance_trends",
                    "severity": "WARNING",
                    "message": f"ROAS {roas:.0f}% is {gap_pct:.0f}% below target {effective_target:.0f}%",
                    "value": roas,
                    "expected": f">= {effective_target:.0f}%",
                })
            else:
                findings.append({
                    "check": "roas_vs_target",
                    "category": "performance_trends",
                    "severity": "PASS",
                    "message": f"ROAS {roas:.0f}% is on target ({effective_target:.0f}%)",
                    "value": roas,
                    "expected": f">= {effective_target:.0f}%",
                })
        else:
            findings.append({
                "check": "roas_vs_target",
                "category": "performance_trends",
                "severity": "INFO",
                "message": "Insufficient ROAS data for comparison",
                "value": roas,
                "expected": "ROAS and target data available",
            })

        # Check 12: ROAS trend direction (declining 3+ consecutive weeks)
        if len(history) >= 4:
            # history is sorted descending: [newest, ..., oldest]
            last_4_roas = [float(w.get("roas_percent", 0)) for w in history[:4]]
            declining_count = 0
            for i in range(len(last_4_roas) - 1):
                if last_4_roas[i] < last_4_roas[i + 1]:
                    declining_count += 1
                else:
                    break

            # Display oldest-to-newest for readability
            trend_display = " -> ".join(f"{r:.0f}%" for r in reversed(last_4_roas))

            if declining_count >= 3:
                findings.append({
                    "check": "roas_trend",
                    "category": "performance_trends",
                    "severity": "WARNING",
                    "message": f"ROAS declining {declining_count} consecutive weeks: {trend_display}",
                    "value": last_4_roas,
                    "expected": "Stable or improving trend",
                })
            else:
                findings.append({
                    "check": "roas_trend",
                    "category": "performance_trends",
                    "severity": "PASS",
                    "message": f"ROAS trend stable: {trend_display}",
                    "value": last_4_roas,
                    "expected": "Stable or improving trend",
                })
        else:
            findings.append({
                "check": "roas_trend",
                "category": "performance_trends",
                "severity": "INFO",
                "message": f"Only {len(history)} week(s) of data — need 4 for trend analysis",
                "value": len(history),
                "expected": ">= 4 weeks of data",
            })

        # Check 13: Budget utilization healthy (not <50% for 2+ consecutive weeks)
        low_util_weeks = 0
        for week in history[:4]:
            util = float(week.get("budget_utilization_percent", 0))
            if util < 50:
                low_util_weeks += 1
            else:
                break

        latest_util = float(history[0].get("budget_utilization_percent", 0))

        if low_util_weeks >= 2:
            findings.append({
                "check": "budget_utilization",
                "category": "performance_trends",
                "severity": "WARNING",
                "message": (
                    f"Budget utilization below 50% for {low_util_weeks} consecutive "
                    f"weeks (latest: {latest_util:.0f}%)"
                ),
                "value": latest_util,
                "expected": ">= 50% utilization",
            })
        else:
            findings.append({
                "check": "budget_utilization",
                "category": "performance_trends",
                "severity": "PASS",
                "message": f"Budget utilization healthy at {latest_util:.0f}%",
                "value": latest_util,
                "expected": ">= 50% utilization",
            })

        # Check 14: Spend volatility (>40% week-to-week swing)
        if len(history) >= 2:
            spends = [float(w.get("total_spend", 0)) for w in history[:4]]
            max_swing = 0
            for i in range(len(spends) - 1):
                if spends[i + 1] > 0:
                    swing = abs(spends[i] - spends[i + 1]) / spends[i + 1] * 100
                    max_swing = max(max_swing, swing)

            if max_swing > 40:
                findings.append({
                    "check": "spend_volatility",
                    "category": "performance_trends",
                    "severity": "INFO",
                    "message": f"Spend volatility {max_swing:.0f}% — large week-to-week swings suggest instability",
                    "value": max_swing,
                    "expected": "<= 40% swing",
                })
            else:
                findings.append({
                    "check": "spend_volatility",
                    "category": "performance_trends",
                    "severity": "PASS",
                    "message": f"Spend volatility {max_swing:.0f}% within normal range",
                    "value": max_swing,
                    "expected": "<= 40% swing",
                })
        else:
            findings.append({
                "check": "spend_volatility",
                "category": "performance_trends",
                "severity": "INFO",
                "message": "Not enough data for volatility analysis",
                "value": None,
                "expected": ">= 2 weeks of data",
            })

        return findings

    # --- Category 4: Asset Health (Checks 15-18) ---

    def _check_asset_health(self, campaign_name: str) -> List[Dict[str, Any]]:
        findings = []

        try:
            assets = get_latest_asset_records(campaign_name)
        except Exception as e:
            logger.warning("Could not load assets for %s: %s", campaign_name, e)
            return [{
                "check": "asset_data_unavailable",
                "category": "asset_health",
                "severity": "WARNING",
                "message": f"Could not load asset data: {e}",
                "value": None,
                "expected": "Asset data available",
            }]

        active_assets = [a for a in assets if a.get("status") == "active"]

        # Check 15: Text asset minimums met (PMax requires >=3 headlines, >=2 descriptions, >=1 long headline)
        headlines = [a for a in active_assets if a.get("asset_type") == "HEADLINE"]
        long_headlines = [a for a in active_assets if a.get("asset_type") == "LONG_HEADLINE"]
        descriptions = [a for a in active_assets if a.get("asset_type") == "DESCRIPTION"]

        missing = []
        if len(headlines) < 3:
            missing.append(f"headlines ({len(headlines)}/3)")
        if len(descriptions) < 2:
            missing.append(f"descriptions ({len(descriptions)}/2)")
        if len(long_headlines) < 1:
            missing.append(f"long headlines ({len(long_headlines)}/1)")

        if missing:
            findings.append({
                "check": "text_asset_minimums",
                "category": "asset_health",
                "severity": "CRITICAL",
                "message": f"Below PMax minimums: {', '.join(missing)}",
                "value": {
                    "headlines": len(headlines),
                    "descriptions": len(descriptions),
                    "long_headlines": len(long_headlines),
                },
                "expected": {"headlines": ">= 3", "descriptions": ">= 2", "long_headlines": ">= 1"},
            })
        else:
            findings.append({
                "check": "text_asset_minimums",
                "category": "asset_health",
                "severity": "PASS",
                "message": (
                    f"Text asset minimums met: {len(headlines)} headlines, "
                    f"{len(descriptions)} descriptions, {len(long_headlines)} long headlines"
                ),
                "value": {
                    "headlines": len(headlines),
                    "descriptions": len(descriptions),
                    "long_headlines": len(long_headlines),
                },
                "expected": {"headlines": ">= 3", "descriptions": ">= 2", "long_headlines": ">= 1"},
            })

        # Check 16: Image format coverage (at least 1 landscape, 1 square, 1 portrait)
        try:
            images = get_images_for_campaign(campaign_name)
        except Exception as e:
            logger.warning("Could not load images for %s: %s", campaign_name, e)
            images = []

        formats_present = set()
        for image in images:
            for mapping in image.get("google_ads_assets", []):
                if (
                    mapping.get("campaign_name") == campaign_name
                    and not mapping.get("date_unlinked")
                ):
                    ft = mapping.get("field_type", "")
                    if ft:
                        formats_present.add(ft)

        required_formats = {"MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE"}
        missing_formats = required_formats - formats_present

        if missing_formats:
            friendly = [f.replace("_", " ").lower() for f in sorted(missing_formats)]
            findings.append({
                "check": "image_format_coverage",
                "category": "asset_health",
                "severity": "CRITICAL",
                "message": f"Missing image formats: {', '.join(friendly)} — PMax cannot optimize all placements",
                "value": sorted(formats_present),
                "expected": sorted(required_formats),
            })
        else:
            findings.append({
                "check": "image_format_coverage",
                "category": "asset_health",
                "severity": "PASS",
                "message": "All image formats present (landscape, square, portrait)",
                "value": sorted(formats_present),
                "expected": sorted(required_formats),
            })

        # Check 17: Asset freshness (oldest active asset >180 days)
        oldest_days = 0
        oldest_asset_type = None
        for asset in active_assets:
            date_added = asset.get("date_added") or asset.get("created_at", "")
            if date_added:
                try:
                    added_dt = datetime.fromisoformat(date_added.replace("Z", "+00:00"))
                    now = datetime.utcnow().replace(tzinfo=added_dt.tzinfo)
                    age_days = (now - added_dt).days
                    if age_days > oldest_days:
                        oldest_days = age_days
                        oldest_asset_type = asset.get("asset_type", "asset")
                except (ValueError, TypeError):
                    pass

        if oldest_days > 180:
            findings.append({
                "check": "asset_freshness",
                "category": "asset_health",
                "severity": "WARNING",
                "message": (
                    f"Oldest active {oldest_asset_type.lower() if oldest_asset_type else 'asset'} "
                    f"is {oldest_days} days old — consider refreshing"
                ),
                "value": oldest_days,
                "expected": "<= 180 days",
            })
        else:
            findings.append({
                "check": "asset_freshness",
                "category": "asset_health",
                "severity": "PASS",
                "message": f"All assets under 180 days old (oldest: {oldest_days} days)",
                "value": oldest_days,
                "expected": "<= 180 days",
            })

        # Check 18: Kill rate not excessive (>40% killed in last 60 days)
        try:
            graveyard = get_graveyard_assets(campaign_name)
        except Exception as e:
            logger.warning("Could not load graveyard for %s: %s", campaign_name, e)
            graveyard = []

        cutoff = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent_kills = [a for a in graveyard if a.get("date_killed", "") >= cutoff]
        total_active = len(active_assets)
        total_pool = total_active + len(recent_kills)

        if total_pool > 0:
            kill_rate = len(recent_kills) / total_pool * 100
            if kill_rate > 40:
                findings.append({
                    "check": "kill_rate",
                    "category": "asset_health",
                    "severity": "WARNING",
                    "message": (
                        f"Kill rate {kill_rate:.0f}% in last 60 days "
                        f"({len(recent_kills)} killed of {total_pool} total) — systemic issue possible"
                    ),
                    "value": kill_rate,
                    "expected": "<= 40%",
                })
            else:
                findings.append({
                    "check": "kill_rate",
                    "category": "asset_health",
                    "severity": "PASS",
                    "message": (
                        f"Kill rate {kill_rate:.0f}% in last 60 days "
                        f"({len(recent_kills)} killed of {total_pool} total)"
                    ),
                    "value": kill_rate,
                    "expected": "<= 40%",
                })
        else:
            findings.append({
                "check": "kill_rate",
                "category": "asset_health",
                "severity": "PASS",
                "message": "No asset turnover data available",
                "value": 0,
                "expected": "<= 40%",
            })

        return findings

    # --- Category 5: Image Composition (Checks 19-20) ---

    def _check_image_composition(
        self, campaign_name: str, campaign_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        findings = []

        image_profile = campaign_data.get("image_profile", {})
        if not image_profile:
            findings.append({
                "check": "image_composition",
                "category": "image_composition",
                "severity": "INFO",
                "message": "No image profile defined — skipping composition checks",
                "value": None,
                "expected": "Image profile defined",
            })
            return findings

        try:
            all_images = get_images_for_campaign(campaign_name)
            images = _dedupe_campaign_images(all_images, campaign_name)
        except Exception as e:
            logger.warning("Could not load images for %s: %s", campaign_name, e)
            return [{
                "check": "image_data_unavailable",
                "category": "image_composition",
                "severity": "WARNING",
                "message": f"Could not load image data: {e}",
                "value": None,
                "expected": "Image data available",
            }]

        total = len(images)

        # Check 20: Total image count adequate (need 10+ for PMax variety)
        if total < 10:
            findings.append({
                "check": "image_count",
                "category": "image_composition",
                "severity": "WARNING",
                "message": f"Only {total} images — PMax needs visual variety (recommend 10+)",
                "value": total,
                "expected": ">= 10",
            })
        else:
            findings.append({
                "check": "image_count",
                "category": "image_composition",
                "severity": "PASS",
                "message": f"{total} images in asset group",
                "value": total,
                "expected": ">= 10",
            })

        # Check 19: No category >15% underrepresented
        if total > 0:
            category_counts = {cat: 0 for cat in CONTENT_CATEGORIES}
            for image in images:
                cat = image.get("content_category", "")
                if cat in category_counts:
                    category_counts[cat] += 1

            underrepresented = []
            for category in CONTENT_CATEGORIES:
                actual_pct = category_counts[category] / total * 100
                target_pct = float(image_profile.get(category, 0)) * 100
                delta = target_pct - actual_pct

                if delta > 15:
                    underrepresented.append({
                        "category": category,
                        "actual_pct": round(actual_pct, 1),
                        "target_pct": round(target_pct, 1),
                        "delta": round(delta, 1),
                    })

            if underrepresented:
                for gap in underrepresented:
                    cat_name = gap["category"].replace("_", " ")
                    findings.append({
                        "check": "image_category_gap",
                        "category": "image_composition",
                        "severity": "WARNING",
                        "message": (
                            f"{cat_name} is {gap['delta']:.0f}% underrepresented "
                            f"({gap['actual_pct']:.0f}% actual vs {gap['target_pct']:.0f}% target)"
                        ),
                        "value": gap["actual_pct"],
                        "expected": f"{gap['target_pct']:.0f}% (+/- 15%)",
                    })
            else:
                findings.append({
                    "check": "image_category_gap",
                    "category": "image_composition",
                    "severity": "PASS",
                    "message": "Image composition within targets (no category >15% underrepresented)",
                    "value": category_counts,
                    "expected": "All categories within 15% of target",
                })
        else:
            findings.append({
                "check": "image_category_gap",
                "category": "image_composition",
                "severity": "INFO",
                "message": "No images to analyze composition",
                "value": 0,
                "expected": "Images available for composition analysis",
            })

        return findings

    # --- Score Calculation ---

    def _calculate_score(self, findings: List[Dict[str, Any]]) -> Tuple[int, str]:
        """Calculate health score (0-100) and letter grade from findings."""
        score = 100
        for finding in findings:
            score -= SEVERITY_DEDUCTIONS.get(finding["severity"], 0)

        score = max(0, score)

        grade = "F"
        for threshold, letter in GRADE_SCALE:
            if score >= threshold:
                grade = letter
                break

        return score, grade

    # --- Claude Executive Summary ---

    def _generate_summary(
        self, all_findings: List[Dict[str, Any]], campaign_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate executive summary using Claude API."""
        if not self.anthropic_key:
            return self._fallback_summary(all_findings, campaign_results)

        # Build concise findings for the prompt (exclude PASS)
        findings_for_prompt = []
        for campaign_name, result in campaign_results.items():
            findings_for_prompt.append({
                "campaign": campaign_name,
                "score": result["health_score"],
                "grade": result["grade"],
                "findings": [
                    {
                        "check": f["check"],
                        "severity": f["severity"],
                        "message": f["message"],
                    }
                    for f in result["findings"]
                    if f["severity"] != "PASS"
                ],
            })

        prompt = AUDIT_SUMMARY_PROMPT.format(
            findings_json=json.dumps(findings_for_prompt, indent=2, default=str),
        )

        try:
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
                        {"role": "user", "content": prompt},
                    ],
                },
            )

            if not response.ok:
                logger.warning(
                    "Claude API error for audit summary: %d — %s",
                    response.status_code, response.text[:300],
                )
                return self._fallback_summary(all_findings, campaign_results)

            result = response.json()
            text = result["content"][0]["text"].strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            summary_data = json.loads(text)
            return {
                "summary": summary_data.get("summary", ""),
                "recommendations": summary_data.get("recommendations", []),
            }

        except Exception as e:
            logger.warning("Failed to generate Claude summary: %s", e)
            return self._fallback_summary(all_findings, campaign_results)

    def _fallback_summary(
        self, all_findings: List[Dict[str, Any]], campaign_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate basic summary when Claude API is unavailable."""
        critical = [f for f in all_findings if f["severity"] == "CRITICAL"]
        warnings = [f for f in all_findings if f["severity"] == "WARNING"]

        parts = []
        for name, result in campaign_results.items():
            parts.append(f"{name}: {result['health_score']}/100 ({result['grade']})")

        summary = f"Audit complete. {', '.join(parts)}."
        if critical:
            summary += f" {len(critical)} critical issue(s) need immediate attention."
        if warnings:
            summary += f" {len(warnings)} warning(s) to review."

        recommendations = []
        for f in critical:
            recommendations.append(f"[CRITICAL] {f['message']}")
        for f in warnings[:5]:
            recommendations.append(f"[WARNING] {f['message']}")

        return {
            "summary": summary,
            "recommendations": recommendations,
        }

    # --- Slack Report Formatting ---

    def format_audit_report(self, results: Dict[str, Any]) -> str:
        """Format full audit results as a Slack message."""
        lines = [
            "\U0001f4cb *Campaign Health Audit*",
            "",
            "\u2501" * 27,
        ]

        for campaign_name, result in results["campaigns"].items():
            score = result["health_score"]
            grade = result["grade"]
            findings = result["findings"]

            passed = [f for f in findings if f["severity"] == "PASS"]
            warnings = [f for f in findings if f["severity"] == "WARNING"]
            criticals = [f for f in findings if f["severity"] == "CRITICAL"]
            infos = [f for f in findings if f["severity"] == "INFO"]

            lines.append("")
            lines.append(f"\U0001f3e5 *{campaign_name} \u2014 Score: {score}/100 ({grade})*")
            lines.append("")
            lines.append(f"\u2705 {len(passed)} checks passed")

            if warnings:
                lines.append(f"\u26a0\ufe0f {len(warnings)} warning(s)")
                for w in warnings:
                    lines.append(f"  \u2022 {w['message']}")
            else:
                lines.append(f"\u26a0\ufe0f 0 warnings")

            if criticals:
                lines.append(f"\U0001f6a8 {len(criticals)} critical issue(s)")
                for c in criticals:
                    lines.append(f"  \u2022 {c['message']}")
            else:
                lines.append(f"\U0001f6a8 0 critical issues")

            if infos:
                lines.append(f"\U0001f4ca {len(infos)} info note(s)")

        lines.append("")
        lines.append("\u2501" * 27)

        if results.get("summary"):
            lines.append("")
            lines.append("*Executive Summary:*")
            lines.append(results["summary"])

        if results.get("recommendations"):
            lines.append("")
            lines.append("*Priority Actions:*")
            for i, rec in enumerate(results["recommendations"], 1):
                lines.append(f"  {i}. {rec}")

        return "\n".join(lines)
