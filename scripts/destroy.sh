#!/usr/bin/env bash
# destroy.sh — tear down AWS infrastructure (keeps S3 bucket by default)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export AWS_PROFILE="${AWS_PROFILE:-default}"

echo "╭──────────────────────────────────────╮"
echo "│  dd-log-analyzer — Destroy AWS       │"
echo "╰──────────────────────────────────────╯"
echo ""

cd "$PROJECT_DIR/infra"

# Check for --all flag to include S3 bucket
if [[ "${1:-}" == "--all" ]]; then
    echo "⚠️  Destroying EVERYTHING including S3 bucket..."
    echo ""

    # Temporarily disable prevent_destroy
    sed -i.bak 's/prevent_destroy = true/prevent_destroy = false/' s3.tf

    # Empty the bucket first (delete all objects + versions)
    BUCKET=$(terraform output -raw s3_reports_bucket 2>/dev/null || echo "")
    if [[ -n "$BUCKET" ]]; then
        echo "🗑  Emptying bucket: $BUCKET"
        aws s3 rm "s3://$BUCKET" --recursive 2>/dev/null || true

        # Delete versioned objects if any
        VERSIONS=$(aws s3api list-object-versions --bucket "$BUCKET" \
            --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
            --output json 2>/dev/null || echo '{"Objects":null}')
        if [[ "$VERSIONS" != '{"Objects":null}' && "$VERSIONS" != *"null"* ]]; then
            aws s3api delete-objects --bucket "$BUCKET" --delete "$VERSIONS" > /dev/null 2>&1 || true
        fi

        MARKERS=$(aws s3api list-object-versions --bucket "$BUCKET" \
            --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
            --output json 2>/dev/null || echo '{"Objects":null}')
        if [[ "$MARKERS" != '{"Objects":null}' && "$MARKERS" != *"null"* ]]; then
            aws s3api delete-objects --bucket "$BUCKET" --delete "$MARKERS" > /dev/null 2>&1 || true
        fi
    fi

    terraform destroy -auto-approve

    # Restore prevent_destroy
    mv s3.tf.bak s3.tf
    echo ""
    echo "✅ Everything destroyed (including S3 bucket)."
else
    echo "🛡  Keeping S3 bucket (use --all to include it)"
    echo ""

    terraform destroy -auto-approve \
        -target=aws_lambda_function.analyzer \
        -target=aws_cloudwatch_event_rule.schedule \
        -target=aws_cloudwatch_event_target.lambda \
        -target=aws_lambda_permission.eventbridge \
        -target=aws_iam_role.lambda_role \
        -target=aws_iam_role_policy.lambda_policy \
        -target=aws_iam_role_policy_attachment.lambda_logs \
        -target=aws_cloudwatch_log_group.lambda \
        -target=aws_dynamodb_table.alert_state \
        -target=aws_s3_bucket_lifecycle_configuration.reports \
        -target=aws_s3_bucket_public_access_block.reports \
        -target=aws_s3_bucket_server_side_encryption_configuration.reports

    echo ""
    echo "✅ Infrastructure destroyed. S3 bucket preserved."
fi
