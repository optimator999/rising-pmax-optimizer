"""Claude API integration for generating replacement ad copy."""

import logging
from typing import Any, Dict, List, Optional

import anthropic

from config.settings import ASSET_CHARACTER_LIMITS, RISING_VOICE_GUIDELINES

logger = logging.getLogger("rising-pmax.copy_generator")

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 200


class CopyGenerator:
    """Generates Rising-voice replacement copy via Claude API."""

    def __init__(self, api_key: str):
        """Initialize Claude API client."""
        self.client = anthropic.Anthropic(api_key=api_key)
        logger.info("Claude API client initialized (model: %s)", MODEL)

    def build_prompt(
        self,
        asset: Dict[str, Any],
        kill_reason: str,
        diagnosis: str,
        graveyard: List[Dict[str, Any]],
    ) -> str:
        """Construct the prompt for Claude."""
        asset_type = asset.get("asset_type", "HEADLINE")
        max_length = ASSET_CHARACTER_LIMITS.get(asset_type, 30)

        # Build graveyard section
        graveyard_lines = []
        for grave in graveyard[-20:]:  # Last 20 killed assets
            graveyard_lines.append(
                f"- \"{grave.get('asset_text', '')}\" ({grave.get('asset_type', '')}) "
                f"- Killed: {grave.get('kill_reason', 'unknown')}"
            )
        graveyard_section = "\n".join(graveyard_lines) if graveyard_lines else "None yet"

        prompt = f"""You are a copywriter for Rising Fishing, a fly fishing gear company. \
Your task is to generate replacement copy for underperforming Google Ads assets.

RISING VOICE GUIDELINES:
{RISING_VOICE_GUIDELINES}

WHAT WORKS:
- Direct product focus: "Fly Fishing Nets"
- Origin/credibility: "USA Made Fly Fishing Nets"
- Material specificity: "Aluminum Nets"
- Real conditions: "Built for Rivers and Big Fish"

WHAT FAILS:
- Hype language: "Innovative", "Premier", "Top-of-the-Line", "Unmatched"
- Vague benefits: "Experience", "Destination"
- Gatekeeping: "For Serious Anglers"
- Hyperbole: "Nets That Land Monsters"

GRAVEYARD (what has failed before):
{graveyard_section}

ASSET TO REPLACE:
Type: {asset_type}
Text: {asset.get('asset_text', '')}
Performance: {asset.get('impressions', 0)} impressions, \
{asset.get('ctr', 0)}% CTR, {asset.get('conversions', 0)} conversions, \
${asset.get('cost', 0):.2f} spent
Kill reason: {kill_reason}
Diagnosis: {diagnosis}

TASK:
Generate ONE replacement {asset_type} that:
1. Follows Rising voice (no hype, direct, specific)
2. Avoids patterns that failed in the graveyard
3. Addresses why the original failed

Respond with ONLY the replacement text (no explanation, no quotes, no formatting).
Maximum length: {max_length} characters

Replacement text:"""

        return prompt

    def generate_replacement(
        self,
        asset: Dict[str, Any],
        kill_reason: str,
        diagnosis: str,
        graveyard: List[Dict[str, Any]],
    ) -> Optional[Dict[str, str]]:
        """Generate a single replacement for a killed asset.

        Returns dict with 'text' and 'strategy', or None on failure.
        """
        asset_type = asset.get("asset_type", "HEADLINE")
        max_length = ASSET_CHARACTER_LIMITS.get(asset_type, 30)

        prompt = self.build_prompt(asset, kill_reason, diagnosis, graveyard)

        for attempt in range(1, 4):
            try:
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )

                replacement_text = response.content[0].text.strip()
                replacement_text = replacement_text.strip('"').strip("'")

                # Validate length
                if len(replacement_text) > max_length:
                    logger.warning(
                        "Replacement '%s' exceeds %d chars (%d), retrying",
                        replacement_text,
                        max_length,
                        len(replacement_text),
                    )
                    # Add length reminder and retry
                    prompt += (
                        f"\n\nIMPORTANT: Your previous response was "
                        f"{len(replacement_text)} characters. "
                        f"Maximum is {max_length}. Try again, shorter:"
                    )
                    continue

                if not replacement_text:
                    logger.warning("Empty replacement on attempt %d", attempt)
                    continue

                logger.info(
                    "Generated replacement for '%s': '%s'",
                    asset.get("asset_text", "?"),
                    replacement_text,
                )

                return {
                    "text": replacement_text,
                    "strategy": diagnosis,
                }

            except anthropic.APIError as e:
                logger.error(
                    "Claude API error (attempt %d/3): %s", attempt, e
                )
                if attempt == 3:
                    return None

        return None

    def generate_replacements(
        self,
        flagged_assets: List[Dict[str, Any]],
        graveyard: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, str]]:
        """Generate replacements for all flagged assets.

        Returns dict mapping asset_id to replacement info.
        """
        replacements = {}

        for asset in flagged_assets:
            asset_id = asset.get("asset_id", "unknown")
            kill_reason = asset.get("kill_reason", "unknown")
            diagnosis = asset.get("diagnosis", "unknown")

            result = self.generate_replacement(
                asset, kill_reason, diagnosis, graveyard
            )

            if result:
                replacements[asset_id] = result
            else:
                logger.error(
                    "Failed to generate replacement for '%s'",
                    asset.get("asset_text", "?"),
                )

        logger.info(
            "Generated %d of %d replacements",
            len(replacements),
            len(flagged_assets),
        )
        return replacements
