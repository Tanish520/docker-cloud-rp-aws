#!/usr/bin/env bash
# Phase 2 on CodeBuild: build the image, restore or convert the model, prepare the
# translation memory, then translate inside Docker pinned to $PHASE2_CPUSET.
set -euo pipefail

: "${BUCKET:?BUCKET must be set}"
: "${RUN_ID:?RUN_ID must be set}"
: "${DB_HOST:?DB_HOST must be set}"
: "${DB_USER:?DB_USER must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set}"
: "${MODEL_KEY:?MODEL_KEY must be set}"
PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"
RESET_TM="${RESET_TM:-true}"
export DB_NAME="${DB_NAME:-localization}"

IMAGE="local-ai-translator"
BASE_IMAGE="public.ecr.aws/docker/library/python:3.10-slim-bookworm"
TRANSLATOR_DIR="$(pwd)/aws/translator"
MODEL_DIR="$TRANSLATOR_DIR/nllb_ct2"

# -e NAME passes the host value through. The container reaches S3 with the build's IAM role
# via the CodeBuild credentials endpoint (AWS_CONTAINER_CREDENTIALS_RELATIVE_URI).
DOCKER_ENV=(-e RUN_ID -e BUCKET -e DB_HOST -e DB_USER -e DB_PASSWORD -e DB_NAME
            -e AWS_REGION -e AWS_DEFAULT_REGION -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI)

run_in_image() {
  docker run --rm "${DOCKER_ENV[@]}" -v "$TRANSLATOR_DIR:/app" "$IMAGE" "$@"
}

model_loads() {
  run_in_image python -c "import ctranslate2; ctranslate2.Translator('nllb_ct2', device='cpu', compute_type='int8')"
}

convert_model() {
  echo "⚙️ Converting facebook/nllb-200-distilled-600M to CTranslate2 INT8..."
  rm -rf "$MODEL_DIR"
  docker run --rm -v "$TRANSLATOR_DIR:/work" "$BASE_IMAGE" bash -c '
    set -euo pipefail
    pip install --quiet --no-cache-dir "torch==2.2.2" --index-url https://download.pytorch.org/whl/cpu
    pip install --quiet --no-cache-dir "ctranslate2==4.7.1" "transformers==4.38.2" "sentencepiece==0.2.0" "protobuf==4.25.3" "numpy<2.0.0"
    ct2-transformers-converter --model facebook/nllb-200-distilled-600M --output_dir /work/nllb_ct2 --quantization int8
  '
}

echo "🐳 Building translator image..."
cp aws/config.json "$TRANSLATOR_DIR/config.json"
docker build -t "$IMAGE" "$TRANSLATOR_DIR"

echo "📦 Looking for cached model $MODEL_KEY..."
if python aws/model_cache.py fetch --dir "$MODEL_DIR" && model_loads; then
  echo "✅ Using cached model."
else
  python aws/model_cache.py invalidate
  convert_model
  model_loads
  python aws/model_cache.py publish --dir "$MODEL_DIR"
fi

echo "🗄️ Preparing translation memory database..."
run_in_image python db_admin.py init
if [ "$(echo "$RESET_TM" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  run_in_image python db_admin.py reset
fi

echo "🚀 Translating run $RUN_ID on cores $PHASE2_CPUSET..."
docker run --rm --cpuset-cpus="$PHASE2_CPUSET" "${DOCKER_ENV[@]}" -v "$TRANSLATOR_DIR:/app" "$IMAGE" \
  python translation.py config.json 2>&1 | tee phase2.log
echo "✅ Phase 2 completed."
