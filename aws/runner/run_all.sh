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
export RESET_TM="${RESET_TM:-false}"
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
  tm-experiment)
    # Translation-memory experiment: several app versions on one machine, memory kept between steps.
    # TM_STEPS: space-separated "name[=file][:reset][:plain]", e.g. "v1:reset v2-r1 v4-cold=v4-r1:reset:plain"
    # (reset empties the memory first; plain also switches near-twin reuse off, i.e. the pipeline before it);
    # step "name" translates $TM_INPUT_PREFIX/kde4tm-<file>.resx as run $RUN_ID-<name>.
    : "${TM_STEPS:?TM_STEPS must be set}"
    : "${TM_INPUT_PREFIX:?TM_INPUT_PREFIX must be set}"
    export PIPELINE_CONFIG=aws/config.kde4tm.json
    base_run="$RUN_ID"
    timings="tm-steps.tsv"
    now() { python3 -c 'import time; print(time.time())'; }
    secs() { awk "BEGIN { printf \"%.1f\", $2 - $1 }"; }
    printf 'step\trun_id\tfile\treset\tphase1_s\tphase2_s\tphase3_s\ttotal_s\n' > "$timings"
    mkdir -p source-folder
    for step in $TM_STEPS; do
      reset=false
      twins=on
      spec="$step"
      case "$spec" in *:plain) twins=off; spec="${spec%:plain}" ;; esac
      case "$spec" in *:reset) reset=true; spec="${spec%:reset}" ;; esac
      name="${spec%%=*}"
      file="${spec#*=}"
      export RUN_ID="$base_run-$name" RESET_TM="$reset" TM_TWINS="$twins"
      echo "=== Memory experiment step $name (file $file, reset $reset, twins $twins, run $RUN_ID) ==="
      aws s3 cp "$TM_INPUT_PREFIX/kde4tm-$file.resx" source-folder/kde4tm.resx --only-show-errors
      t0="$(now)"; python3 aws/phase1.py "$PIPELINE_CONFIG"
      t1="$(now)"; bash aws/scripts/run_phase2.sh
      t2="$(now)"; python3 aws/phase3.py "$PIPELINE_CONFIG"
      t3="$(now)"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$RUN_ID" "$file" "$reset" \
        "$(secs "$t0" "$t1")" "$(secs "$t1" "$t2")" "$(secs "$t2" "$t3")" "$(secs "$t0" "$t3")" >> "$timings"
    done
    aws s3 cp "$timings" "s3://$BUCKET/runs/$base_run/experiment/steps.tsv" --only-show-errors
    echo "✅ Memory experiment finished: s3://$BUCKET/runs/$base_run/experiment/steps.tsv"
    exit 0 ;;
  *) echo "❌ Unknown RUNNER_TASK: ${RUNNER_TASK}"; exit 1 ;;
esac

# INPUT_S3: one or more space-separated s3:// .resx files to add to source-folder/ before Phase 1.
if [ -n "$INPUT_S3" ]; then
  mkdir -p source-folder
  for uri in $INPUT_S3; do
    aws s3 cp "$uri" "source-folder/$(basename "$uri")" --only-show-errors
  done
fi
echo "=== Phase 1: extract (run $RUN_ID) ==="
python3 aws/phase1.py "$PIPELINE_CONFIG"
echo "=== Phase 2: translate ==="
bash aws/scripts/run_phase2.sh
echo "=== Phase 3: merge ==="
python3 aws/phase3.py "$PIPELINE_CONFIG"
echo "✅ Run $RUN_ID finished: s3://$BUCKET/runs/$RUN_ID/output/"
