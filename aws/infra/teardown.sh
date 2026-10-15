#!/usr/bin/env bash
# Delete everything the AWS pipeline stack created. Buckets are emptied first.
# Pass --yes to skip the confirmation prompt.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STACK_NAME="${STACK_NAME:-cloud-loc}"

if ! aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" >/dev/null 2>&1; then
  echo "ℹ️ Stack $STACK_NAME not found in $REGION; nothing to delete."
  exit 0
fi

if [ "${1:-}" != "--yes" ]; then
  read -r -p "Delete stack $STACK_NAME in $REGION, its database and all run files? Type 'delete' to confirm: " answer
  [ "$answer" = "delete" ] || { echo "Cancelled."; exit 1; }
fi

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

for key in DataBucketName ArtifactBucketName; do
  bucket="$(output "$key")"
  if [ -n "$bucket" ] && [ "$bucket" != "None" ]; then
    echo "🧹 Emptying s3://$bucket ..."
    aws s3 rm "s3://$bucket" --recursive --region "$REGION" --only-show-errors
  fi
done

echo "🗑️ Deleting stack $STACK_NAME (the database takes about 10 minutes)..."
aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME"
echo "✅ Stack deleted."
