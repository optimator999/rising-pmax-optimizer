terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# ---------- DynamoDB Tables ----------

resource "aws_dynamodb_table" "asset_performance" {
  name         = "rising_asset_performance"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "asset_id"
  range_key    = "report_date"

  attribute {
    name = "asset_id"
    type = "S"
  }

  attribute {
    name = "report_date"
    type = "S"
  }

  attribute {
    name = "campaign_name"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "campaign-status-index"
    hash_key        = "campaign_name"
    range_key       = "status"
    projection_type = "ALL"
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "asset_graveyard" {
  name         = "rising_asset_graveyard"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "campaign_name"
  range_key    = "date_killed"

  attribute {
    name = "campaign_name"
    type = "S"
  }

  attribute {
    name = "date_killed"
    type = "S"
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "budget_performance" {
  name         = "rising_budget_performance"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "campaign_name"
  range_key    = "week_ending"

  attribute {
    name = "campaign_name"
    type = "S"
  }

  attribute {
    name = "week_ending"
    type = "S"
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "image_registry" {
  name         = "rising_image_registry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "image_id"

  attribute {
    name = "image_id"
    type = "S"
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

# ---------- S3 Bucket ----------

resource "aws_s3_bucket" "pmax_images" {
  bucket = "rising-pmax"

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "pmax_images" {
  bucket = aws_s3_bucket.pmax_images.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pmax_images" {
  bucket = aws_s3_bucket.pmax_images.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "pmax_images" {
  bucket = aws_s3_bucket.pmax_images.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------- IAM Role ----------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_role" {
  name               = "rising-pmax-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

data "aws_iam_policy_document" "lambda_policy" {
  # DynamoDB access
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.asset_performance.arn,
      "${aws_dynamodb_table.asset_performance.arn}/index/*",
      aws_dynamodb_table.asset_graveyard.arn,
      aws_dynamodb_table.budget_performance.arn,
      aws_dynamodb_table.image_registry.arn,
    ]
  }

  # S3 access for image assets
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.pmax_images.arn,
      "${aws_s3_bucket.pmax_images.arn}/*",
    ]
  }

  # Parameter Store access
  statement {
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      "arn:aws:ssm:${var.aws_region}:*:parameter/Google_Ads/*",
      "arn:aws:ssm:${var.aws_region}:*:parameter/Slack/*",
      "arn:aws:ssm:${var.aws_region}:*:parameter/Anthropic/*",
      "arn:aws:ssm:${var.aws_region}:*:parameter/Shopify/*",
    ]
  }

  # KMS for decrypting SecureString parameters
  statement {
    actions = [
      "kms:Decrypt",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.aws_region}:*:*"]
  }
}

resource "aws_iam_role_policy" "lambda_policy" {
  name   = "rising-pmax-lambda-policy"
  role   = aws_iam_role.lambda_role.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}

# ---------- Lambda Layer ----------

resource "aws_lambda_layer_version" "dependencies" {
  s3_bucket           = aws_s3_bucket.pmax_images.id
  s3_key              = "deployments/lambda_layer.zip"
  layer_name          = "rising-pmax-dependencies"
  compatible_runtimes = ["python3.12"]
  description         = "Dependencies for Rising PMax Optimizer (with Pillow)"
}

# ---------- Lambda Functions ----------

resource "aws_lambda_function" "weekly_review" {
  filename         = "${path.module}/../../packages/weekly_review.zip"
  function_name    = "rising-weekly-review"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_functions.weekly_review.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  source_code_hash = filebase64sha256("${path.module}/../../packages/weekly_review.zip")

  layers = [aws_lambda_layer_version.dependencies.arn]

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      DEPLOY_REGION = var.aws_region
    }
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_lambda_function" "verify_upload" {
  filename         = "${path.module}/../../packages/verify_upload.zip"
  function_name    = "rising-verify-upload"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_functions.verify_upload.lambda_handler"
  runtime          = "python3.12"
  timeout          = 180
  memory_size      = 256
  source_code_hash = filebase64sha256("${path.module}/../../packages/verify_upload.zip")

  layers = [aws_lambda_layer_version.dependencies.arn]

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      DEPLOY_REGION = var.aws_region
    }
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_lambda_function" "image_ops" {
  filename         = "${path.module}/../../packages/image_ops.zip"
  function_name    = "rising-image-ops"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_functions.image_ops.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 1024
  source_code_hash = filebase64sha256("${path.module}/../../packages/image_ops.zip")

  layers = [aws_lambda_layer_version.dependencies.arn]

  environment {
    variables = {
      ENVIRONMENT   = var.environment
      DEPLOY_REGION = var.aws_region
      S3_IMAGE_BUCKET = aws_s3_bucket.pmax_images.id
    }
  }

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

# ---------- EventBridge Schedules ----------

resource "aws_cloudwatch_event_rule" "weekly_review" {
  name                = "rising-weekly-review-trigger"
  description         = "Run asset analysis every Monday at 6am MT (1pm UTC)"
  schedule_expression = "cron(0 13 ? * MON *)"

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "weekly_review" {
  rule = aws_cloudwatch_event_rule.weekly_review.name
  arn  = aws_lambda_function.weekly_review.arn
}

resource "aws_lambda_permission" "weekly_review_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.weekly_review.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_review.arn
}

resource "aws_cloudwatch_event_rule" "verify_upload" {
  name                = "rising-verify-upload-trigger"
  description         = "Verify uploads every Thursday at 6am MT (1pm UTC)"
  schedule_expression = "cron(0 13 ? * THU *)"

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "verify_upload" {
  rule = aws_cloudwatch_event_rule.verify_upload.name
  arn  = aws_lambda_function.verify_upload.arn
}

resource "aws_lambda_permission" "verify_upload_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.verify_upload.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.verify_upload.arn
}

resource "aws_cloudwatch_event_rule" "sync_config" {
  name                = "rising-sync-config-trigger"
  description         = "Sync Google Ads settings daily at 6am MT (1pm UTC)"
  schedule_expression = "cron(0 13 ? * * *)"

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "sync_config" {
  rule  = aws_cloudwatch_event_rule.sync_config.name
  arn   = aws_lambda_function.image_ops.arn
  input = jsonencode({ action = "sync_config" })
}

resource "aws_lambda_permission" "sync_config_eventbridge" {
  statement_id  = "AllowEventBridgeSyncConfig"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.image_ops.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sync_config.arn
}

resource "aws_cloudwatch_event_rule" "audit" {
  name                = "rising-audit-trigger"
  description         = "Run campaign health audit every Monday at 6am MT (1pm UTC)"
  schedule_expression = "cron(0 13 ? * MON *)"

  tags = {
    Project     = "rising-pmax"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "audit" {
  rule  = aws_cloudwatch_event_rule.audit.name
  arn   = aws_lambda_function.image_ops.arn
  input = jsonencode({ action = "audit" })
}

resource "aws_lambda_permission" "audit_eventbridge" {
  statement_id  = "AllowEventBridgeAudit"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.image_ops.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.audit.arn
}

# ---------- Outputs ----------

output "weekly_review_function_arn" {
  value = aws_lambda_function.weekly_review.arn
}

output "verify_upload_function_arn" {
  value = aws_lambda_function.verify_upload.arn
}

output "asset_performance_table" {
  value = aws_dynamodb_table.asset_performance.name
}

output "asset_graveyard_table" {
  value = aws_dynamodb_table.asset_graveyard.name
}

output "budget_performance_table" {
  value = aws_dynamodb_table.budget_performance.name
}

output "image_registry_table" {
  value = aws_dynamodb_table.image_registry.name
}

output "image_ops_function_arn" {
  value = aws_lambda_function.image_ops.arn
}

output "pmax_images_bucket" {
  value = aws_s3_bucket.pmax_images.id
}
