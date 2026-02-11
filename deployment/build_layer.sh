#!/bin/bash
# Build Lambda layer with Python dependencies
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Building Lambda layer..."

# Clean previous build
rm -rf "$PROJECT_ROOT/build/layer"
mkdir -p "$PROJECT_ROOT/build/layer/python/lib/python3.12/site-packages"

# Install dependencies
pip install \
  -r "$PROJECT_ROOT/requirements.txt" \
  -t "$PROJECT_ROOT/build/layer/python/lib/python3.12/site-packages" \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --python-version 3.12 \
  2>/dev/null || \
pip install \
  -r "$PROJECT_ROOT/requirements.txt" \
  -t "$PROJECT_ROOT/build/layer/python/lib/python3.12/site-packages"

# Remove test/cache files to reduce size
find "$PROJECT_ROOT/build/layer" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$PROJECT_ROOT/build/layer" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "$PROJECT_ROOT/build/layer" -name "*.pyc" -delete 2>/dev/null || true

# Create zip
cd "$PROJECT_ROOT/build/layer"
zip -r "$PROJECT_ROOT/lambda_layer.zip" python

echo "Layer built: $PROJECT_ROOT/lambda_layer.zip"
echo "Size: $(du -h "$PROJECT_ROOT/lambda_layer.zip" | cut -f1)"
