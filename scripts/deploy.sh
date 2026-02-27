#!/usr/bin/env bash
# deploy.sh — build Lambda package and deploy to AWS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export AWS_PROFILE="${AWS_PROFILE:-default}"

echo "╭──────────────────────────────────────╮"
echo "│  dd-log-analyzer — Deploy to AWS     │"
echo "╰──────────────────────────────────────╯"
echo ""

# 1. Build Lambda zip
echo "📦 Building Lambda package..."
bash "$SCRIPT_DIR/package.sh"
echo ""

# 2. Terraform apply
echo "🚀 Deploying infrastructure..."
cd "$PROJECT_DIR/infra"
terraform init -input=false -no-color > /dev/null 2>&1
terraform apply -auto-approve

echo ""
echo "✅ Deployed successfully!"
