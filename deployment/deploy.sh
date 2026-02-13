#!/bin/bash
# Deploy Rising PMax Optimizer to AWS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Rising PMax Optimizer Deployment ==="
echo ""

# Step 1: Build Lambda layer
echo "Step 1: Building Lambda layer..."
bash "$SCRIPT_DIR/build_layer.sh"
echo ""

# Step 2: Package Lambda functions
echo "Step 2: Packaging Lambda functions..."
mkdir -p "$PROJECT_ROOT/packages"

# Package weekly_review
cd "$PROJECT_ROOT"
zip -r packages/weekly_review.zip \
  lambda_functions/weekly_review.py \
  lambda_functions/__init__.py \
  src/ \
  config/ \
  database/ \
  utils/ \
  -x "*__pycache__*" "*.pyc"

# Package verify_upload
zip -r packages/verify_upload.zip \
  lambda_functions/verify_upload.py \
  lambda_functions/__init__.py \
  src/ \
  config/ \
  database/ \
  utils/ \
  -x "*__pycache__*" "*.pyc"

# Package image_ops
zip -r packages/image_ops.zip \
  lambda_functions/image_ops.py \
  lambda_functions/__init__.py \
  src/ \
  config/ \
  database/ \
  utils/ \
  -x "*__pycache__*" "*.pyc"

echo "Packages built:"
ls -lh packages/
echo ""

# Step 3: Terraform
echo "Step 3: Running Terraform..."
cd "$SCRIPT_DIR/terraform"

terraform init
terraform plan -out=tfplan
echo ""
echo "Review the plan above. To apply:"
echo "  cd $SCRIPT_DIR/terraform && terraform apply tfplan"
echo ""

echo "=== Deployment preparation complete ==="
echo ""
echo "Next steps:"
echo "1. Review and apply Terraform plan"
echo "2. Add secrets to Parameter Store (see docs/API_SETUP.md)"
echo "3. Test with: aws lambda invoke --function-name rising-weekly-review --payload '{}' response.json"
echo "4. Check Slack for test message"
echo "5. Monitor first automated run on Monday at 6am MT"
