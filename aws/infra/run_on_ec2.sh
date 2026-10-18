#!/usr/bin/env bash
# Run the whole pipeline (Phases 1-3) on a temporary EC2 machine instead of CodeBuild.
# The last commit is uploaded to S3, the machine runs aws/runner/run_all.sh through SSM,
# its output goes to CloudWatch Logs, and the machine is terminated afterwards.
# Settings (environment variables): AWS_REGION, STACK_NAME, RESET_TM (default false: keep the translation memory),
# PHASE2_CPUSET (default 0,1), RUNNER_POLL_SECONDS (default 20),
# RUNNER_REUSE (default true: start the stopped "ready" runner, whose disk keeps the built image and the
#   model, and stop it afterwards; false: a fresh machine that is terminated afterwards),
# RUNNER_TASK (pipeline = Phases 1-3, default; batching-bench = the batching benchmark),
# RUNNER_TIMEOUT_SECONDS (default 7200), BENCH_ARGS (extra benchmark options, e.g. --kde4 s3://...),
# PIPELINE_CONFIG (default aws/config.json), INPUT_S3 (space-separated .resx files copied into source-folder/ first),
# TM_STEPS and TM_INPUT_PREFIX (RUNNER_TASK=tm-experiment, see runner/run_all.sh).
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STACK_NAME="${STACK_NAME:-cloud-loc}"
RESET_TM="${RESET_TM:-false}"
PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"
POLL="${RUNNER_POLL_SECONDS:-20}"
RUNNER_TASK="${RUNNER_TASK:-pipeline}"
RUNNER_REUSE="${RUNNER_REUSE:-true}"
TIMEOUT="${RUNNER_TIMEOUT_SECONDS:-7200}"
BENCH_ARGS="${BENCH_ARGS:-}"
PIPELINE_CONFIG="${PIPELINE_CONFIG:-aws/config.json}"
INPUT_S3="${INPUT_S3:-}"
TM_STEPS="${TM_STEPS:-}"
TM_INPUT_PREFIX="${TM_INPUT_PREFIX:-}"
# The launch template's 4-hour safety shutdown is moved to the timeout + 30 minutes.
SHUTDOWN_MINUTES=$(( TIMEOUT / 60 + 30 ))

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

INSTANCE_ID=""
if [ "$RUNNER_REUSE" = "true" ]; then
  # A runner still shutting down from the previous run counts: wait for it rather than creating another.
  INSTANCE_ID="$(aws ec2 describe-instances --region "$REGION" --filters "Name=tag:cloud-loc-runner,Values=ready" \
    "Name=instance-state-name,Values=stopping,stopped" --query 'Reservations[].Instances[].InstanceId' --output text | awk '{print $1}')"
fi
if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  aws ec2 wait instance-stopped --region "$REGION" --instance-ids "$INSTANCE_ID"
  echo "🖥️ Starting the ready runner $INSTANCE_ID (built image and model already on its disk)..."
  aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
elif [ "$RUNNER_REUSE" = "true" ]; then
  echo "🖥️ No stopped ready runner; creating one (it is stopped, not terminated, after the run)..."
  INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" --launch-template "LaunchTemplateId=$TEMPLATE_ID" \
    --instance-initiated-shutdown-behavior stop \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$STACK_NAME-runner-ready},{Key=Project,Value=$STACK_NAME},{Key=cloud-loc-runner,Value=ready}]" \
    --query 'Instances[0].InstanceId' --output text)"
else
  echo "🖥️ Starting a fresh runner machine..."
  INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" --launch-template "LaunchTemplateId=$TEMPLATE_ID" \
    --query 'Instances[0].InstanceId' --output text)"
fi
cleanup() {
  if [ "$RUNNER_REUSE" = "true" ]; then
    echo "🧹 Stopping $INSTANCE_ID (kept for the next run) ..."
    aws ec2 stop-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null || true
  else
    echo "🧹 Terminating $INSTANCE_ID ..."
    aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null || true
  fi
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
shutdown -h +$SHUTDOWN_MINUTES
test -x /opt/venv/bin/python || { echo 'Runner bootstrap failed (no /opt/venv)'; exit 1; }
# A reused runner keeps the model between runs; everything else is unpacked fresh.
mkdir -p /opt/model-cache
if [ -d /opt/run/aws/translator/nllb_ct2 ]; then rm -rf /opt/model-cache/nllb_ct2; mv /opt/run/aws/translator/nllb_ct2 /opt/model-cache/; fi
rm -rf /opt/run && mkdir -p /opt/run && cd /opt/run
aws s3 cp s3://$BUCKET/runs/$RUN_ID/source.tar.gz - --region $REGION | tar -xz
if [ -d /opt/model-cache/nllb_ct2 ]; then mv /opt/model-cache/nllb_ct2 aws/translator/nllb_ct2; fi
RUN_ID=$RUN_ID BUCKET=$BUCKET DB_HOST=$DB_HOST DB_SECRET_ARN='$DB_SECRET_ARN' AWS_REGION=$REGION RESET_TM=$RESET_TM PHASE2_CPUSET=$PHASE2_CPUSET RUNNER_TASK=$RUNNER_TASK BENCH_ARGS='$BENCH_ARGS' PIPELINE_CONFIG='$PIPELINE_CONFIG' INPUT_S3='$INPUT_S3' TM_STEPS='$TM_STEPS' TM_INPUT_PREFIX='$TM_INPUT_PREFIX' bash aws/runner/run_all.sh"
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
if [ "$RUNNER_TASK" = "tm-experiment" ]; then
  echo "Step timings: s3://$BUCKET/runs/$RUN_ID/experiment/steps.tsv (each step is run $RUN_ID-<step>)"
  exit 0
fi
if [ "$RUNNER_TASK" = "batching-bench" ]; then
  echo "Benchmark results: s3://$BUCKET/runs/$RUN_ID/bench/results.json"
  exit 0
fi
echo "Results: s3://$BUCKET/runs/$RUN_ID/output/"
echo "Telemetry: aws/.venv/bin/python aws/tools/export_telemetry.py --run-id $RUN_ID --log-group $LOG_GROUP --out $RUN_ID.csv"
