#!/usr/bin/env bash
# Create or update the AWS pipeline stack, then open the database firewall to CodeBuild.
# Settings (environment variables): AWS_REGION, STACK_NAME, PROJECT_NAME, GITHUB_REPOSITORY,
# GITHUB_BRANCH, DB_INSTANCE_CLASS, PHASE2_CPUSET, ADMIN_CIDR, PYTHON.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STACK_NAME="${STACK_NAME:-cloud-loc}"
PROJECT_NAME="${PROJECT_NAME:-cloud-loc}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-AayushGupta56/docker-cloud-rp}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
DB_INSTANCE_CLASS="${DB_INSTANCE_CLASS:-db.t3.small}"
PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"
ADMIN_CIDR="${ADMIN_CIDR:-}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"

AWS_CLI_VERSION="$(aws --version 2>&1 || true)"
case "$AWS_CLI_VERSION" in
  aws-cli/2*) ;;
  *) echo "❌ AWS CLI v2 is required."; exit 1 ;;
esac
aws sts get-caller-identity --region "$REGION" >/dev/null || { echo "❌ No working AWS credentials (run: aws configure)."; exit 1; }
"$PYTHON" -c "import boto3" 2>/dev/null || { echo "❌ boto3 missing; run: python3 -m venv aws/.venv && aws/.venv/bin/pip install -r aws/requirements-dev.txt, then PYTHON=aws/.venv/bin/python"; exit 1; }
DEFAULT_VPC="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [ "$DEFAULT_VPC" = "None" ] || [ -z "$DEFAULT_VPC" ]; then
  echo "❌ $REGION has no default VPC. Create one with: aws ec2 create-default-vpc --region $REGION"
  exit 1
fi

echo "🚀 Deploying stack $STACK_NAME to $REGION (first deploy takes ~15 minutes for the database)..."
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$HERE/pipeline.yaml" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    GitHubRepository="$GITHUB_REPOSITORY" \
    GitHubBranch="$GITHUB_BRANCH" \
    DBInstanceClass="$DB_INSTANCE_CLASS" \
    Phase2CpuSet="$PHASE2_CPUSET"

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

EXTRA=()
if [ -n "$ADMIN_CIDR" ]; then EXTRA=(--extra-cidr "$ADMIN_CIDR"); fi
"$PYTHON" "$HERE/sync_prefix_list.py" --region "$REGION" --prefix-list-id "$(output DbPrefixListId)" ${EXTRA[@]+"${EXTRA[@]}"}

PIPELINE="$(output PipelineName)"
cat <<EOF

✅ Stack ready.

Next steps:
  1. Approve the GitHub connection (one time): AWS console → Developer Tools → Settings → Connections →
     "${PROJECT_NAME}-github" → Update pending connection. Its ARN: $(output GitHubConnectionArn)
     (The automatic first pipeline run fails at Source while the connection is pending; that is expected.)
  2. Start a run:
     aws codepipeline start-pipeline-execution --region $REGION --name $PIPELINE
     Add --variables name=RESET_TM,value=true to empty the translation memory first (0% cache start).
EOF
