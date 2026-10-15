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

for output_key in DataBucketName ArtifactBucketName; do
  bucket="$(output "$output_key")"
  if [ -z "$bucket" ] || [ "$bucket" = "None" ]; then continue; fi
  # Safe to re-run after a partial teardown: a bucket that is already gone is skipped.
  if ! aws s3api head-bucket --bucket "$bucket" --region "$REGION" >/dev/null 2>&1; then
    echo "ℹ️ s3://$bucket is already gone."
    continue
  fi
  echo "🧹 Aborting unfinished uploads in s3://$bucket ..."
  aws s3api list-multipart-uploads --bucket "$bucket" --region "$REGION" \
    --query 'Uploads[].[Key,UploadId]' --output text |
  while IFS=$'\t' read -r object_key upload_id; do
    if [ -z "$object_key" ] || [ "$object_key" = "None" ]; then continue; fi
    aws s3api abort-multipart-upload --bucket "$bucket" --region "$REGION" --key "$object_key" --upload-id "$upload_id"
  done
  echo "🧹 Emptying s3://$bucket ..."
  aws s3 rm "s3://$bucket" --recursive --region "$REGION" --only-show-errors
done

echo "🗑️ Deleting stack $STACK_NAME (the database takes about 10 minutes)..."
aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME"
echo "✅ Stack deleted."
