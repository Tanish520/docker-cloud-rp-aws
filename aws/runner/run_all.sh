#!/usr/bin/env bash
# Runs Phases 1-3 on the EC2 runner: the same steps as aws/buildspecs/phase{1,2,3}.yml.
# Started by infra/run_on_ec2.sh through SSM Run Command, from the unpacked source folder.
set -euo pipefail

: "${RUN_ID:?RUN_ID must be set}"
: "${BUCKET:?BUCKET must be set}"
: "${DB_HOST:?DB_HOST must be set}"
: "${DB_SECRET_ARN:?DB_SECRET_ARN must be set}"
: "${AWS_REGION:?AWS_REGION must be set}"
export RUN_ID BUCKET DB_HOST AWS_REGION
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
export RESET_TM="${RESET_TM:-true}"
export PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"
export DB_NAME="${DB_NAME:-localization}"
export PIPELINE_CONFIG="${PIPELINE_CONFIG:-aws/config.json}"
INPUT_S3="${INPUT_S3:-}"

# boto3 and a plain `python` command come from the venv the instance bootstrap creates.
if [ -d /opt/venv/bin ]; then export PATH="/opt/venv/bin:$PATH"; fi

secret="$(aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN" --query SecretString --output text)"
DB_USER="$(printf '%s' "$secret" | python3 -c 'import json, sys; print(json.load(sys.stdin)["username"])')"
DB_PASSWORD="$(printf '%s' "$secret" | python3 -c 'import json, sys; print(json.load(sys.stdin)["password"])')"
export DB_USER DB_PASSWORD

MODEL_KEY="$(sed -n 's/^ *MODEL_KEY: *"\(.*\)" *$/\1/p' aws/buildspecs/phase2.yml)"
if [ -z "$MODEL_KEY" ]; then
  echo "❌ MODEL_KEY not found in aws/buildspecs/phase2.yml"
  exit 1
fi
export MODEL_KEY

case "${RUNNER_TASK:-pipeline}" in
  pipeline) ;;
  batching-bench)
    echo "=== Batching benchmark (run $RUN_ID) ==="
    PHASE2_TASK=bench bash aws/scripts/run_phase2.sh
    exit 0 ;;
  *) echo "❌ Unknown RUNNER_TASK: ${RUNNER_TASK}"; exit 1 ;;
esac

if [ -n "$INPUT_S3" ]; then
  mkdir -p source-folder
  aws s3 cp "$INPUT_S3" "source-folder/$(basename "$INPUT_S3")" --only-show-errors
fi
echo "=== Phase 1: extract (run $RUN_ID) ==="
python3 aws/phase1.py "$PIPELINE_CONFIG"
echo "=== Phase 2: translate ==="
bash aws/scripts/run_phase2.sh
echo "=== Phase 3: merge ==="
python3 aws/phase3.py "$PIPELINE_CONFIG"
echo "✅ Run $RUN_ID finished: s3://$BUCKET/runs/$RUN_ID/output/"
