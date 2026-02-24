# Deployment

## Architecture

Three Lambda functions share the same source code and dependency layer:

| Function | Name | Trigger | Timeout |
|----------|------|---------|---------|
| Weekly Review | `rising-weekly-review` | EventBridge, Mondays 6am MT | 300s |
| Verify Upload | `rising-verify-upload` | EventBridge, Thursdays 6am MT | 180s |
| Image Ops | `rising-image-ops` | EventBridge daily + manual invoke | 300s |

All three include the same directories: `lambda_functions/`, `src/`, `config/`, `database/`, `utils/`.

Dependencies are in a shared Lambda layer (`rising-pmax-dependencies`) deployed via S3.

## Code-Only Deploy (most changes)

When you change Python files in `src/`, `config/`, `database/`, `utils/`, or `lambda_functions/`, deploy the affected function directly:

```bash
# Package
cd /Users/scottnichols/development/rising-pmax-optimizer
zip -r /tmp/weekly_review.zip \
  lambda_functions/weekly_review.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ \
  -x "*__pycache__*" "*.pyc"

# Deploy
aws lambda update-function-code \
  --function-name rising-weekly-review \
  --zip-file fileb:///tmp/weekly_review.zip
```

Replace the function name and zip filename for other functions:

```bash
# verify_upload
zip -r /tmp/verify_upload.zip \
  lambda_functions/verify_upload.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ \
  -x "*__pycache__*" "*.pyc"
aws lambda update-function-code \
  --function-name rising-verify-upload \
  --zip-file fileb:///tmp/verify_upload.zip

# image_ops
zip -r /tmp/image_ops.zip \
  lambda_functions/image_ops.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ \
  -x "*__pycache__*" "*.pyc"
aws lambda update-function-code \
  --function-name rising-image-ops \
  --zip-file fileb:///tmp/image_ops.zip
```

Since all three functions share `src/`, `config/`, `database/`, and `utils/`, changes to those directories should be deployed to all three functions.

## Layer Deploy (dependency changes)

When `requirements.txt` changes (add/remove/update a package):

```bash
# 1. Build the layer
cd /Users/scottnichols/development/rising-pmax-optimizer
bash deployment/build_layer.sh

# 2. Upload to S3 (direct upload fails for layers >50MB)
aws s3 cp lambda_layer.zip s3://rising-pmax/deployments/lambda_layer.zip

# 3. Publish new layer version
aws lambda publish-layer-version \
  --layer-name rising-pmax-dependencies \
  --content S3Bucket=rising-pmax,S3Key=deployments/lambda_layer.zip \
  --compatible-runtimes python3.12

# 4. Update each function to use the new layer version
LAYER_ARN=$(aws lambda list-layer-versions \
  --layer-name rising-pmax-dependencies \
  --query 'LayerVersions[0].LayerVersionArn' --output text)

aws lambda update-function-configuration \
  --function-name rising-weekly-review --layers "$LAYER_ARN"
aws lambda update-function-configuration \
  --function-name rising-verify-upload --layers "$LAYER_ARN"
aws lambda update-function-configuration \
  --function-name rising-image-ops --layers "$LAYER_ARN"
```

## Infrastructure Deploy (Terraform)

When changing Lambda configuration (timeout, memory, env vars), IAM policies, DynamoDB tables, EventBridge schedules, or S3 settings:

```bash
# 1. Package all functions first (Terraform references the zips)
cd /Users/scottnichols/development/rising-pmax-optimizer
mkdir -p packages
zip -r packages/weekly_review.zip \
  lambda_functions/weekly_review.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ -x "*__pycache__*" "*.pyc"
zip -r packages/verify_upload.zip \
  lambda_functions/verify_upload.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ -x "*__pycache__*" "*.pyc"
zip -r packages/image_ops.zip \
  lambda_functions/image_ops.py lambda_functions/__init__.py \
  src/ config/ database/ utils/ -x "*__pycache__*" "*.pyc"

# 2. Plan and apply
cd deployment/terraform
terraform plan -out=tfplan
terraform apply tfplan
```

## Testing a Deploy

```bash
# Weekly review (preview mode - no side effects)
aws lambda invoke \
  --function-name rising-weekly-review \
  --payload "$(echo '{"preview_mode": true}' | base64)" \
  --cli-read-timeout 120 \
  /tmp/output.json
cat /tmp/output.json

# Image ops (specific action)
aws lambda invoke \
  --function-name rising-image-ops \
  --payload "$(echo '{"action": "gap_analysis"}' | base64)" \
  --cli-read-timeout 120 \
  /tmp/output.json
cat /tmp/output.json

# Verify upload
aws lambda invoke \
  --function-name rising-verify-upload \
  --payload "$(echo '{}' | base64)" \
  --cli-read-timeout 120 \
  /tmp/output.json
cat /tmp/output.json
```

## Quick Reference

| Change | What to do |
|--------|-----------|
| Python code in `src/`, `config/`, `database/`, `utils/` | Code deploy to all 3 functions |
| Only `lambda_functions/weekly_review.py` | Code deploy to `rising-weekly-review` only |
| Only `lambda_functions/image_ops.py` | Code deploy to `rising-image-ops` only |
| Only `lambda_functions/verify_upload.py` | Code deploy to `rising-verify-upload` only |
| `requirements.txt` | Layer deploy, then code deploy all 3 |
| Lambda timeout, memory, env vars | Terraform deploy |
| New DynamoDB table or index | Terraform deploy |
| New EventBridge schedule | Terraform deploy |
| New IAM permissions | Terraform deploy |
