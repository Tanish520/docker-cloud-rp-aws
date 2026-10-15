# AWS pipeline

The AWS version of the localization pipeline. It runs the same three phases as [the Azure version](../azure/README.md), with the same model and settings, so runs on the two clouds can be compared directly. The design and its fairness rules are in [the design spec](../docs/superpowers/specs/2026-10-16-aws-migration-design.md).

| Phase | Where it runs | What it does |
|---|---|---|
| 1 | CodeBuild (small) | `phase1.py`: `.resx` → 7 XLIFF files in `s3://<data-bucket>/runs/<run-id>/raw/` |
| 2 | CodeBuild (medium, Docker) | `scripts/run_phase2.sh`: build the image, restore or convert the NLLB model, prepare the SQL translation memory, run `translator/translation.py` on 2 pinned cores |
| 3 | CodeBuild (small) | `phase3.py`: translated XLIFF → localized `.resx` in `runs/<run-id>/output/<lang>/` and in the `LocalizedResx` pipeline artifact |

## Prerequisites

- An AWS account with AWS CLI v2 configured (`aws configure`). Default region: `ap-south-1`.
- A default VPC in that region (most accounts have one; otherwise `aws ec2 create-default-vpc --region ap-south-1`).
- Python 3.9 or newer.
- The code you want to run pushed to GitHub. CodePipeline builds from the GitHub branch, not from your laptop.

## One-time setup

```bash
python3 -m venv aws/.venv
aws/.venv/bin/pip install -r aws/requirements-dev.txt
PYTHON=aws/.venv/bin/python bash aws/infra/deploy.sh
```

`deploy.sh` settings (environment variables): `AWS_REGION` (default `ap-south-1`), `STACK_NAME` and `PROJECT_NAME` (default `cloud-loc`), `GITHUB_REPOSITORY` (default `AayushGupta56/docker-cloud-rp`), `GITHUB_BRANCH` (default `main`), `DB_INSTANCE_CLASS` (default `db.t3.small`), `PHASE2_CPUSET` (default `0,1`), `ADMIN_CIDR` (optional extra address allowed to reach the database, e.g. your IP for inspecting it).

Then approve the GitHub connection once: AWS console → **Developer Tools** → **Settings** → **Connections** → `cloud-loc-github` → **Update pending connection**, and allow the AWS Connector app on the repository. The pipeline's automatic first run fails at Source while the connection is pending; that is expected.

## Running

```bash
aws codepipeline start-pipeline-execution --region ap-south-1 --name cloud-loc-pipeline
```

The command prints a `pipelineExecutionId`; that is the **run ID** used in S3 paths, logs and telemetry.

- `RESET_TM` (pipeline variable, default `true`) empties the translation memory before Phase 2, so the run starts at a 0% cache rate like the report's runs. To measure cache reuse, keep the memory from earlier runs:
  `aws codepipeline start-pipeline-execution --region ap-south-1 --name cloud-loc-pipeline --variables name=RESET_TM,value=false`
- The first run converts the model (about 10–15 extra minutes) and caches it in `s3://<data-bucket>/models/`; later runs reuse it.
- Watch progress in the CodePipeline console, or:
  `aws codepipeline get-pipeline-execution --region ap-south-1 --pipeline-name cloud-loc-pipeline --pipeline-execution-id <run-id> --query pipelineExecution.status`

## Results

- **Localized files:** `aws s3 ls s3://<data-bucket>/runs/<run-id>/output/ --recursive`, or download the `LocalizedResx` artifact from the pipeline run in the console. `<data-bucket>` is the `DataBucketName` stack output.
- **Telemetry CSV** (same column names and order as the Azure `RESEARCH_DATA` payload, plus `cloud_provider`, `region`, `run_id`, `cpu_limit`, `cpu_model`):
  `aws/.venv/bin/python aws/tools/export_telemetry.py --run-id <run-id> --out <run-id>.csv`
- **Quick look:** CloudWatch → Logs Insights → saved query `cloud-loc/RESEARCH_DATA per file`.

## Changing the workload

Edit [config.json](config.json): `InputFiles` (empty list = every `.resx` in `SourceRepoPath`) and `TargetLanguages`. Commit and push before starting a run. Keep the defaults (`resx_file_10.resx`, 7 languages) for comparisons with the Azure numbers.

## Tearing down

```bash
bash aws/infra/teardown.sh
```

It asks for confirmation, empties both buckets (including the cached model) and deletes the stack and database. Run it between experiment rounds to stop charges.

## Cost

The database is the only always-on charge, roughly $1/day for `db.t3.small` SQL Server Express while the stack exists (check the AWS pricing calculator for current prices). A pipeline run costs well under $1 in CodeBuild minutes. S3, Secrets Manager (about $0.40/month) and CodePipeline are negligible.

## Fairness notes

What is identical to Azure, which known telemetry quirks are kept on purpose, and what differs between the clouds are listed in section 9 of [the design spec](../docs/superpowers/specs/2026-10-16-aws-migration-design.md). The parity tests (`tests/test_parity.py`) fail if the shared translation logic drifts from the Azure code.

## Troubleshooting

- **Phase 2 cannot reach S3 from inside the container:** right after building the image, Phase 2 checks S3 access from inside the container. If the CodeBuild credentials endpoint is not usable there, it switches to short-lived keys (`aws configure export-credentials`) automatically and logs a warning. If it still fails ("The container cannot reach s3://…"), check the CodeBuild role's S3 permissions in `infra/pipeline.yaml`.
- **Phase 2 times out connecting to the database:** CodeBuild's IP ranges may have changed; run `deploy.sh` again (it re-syncs the firewall).
- **The cached model is broken:** `aws s3 rm s3://<data-bucket>/models/<model-key>/_COMPLETE` forces a fresh conversion on the next run.

## Tests

```bash
cd aws && .venv/bin/python -m pytest -q
```
