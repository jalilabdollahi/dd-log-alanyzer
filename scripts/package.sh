#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# package.sh — Build Lambda deployment zip
#
# Usage: ./scripts/package.sh
# Output: dist/lambda.zip
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$PROJECT_ROOT/dist"
BUILD_DIR="$PROJECT_ROOT/dist/build"

echo "📦 Building Lambda deployment package..."

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

# Install dependencies into build dir
echo "  Installing dependencies..."
python3 -m pip install \
  --target "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  --upgrade \
  datadog-api-client \
  pydantic \
  pydantic-settings \
  pyyaml \
  python-dotenv \
  numpy \
  httpx \
  boto3 \
  2>&1 | tail -5

# Copy source code
echo "  Copying source code..."
cp -r "$PROJECT_ROOT/src/dd_log_analyzer" "$BUILD_DIR/"

# Remove unnecessary files to keep zip small
echo "  Cleaning up..."
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true

# Remove CLI-only dependencies (not needed in Lambda)
rm -rf "$BUILD_DIR/click" "$BUILD_DIR/rich" 2>/dev/null || true

# Create zip
echo "  Creating zip..."
cd "$BUILD_DIR"
zip -q -r "$DIST_DIR/lambda.zip" . -x "*.pyc" "__pycache__/*"

# Report size
SIZE=$(du -sh "$DIST_DIR/lambda.zip" | cut -f1)
echo ""
echo "✅ Lambda package built: dist/lambda.zip ($SIZE)"
echo "   Deploy with: cd infra && terraform apply"
