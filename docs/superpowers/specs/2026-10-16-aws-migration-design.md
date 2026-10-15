# AWS Migration of the Localization Pipeline — Design

**Date:** 2026-10-16
**Status:** Draft for review

## 1. Purpose

Build an AWS version of the full three-phase localization pipeline (Phase 1 extract, Phase 2 Docker + NLLB/CTranslate2 translate, Phase 3 merge) and keep the existing Azure pipeline in the repo, working again.

The AWS pipeline exists first to produce a **fair Azure vs AWS comparison** of Docker local inference, and then to serve as the base for future experiments. "Fair" means: the same model, inference settings, workload, database engine, translation logic and telemetry fields, so that the main thing that changes between the two runs is the cloud.

### Success criteria

1. A manual AWS pipeline run with `RESET_TM=true` finishes green and produces:
   - 7 `RESEARCH_DATA` lines (one per language), each with `total_segments=50`, `nmt_calls=50`, `transational_memory_hits=0`, `cache_rate=0` and `cpu_limit=2`;
   - 7 localized `.resx` files in S3 (`runs/<run-id>/output/<lang>/resx_file_10.resx`) and in the Phase 3 build artifact;
   - a CSV export of those lines whose field names match the Azure `RESEARCH_DATA` payload.
2. A second run with `RESET_TM=false` reports `cache_rate=100` and `nmt_calls=0` for every language.
3. The parity tests pass, proving the AWS translation logic matches the Azure logic.
4. `teardown.sh` leaves no billable resources behind.
5. The Azure pipeline lives under `azure/`, contains no secrets, and is runnable again once its YAML path is updated in Azure DevOps.

## 2. Decisions

| Topic | Decision |
|---|---|
| Purpose | Fair Azure vs AWS comparison first; future-work platform second |
| Azure preservation | Move to `azure/`; restore Phase 1 and Phase 3 from git history; path fixes only; no Python changes |
| AWS shape | CodePipeline + three CodeBuild stages; Phase 2 runs `docker build` + `docker run` inside CodeBuild, as on Azure |
| Infrastructure as code | One CloudFormation stack |
| Region | `ap-south-1` (Mumbai) |
| Database | Amazon RDS for SQL Server Express (same engine family as Azure SQL) |
| Outputs | S3 + CodeBuild artifact; no commits to git |
| CPU parity | Phase 2 container pinned to 2 cores (`--cpuset-cpus=0,1`), matching the 2-vCPU / 7 GB private-project Azure agent |
| Workload | `resx_file_10.resx` × 7 languages (fr-FR, es-ES, hi-IN, de-DE, ja-JP, ru-RU, zh-CN), the same workload behind the report's Docker telemetry |

## 3. Scope

**In scope:** repo reorganization; restoring the Azure pipeline; the AWS pipeline (code, buildspecs, CloudFormation, deploy/teardown scripts); telemetry export; tests; READMEs.

