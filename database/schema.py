"""DynamoDB table definitions for Rising PMax Optimizer."""

import logging

import boto3

from config.settings import AWS_REGION

logger = logging.getLogger("rising-pmax.schema")

ASSET_PERFORMANCE_TABLE = {
    "TableName": "rising_asset_performance",
    "KeySchema": [
        {"AttributeName": "asset_id", "KeyType": "HASH"},
        {"AttributeName": "report_date", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "asset_id", "AttributeType": "S"},
        {"AttributeName": "report_date", "AttributeType": "S"},
        {"AttributeName": "campaign_name", "AttributeType": "S"},
        {"AttributeName": "status", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "campaign-status-index",
            "KeySchema": [
                {"AttributeName": "campaign_name", "KeyType": "HASH"},
                {"AttributeName": "status", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

ASSET_GRAVEYARD_TABLE = {
    "TableName": "rising_asset_graveyard",
    "KeySchema": [
        {"AttributeName": "campaign_name", "KeyType": "HASH"},
        {"AttributeName": "date_killed", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "campaign_name", "AttributeType": "S"},
        {"AttributeName": "date_killed", "AttributeType": "S"},
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

BUDGET_PERFORMANCE_TABLE = {
    "TableName": "rising_budget_performance",
    "KeySchema": [
        {"AttributeName": "campaign_name", "KeyType": "HASH"},
        {"AttributeName": "week_ending", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "campaign_name", "AttributeType": "S"},
        {"AttributeName": "week_ending", "AttributeType": "S"},
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

ALL_TABLES = [ASSET_PERFORMANCE_TABLE, ASSET_GRAVEYARD_TABLE, BUDGET_PERFORMANCE_TABLE]


def create_tables(dynamodb_resource=None):
    """Create all DynamoDB tables. Skips tables that already exist."""
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)

    existing = [t.name for t in dynamodb_resource.tables.all()]

    for table_def in ALL_TABLES:
        name = table_def["TableName"]
        if name in existing:
            logger.info("Table %s already exists, skipping", name)
            continue

        params = {
            "TableName": name,
            "KeySchema": table_def["KeySchema"],
            "AttributeDefinitions": table_def["AttributeDefinitions"],
            "BillingMode": table_def["BillingMode"],
        }
        if "GlobalSecondaryIndexes" in table_def:
            params["GlobalSecondaryIndexes"] = table_def["GlobalSecondaryIndexes"]

        table = dynamodb_resource.create_table(**params)
        table.wait_until_exists()
        logger.info("Created table %s", name)

    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_tables()
