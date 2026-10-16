#!/usr/bin/env bash
# Run the whole pipeline (Phases 1-3) on a temporary EC2 machine instead of CodeBuild.
# The last commit is uploaded to S3, the machine runs aws/runner/run_all.sh through SSM,
# its output goes to CloudWatch Logs, and the machine is terminated afterwards.
# Settings (environment variables): AWS_REGION, STACK_NAME, RESET_TM (default true),
# PHASE2_CPUSET (default 0,1), RUNNER_POLL_SECONDS (default 20),
# RUNNER_TASK (pipeline = Phases 1-3, default; batching-bench = the batching benchmark),
# RUNNER_TIMEOUT_SECONDS (default 7200), BENCH_ARGS (extra benchmark options, e.g. --kde4 s3://...).
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STACK_NAME="${STACK_NAME:-cloud-loc}"
RESET_TM="${RESET_TM:-true}"
PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"
POLL="${RUNNER_POLL_SECONDS:-20}"
RUNNER_TASK="${RUNNER_TASK:-pipeline}"
TIMEOUT="${RUNNER_TIMEOUT_SECONDS:-7200}"
BENCH_ARGS="${BENCH_ARGS:-}"

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

BUCKET="$(output DataBucketName)"
TEMPLATE_ID="$(output RunnerLaunchTemplateId)"
LOG_GROUP="$(output RunnerLogGroupName)"
DB_HOST="$(output DbEndpoint)"
DB_SECRET_ARN="$(output DbSecretArn)"
for value in "$BUCKET" "$TEMPLATE_ID" "$LOG_GROUP" "$DB_HOST" "$DB_SECRET_ARN"; do
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    echo "❌ Stack $STACK_NAME in $REGION is missing runner outputs; run aws/infra/deploy.sh first."
    exit 1
  fi
done

if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️ Uncommitted changes are not included: the run uses the last commit ($(git rev-parse --short HEAD))."
fi

RUN_ID="ec2-$(date -u +%Y%m%dt%H%M%Sz)"
echo "📦 Uploading the last commit for run $RUN_ID ..."
git archive --format=tar.gz HEAD | aws s3 cp - "s3://$BUCKET/runs/$RUN_ID/source.tar.gz" --region "$REGION" --only-show-errors

echo "🖥️ Starting the runner machine..."
INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" --launch-template "LaunchTemplateId=$TEMPLATE_ID" \
  --query 'Instances[0].InstanceId' --output text)"
cleanup() {
  echo "🧹 Terminating $INSTANCE_ID ..."
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null || true
}
trap cleanup EXIT

aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
echo "⏳ Waiting for $INSTANCE_ID to register with SSM..."
ping="None"
for _ in $(seq 1 60); do
  ping="$(aws ssm describe-instance-information --region "$REGION" --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text)"
  if [ "$ping" = "Online" ]; then break; fi
  sleep "$POLL"
done
if [ "$ping" != "Online" ]; then
  echo "❌ $INSTANCE_ID never registered with SSM."
  exit 1
fi

# Runs on the machine: wait for the bootstrap (Docker + venv), unpack the source, run all phases.
COMMAND="set -euo pipefail
cloud-init status --wait >/dev/null || true
test -x /opt/venv/bin/python || { echo 'Runner bootstrap failed (no /opt/venv)'; exit 1; }
mkdir -p /opt/run && cd /opt/run
aws s3 cp s3://$BUCKET/runs/$RUN_ID/source.tar.gz - --region $REGION | tar -xz
RUN_ID=$RUN_ID BUCKET=$BUCKET DB_HOST=$DB_HOST DB_SECRET_ARN='$DB_SECRET_ARN' AWS_REGION=$REGION RESET_TM=$RESET_TM PHASE2_CPUSET=$PHASE2_CPUSET RUNNER_TASK=$RUNNER_TASK BENCH_ARGS='$BENCH_ARGS' bash aws/runner/run_all.sh"
PARAMS="$(python3 -c 'import json, sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": [sys.argv[2]]}))' "$COMMAND" "$TIMEOUT")"

CMD_ID="$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --comment "localization run $RUN_ID" --parameters "$PARAMS" \
  --cloud-watch-output-config "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=$LOG_GROUP" \
  --query Command.CommandId --output text)"
echo "🚀 Running $RUNNER_TASK for run $RUN_ID on $INSTANCE_ID. Live output: CloudWatch log group $LOG_GROUP"

status="Pending"
while :; do
  status="$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in
    Pending|InProgress|Delayed) sleep "$POLL" ;;
    *) break ;;
  esac
done

echo "Run $RUN_ID finished with status: $status"
if [ "$status" != "Success" ]; then
  echo "--- last error output ---"
  aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
    --query StandardErrorContent --output text || true
  exit 1
fi
if [ "$RUNNER_TASK" = "batching-bench" ]; then
  echo "Benchmark results: s3://$BUCKET/runs/$RUN_ID/bench/results.json"
  exit 0
fi
echo "Results: s3://$BUCKET/runs/$RUN_ID/output/"
echo "Telemetry: aws/.venv/bin/python aws/tools/export_telemetry.py --run-id $RUN_ID --log-group $LOG_GROUP --out $RUN_ID.csv"
