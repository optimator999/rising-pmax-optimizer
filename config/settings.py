"""Environment-aware configuration for Rising PMax Optimizer."""

import os
import logging
from typing import Optional

import boto3
from dotenv import load_dotenv

load_dotenv()

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("DEPLOY_REGION", "us-east-2")

# Logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rising-pmax")

# AWS clients
_ssm_client: Optional[boto3.client] = None
_dynamodb_resource: Optional[boto3.resource] = None


def get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=AWS_REGION)
    return _ssm_client


def get_dynamodb_resource():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb_resource


def get_parameter(name: str, encrypted: bool = True) -> str:
    """Fetch a parameter from AWS Parameter Store."""
    ssm = get_ssm_client()
    response = ssm.get_parameter(Name=name, WithDecryption=encrypted)
    return response["Parameter"]["Value"]


# S3 bucket for image assets
S3_IMAGE_BUCKET = os.getenv("S3_IMAGE_BUCKET", "rising-pmax")

# Campaign configuration
CAMPAIGNS = {
    "Core Brand": {
        "campaign_id": "22483972722",
        "asset_group": "Core Brand",
        "slug": "core_brand",
        "image_profile": {
            "product_hero": 0.20,
            "product_in_use": 0.30,
            "lifestyle_with_product": 0.30,
            "lifestyle_no_product": 0.10,
            "product_detail": 0.10,
        },
    },
    "Replacement Nets": {
        "campaign_id": "22494027316",
        "asset_group": "Replacement Nets",
        "slug": "replacement_nets",
        "image_profile": {
            "product_hero": 0.25,
            "product_detail": 0.30,
            "product_in_use": 0.25,
            "lifestyle_with_product": 0.15,
            "lifestyle_no_product": 0.05,
        },
    },
}

# Character limits for Google Ads asset types
ASSET_CHARACTER_LIMITS = {
    "HEADLINE": 30,
    "LONG_HEADLINE": 90,
    "DESCRIPTION": 90,
}

# Rising voice guidelines (used in Claude API prompts)
RISING_VOICE_GUIDELINES = """Write in the Rising Fishing voice. The tone should be calm, honest, and human. \
No hype, no sales talk, and no cleverness for the sake of being clever. \
Avoid em dashes. Sound like a small crew of anglers who build their own gear \
and spend real time on the water. Use simple, clear sentences with a lived-in feel. \
Everything should read like a conversation at a tailgate or in the shop at the \
end of a day on the river.

Rising messages should be grounded in real experience. Focus on what matters on \
the water. Give readers one true idea at a time. Never preach and never lecture. \
If the message is product focused, connect the gear to real moments on the river. \
If the message is story focused, give one honest moment that feels familiar to \
anyone who fishes. If the message is educational, explain things in plain language \
and with care.

Keep CTAs soft. Respect the reader's attention. Speak with patience, craft, and \
purpose. Rising is about tools built to last, stories worth telling, and the people \
who fish. Everything written should feel steady, trustworthy, and from a crew that \
does things the right way."""