**Out of scope:**
- The quantization benchmark (its Azure stage is commented out, and it has a known target-language bug).
- The VM-local generation, GPU runs, Fargate, and cache-sweep / FLORES experiments.
- Fixing the Azure-side bugs (they stay as-is so the report's Azure numbers remain valid).
- Rewriting git history to purge leaked secrets.
- Deleting stray root files (`nllb_ct2/`, `source-folder-resx_files/`, `resx_file_03.es-ES.xlf`).

## 4. Repository layout after the change

```
/
├── README.md                      overview + links to azure/ and aws/
├── source-folder/                 shared English inputs (unchanged)
├── ENDSEM_REPORT_README.md, idea.md, implementation-checklist.md
├── docs/superpowers/specs/        this spec
├── azure/
│   ├── README.md
│   ├── azure-pipelines.yml        moved from root; paths updated
│   ├── API_based_HFace_AppInsight/
│   │   ├── phase1.py              restored from 8434ded^ (unchanged)
│   │   ├── phase3.py              restored from 8434ded^ (unchanged)
│   │   ├── config.json            restored from 8434ded^, secrets blanked
│   │   └── source-folder/resx_file_10.resx   restored from 8434ded^
│   ├── Docker-Local-Inference/    moved as-is; secrets blanked in config.json
│   └── legacy-powershell/
│       ├── SLALogger.ps1, Walkthrough.md
│       ├── config.json            old root config.json, secrets blanked
│       ├── azure-pipelines.yml    from source-folder/azure-pipelines.yml
│       └── target-folder/         old PowerShell output
└── aws/
    ├── README.md                  runbook
    ├── config.json                non-secret settings
    ├── phase1.py
    ├── phase3.py
    ├── translator/                Phase 2 Docker build context
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── translation.py
    │   ├── s3_store.py
    │   └── db_admin.py
    ├── buildspecs/phase1.yml, phase2.yml, phase3.yml
    ├── infra/pipeline.yaml, deploy.sh, teardown.sh
    ├── tools/export_telemetry.py
    └── tests/
```

All moves use `git mv` so file history follows.

## 5. Azure preservation

### 5.1 Restored files
From commit `8434ded^`, folder `API_based_HFace_AppInsight/`: `phase1.py`, `phase3.py`, `config.json`, `source-folder/resx_file_10.resx`. The Python files are restored byte-for-byte.

### 5.2 Edits to `azure/azure-pipelines.yml` (the only edits)
| Step | Change |
|---|---|
| Phase 1 "Inject secrets" | Add `workingDirectory: 'azure/API_based_HFace_AppInsight'`. It previously had none, so it wrote into the unused root `config.json`; Phase 1 only worked because its own config held hard-coded keys. |
| Phase 1 "Run Phase1" | `API_based_HFace_AppInsight` → `azure/API_based_HFace_AppInsight` |
| Phase 2 (all steps) | `Docker-Local-Inference` → `azure/Docker-Local-Inference` |
| Phase 3 (all steps) | `API_based_HFace_AppInsight` → `azure/API_based_HFace_AppInsight` |

The empty "Commit & Push" step and the commented-out Benchmark stage stay as they are.

### 5.3 Secret blanking
Set these values to `""` (the pipeline injects them at run time):
- `azure/API_based_HFace_AppInsight/config.json`: `blob_connection_string`, `sql_conn_str`, `app_insights_connection_string`, `hf_api_token`
- `azure/Docker-Local-Inference/config.json`: `app_insights_connection_string`
- `azure/legacy-powershell/config.json`: `blob_connection_string`, `sql_conn_str`, `app_insights_connection_string`, `hf_api_token`

### 5.4 Manual step (user)
In Azure DevOps, change the pipeline's YAML file path to `azure/azure-pipelines.yml`.

## 6. AWS architecture

One CloudFormation stack in `ap-south-1`.

| Resource | Configuration |
|---|---|
| S3 bucket | Block all public access; SSE-S3 encryption; TLS-only bucket policy. Prefixes: `runs/<run-id>/raw/`, `runs/<run-id>/translated/`, `runs/<run-id>/output/`, `models/<model-key>/`, plus the CodePipeline artifact prefix. Lifecycle: expire `runs/` and pipeline artifacts after 30 days; keep `models/`. |
| GitHub connection | `AWS::CodeStarConnections::Connection` for the private GitHub repo. It is created in PENDING state; the user approves it once in the console. |
| CodePipeline (V2) | Stages: Source → Phase1 → Phase2 → Phase3. The source action has `DetectChanges: false`, so runs start only manually (matches Azure `trigger: none`). Pipeline variable `RESET_TM` (default `true`). Each CodeBuild action receives `RUN_ID=#{codepipeline.PipelineExecutionId}` and `RESET_TM=#{variables.RESET_TM}`. |
| CodeBuild `phase1` | Image `aws/codebuild/standard:7.0`, `BUILD_GENERAL1_SMALL` |
| CodeBuild `phase2` | Image `aws/codebuild/standard:7.0`, `BUILD_GENERAL1_MEDIUM` (4 vCPU / 7 GB), privileged mode (Docker). Env var `PHASE2_CPUSET` (default `0,1`). |
| CodeBuild `phase3` | Image `aws/codebuild/standard:7.0`, `BUILD_GENERAL1_SMALL`; artifact `localized/**/*` |
| RDS for SQL Server | Engine `sqlserver-ex`, instance class parameter (default `db.t3.small`), 20 GB gp3, single-AZ, publicly accessible, `ManageMasterUserPassword: true` (RDS creates and stores the password in Secrets Manager), backups 0 days, `DeletionPolicy: Delete`. |
| Database network access | Security group allows TCP 1433 only from a customer-managed prefix list. `deploy.sh` fills that list with the current CodeBuild IP ranges for `ap-south-1` (from AWS `ip-ranges.json`) plus an optional `AdminCidr`. This mirrors Azure SQL's public endpoint plus firewall. |
| Secrets | The RDS-managed secret is passed to the Phase 2 CodeBuild project as `SECRETS_MANAGER` environment variables (`DB_USER` ← `username`, `DB_PASSWORD` ← `password`). S3 access uses the CodeBuild IAM role, so storage needs no keys. |
| IAM | CodePipeline role and CodeBuild role, scoped to this bucket, this secret, this connection and this log group. |
| CloudWatch Logs | One log group for all CodeBuild projects, 90-day retention. One saved Logs Insights query for `RESEARCH_DATA`. |

No ECR, VPC, NAT gateway, Fargate or Step Functions.

## 7. AWS components

### 7.1 `aws/config.json` (no secrets)
```json
{
  "SourceRepoPath": "source-folder",
  "InputFiles": ["resx_file_10.resx"],
  "TargetLanguages": ["fr-FR", "es-ES", "hi-IN", "de-DE", "ja-JP", "ru-RU", "zh-CN"],
  "TargetResxPath": "localized"
}
```
An empty `InputFiles` list means every `.resx` in `SourceRepoPath`. The bucket name, run ID, region and database host come from environment variables set by CloudFormation and CodePipeline.

### 7.2 `aws/phase1.py`
- `resx_to_xlf` is identical to the Azure version.
- Reads the files listed in `InputFiles` from `SourceRepoPath`.
- Uploads each output to `s3://<bucket>/runs/<RUN_ID>/raw/<file>.<lang>.xlf`.
- Exits non-zero if any file fails or nothing is uploaded.

### 7.3 `aws/translator/translation.py`
A copy of `azure/Docker-Local-Inference/translation.py`. Allowed differences:
- **Imports:** remove `azure.storage.blob`; add `s3_store`.
- **`main()`:**
  - Storage goes through `s3_store`: list `runs/<RUN_ID>/raw/`, download, upload to `runs/<RUN_ID>/translated/`.
  - The SQL connection string is built from `DB_HOST`, `DB_USER`, `DB_PASSWORD` and database `localization`, with the same ODBC options as the Azure string (`ODBC Driver 18`, `Encrypt=yes`, `TrustServerCertificate=yes`, `Connection Timeout=30`).
  - A raw object is deleted only after its translated file uploads successfully.
  - `main()` returns the number of failed files.
  - The `metrics_payload` dictionary keeps every Azure key with an identical expression, and adds `cloud_provider` (`"aws"`), `region`, `run_id`, `cpu_limit` (measured as `len(os.sched_getaffinity(0))`) and `cpu_model` (from `/proc/cpuinfo`).
- **`__main__` block:** exits `1` if `main()` reports failures, otherwise `0`.

Everything else (constants, model loading, `PerformanceProfiler`, `setup_logging`, `get_sql_connection`, the bulk SQL functions, `translate_local`, `process_xlf_file`) stays identical. That includes the existing OpenCensus in-process metric recording: without an Application Insights connection string, the existing code already skips the Azure exporters.

### 7.4 `aws/translator/s3_store.py`
A small module with `list_raw`, `download`, `upload_translated` and `delete_raw`, operating on `runs/<RUN_ID>/…`. It is kept separate from `translation.py` because that file loads the model at import time, so storage logic can only be unit-tested in its own module.

### 7.5 `aws/translator/db_admin.py`
Runs inside the Phase 2 image, which already has the ODBC driver.
- `init` connects to `master`, creates the `localization` database if it is missing (RDS SQL Server cannot create one at launch), then creates the table if it is missing:
  ```sql
  CREATE TABLE Translations (
      SourceText     NVARCHAR(434) NOT NULL,
      TargetLang     NVARCHAR(16)  NOT NULL,
      TranslatedText NVARCHAR(MAX) NULL,
      ModelName      NVARCHAR(100) NULL,
      LastUpdated    DATETIME2     NULL,
      CONSTRAINT PK_Translations PRIMARY KEY (SourceText, TargetLang)
  );
  ```
- `reset` deletes all rows from `Translations`.

If the user supplies Azure's exact table definition, this one is replaced to match it (see Section 13).

### 7.6 `aws/translator/Dockerfile` and `requirements.txt`
- `requirements.txt` is the Azure file plus one line, `boto3`, which `s3_store.py` needs inside the container. Nothing on the inference path changes.
- The Dockerfile is identical except the base image comes from `public.ecr.aws/docker/library/python:3.10-slim` (the same official image, from AWS's mirror) to avoid Docker Hub pull limits on shared CodeBuild IPs.

### 7.7 `aws/phase3.py`
- `xlf_to_resx` is identical to the Azure version.
- Downloads `runs/<RUN_ID>/translated/*.xlf` and writes `localized/<lang>/<file>.resx`.
- Uploads each result to `runs/<RUN_ID>/output/<lang>/<file>.resx`.
- Exits non-zero if there is nothing to merge or any file fails.

### 7.8 `aws/tools/export_telemetry.py`
`--run-id <id> [--out file.csv]`: reads the CloudWatch log group, keeps the `RESEARCH_DATA` lines for that run, and writes a CSV with the Azure payload keys first (in Azure order) followed by the AWS-only keys.

### 7.9 `aws/infra/deploy.sh` and `teardown.sh`
- **`deploy.sh`:** checks prerequisites (AWS CLI v2, credentials, region), runs `aws cloudformation deploy`, syncs the database prefix list with the current CodeBuild IP ranges, and prints the next steps (approve the GitHub connection, start a run).
- **`teardown.sh`:** empties the bucket (all object versions), deletes the stack and waits for completion.

## 8. Run flow

1. Start the pipeline manually (console, or `aws codepipeline start-pipeline-execution --name <name> [--variables name=RESET_TM,value=false]`).
2. **Source:** snapshot of the GitHub repo.
3. **Phase 1:** `pip install boto3` → `python aws/phase1.py aws/config.json` → 7 `.xlf` files in `raw/`.
4. **Phase 2:**
   1. **Model:** if `s3://<bucket>/models/<model-key>/model.bin` exists, sync it to `aws/translator/nllb_ct2`. Otherwise install `ctranslate2==4.7.1`, `transformers==4.38.2`, `sentencepiece` and CPU-only `torch`, run `ct2-transformers-converter --model facebook/nllb-200-distilled-600M --quantization int8`, and upload the result. `<model-key>` includes the model name, the quantization and the ctranslate2 version, so a version change triggers a fresh conversion.
   2. **Build:** copy `aws/config.json` into `aws/translator/`, then `docker build -t local-ai-translator aws/translator`.
   3. **Database:** `docker run … python db_admin.py init`, then `… reset` if `RESET_TM=true`.
   4. **Translate:** `docker run --cpuset-cpus=$PHASE2_CPUSET -v $(pwd)/aws/translator:/app -e RUN_ID -e BUCKET -e DB_HOST -e DB_USER -e DB_PASSWORD -e AWS_REGION -e AWS_DEFAULT_REGION -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI local-ai-translator python translation.py config.json 2>&1 | tee phase2.log`, with `set -o pipefail` so the container's exit code fails the step.
5. **Phase 3:** `pip install boto3` → `python aws/phase3.py aws/config.json` → files in `output/` plus the `localized/**` artifact.

## 9. Fairness rules

**Identical on both clouds:**
- Model, CT2 settings (`int8`, `device="auto"`, `beam_size=5`, `batch_type="tokens"`, `max_batch_size=1024`), `BATCH_SIZE=52` and `TOKEN_LIMIT=1024`.
- Tokenizer source (downloaded from Hugging Face at start-up) and the language map.
- The SQL bulk read, the `MERGE` write and the ODBC options.
- XML handling and every metric formula.
- The 2-second post-file sleep, the Docker image contents (apart from the base-image registry and the added `boto3`) and the Python package versions.
- The workload and the 2-core CPU allowance.

**Kept quirks (deliberately not fixed, for comparability):**
- Token counts are estimated as characters ÷ 4 with per-language multipliers.
- `cold_start_init_ms` times only the SQL connection.
- `download_ms` is subtracted from a file timer that does not include it.

**Known, documented differences:**
- Cloud services, region, network path and CPU model.
- The database instance size (Azure SQL free serverless vs RDS `db.t3.small` Express).
- `telemetry_overhead_ms` excludes the Application Insights exporter's background work. The same OpenCensus views are registered on AWS (without an exporter), so the in-process recording work matches.
- The converter is pinned on AWS (unpinned on Azure).
- On AWS, raw files are kept on failure and failures produce a non-zero exit code. Neither affects successful-run timing.

## 10. Error handling

| Failure | Behaviour |
|---|---|
| Any phase exits non-zero | CodePipeline stops; later stages do not run. |
| One file fails in Phase 2 | Error logged; its raw object is kept; other files continue; the stage fails at the end. "Retry" on the stage keeps the same `RUN_ID` and processes only the remaining raw files. |
| Database unreachable | `translation.py` exits before translating (existing behaviour) with a non-zero code (new). |
| Model cache missing or unloadable | The model is converted again and the cache refreshed. |
| Phase 3 finds no translated files | Fails rather than silently succeeding. |

## 11. Telemetry

- Container stdout goes through `tee` to the CodeBuild logs and on to CloudWatch Logs.
- The log line format is unchanged: `<timestamp> [INFO] 📊 RESEARCH_DATA: {json}`.
- The saved Logs Insights query filters `RESEARCH_DATA` and parses `run_id`, `target_lang`, `total_time`, `throughput_seg_per_hr`, `cache_rate`, `nmt_calls`, `azure_sql_query_ms`, `blob_download_ms` and `blob_upload_ms`.
- The field names `azure_sql_query_ms` and `blob_*` are kept on AWS so the columns line up. The CSV export is the primary comparison tool.

## 12. Security

- No secrets in the repository. Database credentials live only in Secrets Manager; S3 access uses IAM roles.
- The database is reachable only from CodeBuild's published IP ranges (plus an optional admin CIDR) over an encrypted connection.
- The S3 bucket is private, encrypted, and accepts TLS-only requests.
- IAM permissions are scoped to this stack's resources.
- **User action:** rotate the Azure storage key, the Azure SQL password and the Hugging Face tokens that remain in git history.

## 13. Testing and acceptance

**Offline (pytest; no AWS needed):**
- `test_parity.py` parses the Azure and AWS files with `ast`, without running them, and checks that:
  - every top-level node of `translation.py` except imports, `main` and the `__main__` block is identical;
  - every Azure `metrics_payload` key exists on AWS with an identical expression;
  - `resx_to_xlf` and `xlf_to_resx` are identical;
  - `requirements.txt` equals Azure's plus `boto3`;
  - the Dockerfiles differ only in the `FROM` line.
- `test_phase1.py` and `test_phase3.py` run against a fake S3 (moto). They check object keys and folder layout, `InputFiles` filtering, the output folder layout, and non-zero exits on failure.
- `test_s3_store.py` covers list, download, upload and delete-only-after-upload.
- `cfn-lint aws/infra/pipeline.yaml` passes.

**Live acceptance (needs the user's AWS account; ask before creating billable resources):** success criteria 1, 2 and 4 from Section 1.

**Azure:** the parity tests plus a review of the edited YAML paths. Running Azure requires the user's Azure DevOps project.

## 14. Cost and teardown

- **Always-on charge:** the RDS instance, roughly $1/day for `db.t3.small` Express (to be confirmed with the AWS pricing calculator).
- **Per run:** CodeBuild minutes, well under $1. The first Phase 2 run is longer because it converts the model.
- **Small items:** S3 storage, the Secrets Manager secret (about $0.40/month) and CodePipeline V2 action minutes are negligible.
- Delete the stack between experiment rounds with `teardown.sh`.

## 15. Open items and user actions

1. **Optional:** Azure's exact `Translations` table definition, to copy into `db_admin.py`.
2. Rotate the leaked Azure and Hugging Face credentials.
3. Update the Azure DevOps YAML path to `azure/azure-pipelines.yml`.
4. Approve the GitHub connection in the AWS console after the first deploy.

## 16. Implementation-time checks

Verify these early, because the design depends on them:
1. A Docker container inside privileged CodeBuild can use the build's IAM role when `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` is passed through.
2. `db.t3.small` with `sqlserver-ex` and `ManageMasterUserPassword` is available in `ap-south-1`.
3. Updating the stack does not wipe the prefix-list entries that `deploy.sh` manages. If it does, switch to adding the security-group ingress rules from `deploy.sh`.
4. `ctranslate2==4.7.1`, `transformers==4.38.2` and the CPU `torch` wheel install together on the CodeBuild Python and convert the model successfully.

## 17. Addendum (2026-10-16): EC2 runner

**Why:** the first live run showed CodeBuild refusing every build in this account (`AccountLimitExceededException: Cannot have more than 0 builds in queue for the account`), even a no-op build started directly. This is an account-level restriction that only AWS Support can lift; Service Quotas showed normal values. The user chose to run the pipeline on EC2 instead. The CodeBuild/CodePipeline path stays deployed and unchanged, and will work if AWS lifts the limit.

**Design:**
- `aws/infra/run_on_ec2.sh` (run locally):
  1. Uploads `git archive HEAD` to `s3://<data-bucket>/runs/<run-id>/source.tar.gz` (run IDs look like `ec2-20261016t120000z`).
  2. Launches one instance from the stack's launch template.
  3. Waits for SSM, then runs `aws/runner/run_all.sh` through SSM Run Command with output sent to the CloudWatch log group `/<project>/ec2-runner`.
  4. Reports the result and always terminates the instance.
- `aws/runner/run_all.sh` (on the instance) does what the three buildspecs do:
  - reads the database credentials from Secrets Manager;
  - reads `MODEL_KEY` from `aws/buildspecs/phase2.yml`;
  - runs `phase1.py`, the unchanged `scripts/run_phase2.sh` and `phase3.py`.
- **Stack additions:**
  - **Launch template:** Amazon Linux 2023 x86_64 (from the public SSM parameter), default `m5.large` (2 vCPU / 8 GB, Intel Xeon), 40 GB gp3 root volume, IMDSv2 with hop limit 2 so containers can use the instance role, terminate-on-shutdown. User data installs Docker and a Python venv with boto3, and schedules a 4-hour safety shutdown.
  - **Instance role:** SSM managed core policy, plus the same data-bucket, secret and log permissions as CodeBuild.
  - **Network:** a runner security group that the database security group accepts.
  - **Logs:** the runner log group, kept 90 days.

**Fairness:** an m5.large has exactly 2 vCPUs, like the Azure DS2_v2 agent (2 vCPU / 7 GB), so `--cpuset-cpus=0,1` gives the container every core and `host_cpu_count` equals `cpu_limit`. The inference path, image, scripts and telemetry are unchanged. Orchestration (a local script instead of CodePipeline) does not affect the per-file metrics.

**Telemetry:** SSM may batch several output lines into one CloudWatch event, so `export_telemetry.py` parses `RESEARCH_DATA` per line. Run it with `--log-group /cloud-loc/ec2-runner`.
