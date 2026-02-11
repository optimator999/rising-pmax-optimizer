"""AWS helper utilities for Parameter Store and DynamoDB."""

import logging
import time
from typing import Any, Callable, TypeVar

from config.settings import get_ssm_client

logger = logging.getLogger("rising-pmax.aws")

T = TypeVar("T")


def get_parameter(name: str, encrypted: bool = True) -> str:
    """Fetch a parameter from AWS Parameter Store with retry."""
    return _retry(
        lambda: get_ssm_client().get_parameter(Name=name, WithDecryption=encrypted)[
            "Parameter"
        ]["Value"],
        description=f"get_parameter({name})",
    )


def get_google_ads_credentials() -> dict:
    """Load all Google Ads credentials from Parameter Store."""
    return {
        "developer_token": get_parameter("/Google_Ads/DEVELOPER_TOKEN"),
        "client_id": get_parameter("/Google_Ads/CLIENT_ID"),
        "client_secret": get_parameter("/Google_Ads/CLIENT_SECRET"),
        "refresh_token": get_parameter("/Google_Ads/REFRESH_TOKEN"),
        "customer_id": get_parameter("/Google_Ads/CUSTOMER_ID"),
        "client_customer_id": get_parameter("/Google_Ads/CLIENT_CUSTOMER_ID"),
    }


def get_slack_credentials() -> dict:
    """Load Slack credentials from Parameter Store."""
    return {
        "token": get_parameter("/Slack/TOKEN"),
        "channel": get_parameter("/Slack/PMAX_CHANNEL", encrypted=False),
    }


def get_shopify_credentials() -> dict:
    """Load Shopify credentials from Parameter Store."""
    return {
        "store_url": get_parameter("/Shopify/PROD_STORE", encrypted=False),
        "access_token": get_parameter("/Shopify/ACCESS_TOKEN"),
    }


def get_anthropic_api_key() -> str:
    """Load Anthropic API key from Parameter Store."""
    return get_parameter("/Anthropic/API_KEY")


def _retry(
    func: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    description: str = "operation",
) -> T:
    """Retry a callable with exponential backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except Exception as e:
            if attempt == max_attempts:
                logger.error(
                    "%s failed after %d attempts: %s", description, max_attempts, e
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s attempt %d failed (%s), retrying in %.1fs",
                description,
                attempt,
                e,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable")  # Satisfies type checker
