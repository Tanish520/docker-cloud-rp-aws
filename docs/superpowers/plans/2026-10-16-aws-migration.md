# AWS Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the Azure pipeline under `azure/` (runnable again) and build an AWS copy of the full three-phase localization pipeline under `aws/` that produces telemetry directly comparable with the Azure Docker runs.

**Architecture:** CodePipeline (manual start) runs three CodeBuild jobs. Phase 1 and Phase 3 are small Python scripts that move XLIFF/RESX files through one S3 bucket laid out per run (`runs/<run-id>/raw|translated|output/`). Phase 2 builds the same Docker image as Azure and runs a copy of the Azure `translation.py` in which only `main()` changes (S3 instead of Blob, credentials from Secrets Manager), pinned to 2 CPU cores. The translation memory lives in RDS for SQL Server Express. Everything is created by one CloudFormation stack.

**Tech Stack:** Python 3.9+ (tests), Python 3.10 (Docker image), Python 3.11 (CodeBuild host), boto3, moto, pytest, PyYAML, cfn-lint, CloudFormation, CodePipeline V2, CodeBuild `aws/codebuild/standard:7.0`, S3, RDS SQL Server Express, Secrets Manager, CloudWatch Logs, CTranslate2 4.7.1, NLLB-200 distilled 600M.

**Spec:** `docs/superpowers/specs/2026-10-16-aws-migration-design.md`

## Global Constraints

- All Python must run on Python 3.9 (local tests), 3.10 (Docker image) and 3.11 (CodeBuild host): no `match`, no `X | Y` annotations, no 3.10+-only APIs.
- Model and inference settings never change on the AWS side alone: `facebook/nllb-200-distilled-600M`, `ctranslate2==4.7.1`, `transformers==4.38.2`, `compute_type="int8"`, `beam_size=5`, `BATCH_SIZE = 52`, `TOKEN_LIMIT = 1024`.
- The AWS Docker image's `requirements.txt` is Azure's plus exactly one line, `boto3==1.35.36`; its Dockerfile differs only in the `FROM` line.
- Default workload: `resx_file_10.resx` × `fr-FR`, `es-ES`, `hi-IN`, `de-DE`, `ja-JP`, `ru-RU`, `zh-CN`.
- Phase 2 container is pinned with `--cpuset-cpus="$PHASE2_CPUSET"`, default `0,1`.
- S3 layout: `runs/<run-id>/raw/`, `runs/<run-id>/translated/`, `runs/<run-id>/output/<lang>/`, `models/<model-key>/` (complete only when `_COMPLETE` exists).
- Database: RDS for SQL Server Express, database `localization`, table `dbo.Translations`.
- Region `ap-south-1` is a deploy-time default only; the CloudFormation template must not contain a region name.
- No secrets in the repository. Python files under `azure/` stay byte-identical to git history.
- Work happens on branch `aws-migration`. Never commit to `main`. Never push or create AWS resources without asking the user first.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests run from `aws/`: `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Interrupted model upload** leaves files in `models/<key>/` but no `_COMPLETE` marker → the next run must reconvert, not load a half-uploaded model. Pinned by `test_partial_upload_without_marker_is_not_fetched` (Task 8).
2. **RDS-generated password containing `;` or `}`** → the ODBC connection string must still parse. Pinned by `test_password_with_odbc_special_characters_is_braced` (Task 3).
3. **Retrying Phase 2 after one language fails** → only the failed language is redone; finished languages are not duplicated. Pinned by `test_retry_after_partial_failure_only_redoes_failed_file` (Task 6).
4. **Phase 1 misconfiguration** (missing input file, empty language list) → the stage must fail instead of passing with zero or partial uploads. Pinned by `test_missing_input_file_fails_but_uploads_the_rest` and `test_empty_language_list_fails` (Task 4).
5. **Run folders with more than 1000 objects** (future work translating all 20 files into many languages) → listing must paginate. Pinned by `test_list_paginates_past_1000_objects` (Task 2).

---

## Before you start

- [ ] **Create the branch and commit the design documents**

```bash
cd /Users/tanishgupta/Desktop/docker-cloud-rp-aayush
git checkout -b aws-migration
git add docs/superpowers/specs/2026-10-16-aws-migration-design.md docs/superpowers/plans/2026-10-16-aws-migration.md
git commit -m "docs: AWS migration design and implementation plan" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

All paths below are relative to the repository root unless a step says `cd aws`.

---

### Task 1: Preserve the Azure pipeline under `azure/` and set up the test environment

**Files:**
- Create: `aws/requirements-dev.txt`, `aws/pytest.ini`, `aws/tests/helpers.py`, `aws/tests/test_azure_preservation.py`, `azure/README.md`
- Move (git mv): `azure-pipelines.yml` → `azure/azure-pipelines.yml`; `Docker-Local-Inference/` → `azure/Docker-Local-Inference/`; `SLALogger.ps1`, `Walkthrough.md`, `config.json`, `source-folder/azure-pipelines.yml`, `target-folder/` → `azure/legacy-powershell/`
- Restore from `8434ded^`: `azure/API_based_HFace_AppInsight/{phase1.py,phase3.py,config.json,source-folder/resx_file_10.resx}`
- Modify: `azure/azure-pipelines.yml` (paths only), three `config.json` files (blank secrets), `.gitignore`

**Interfaces:**
- Produces: `aws/tests/helpers.py` with `AWS_DIR`, `REPO_ROOT`, `AZURE_DIR`, `BUCKET = "test-bucket"`, `REGION = "ap-south-1"`, `load_module(name, path)`, `write_resx(path, values)`, `forbidden_connect(*args, **kwargs)`. Every later task imports from it.

- [ ] **Step 1: Create the dev requirements and pytest config**

`aws/requirements-dev.txt`:
```
pytest>=8,<9
moto[s3]>=5,<6
boto3>=1.34,<2
PyYAML>=6,<7
cfn-lint>=1,<2
opencensus-ext-azure==1.1.13
psutil==5.9.8
```

`aws/pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = . tests translator infra tools
```

- [ ] **Step 2: Create the virtual environment**

Run:
```bash
python3 -m venv aws/.venv
aws/.venv/bin/pip install --quiet --upgrade pip
aws/.venv/bin/pip install --quiet -r aws/requirements-dev.txt
```
Expected: installs without errors (`.venv/` is already in `.gitignore`).

- [ ] **Step 3: Create the shared test helpers**

`aws/tests/helpers.py`:
```python
"""Paths and small helpers shared by the AWS test suite."""
import importlib.util
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parent
AZURE_DIR = REPO_ROOT / "azure"

BUCKET = "test-bucket"
REGION = "ap-south-1"


def load_module(name, path):
    """Import a file as a fresh module (used for modules that do work at import time)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_resx(path, values):
    """Write a minimal .resx file whose <data> entries hold the given strings."""
    items = "".join(
        f'<data name="S{i}" xml:space="preserve"><value>{value}</value></data>'
        for i, value in enumerate(values)
    )
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><root>{items}</root>', encoding="utf-8")


def forbidden_connect(*args, **kwargs):
    raise AssertionError("tests must not open a real database connection")
```

- [ ] **Step 4: Write the failing Azure preservation tests**

`aws/tests/test_azure_preservation.py`:
```python
import json
import re
import subprocess

import yaml

from helpers import AWS_DIR, AZURE_DIR, REPO_ROOT

HISTORY_COMMIT = "8434ded"   # last commit before this work: Azure Docker files must match it
RESTORED_FROM = "8434ded^"   # still contains API_based_HFace_AppInsight/
PHASE_DIR = AZURE_DIR / "API_based_HFace_AppInsight"
DOCKER_DIR = AZURE_DIR / "Docker-Local-Inference"
LEGACY_DIR = AZURE_DIR / "legacy-powershell"

SECRET_PATTERNS = [
    re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"InstrumentationKey=[0-9a-fA-F-]{36}"),
    re.compile(r"PWD=(?![{<\"';])[^;\s\"]+"),
]
TEXT_SUFFIXES = {".json", ".yml", ".yaml", ".py", ".ps1", ".md", ".sh", ".txt", ".resx", ".xlf"}
SECRET_KEYS = ["blob_connection_string", "sql_conn_str", "app_insights_connection_string", "hf_api_token"]


def git_show(spec):
    return subprocess.run(["git", "show", spec], cwd=REPO_ROOT, capture_output=True, check=True).stdout


def scanned_files():
    for base in (AZURE_DIR, AWS_DIR):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            if ".venv" in path.parts or path.is_relative_to(AWS_DIR / "tests"):
                continue
            yield path


def pipeline_steps():
    pipeline = yaml.safe_load((AZURE_DIR / "azure-pipelines.yml").read_text(encoding="utf-8"))
    for stage in pipeline["stages"]:
        for job in stage["jobs"]:
            for step in job["steps"]:
                yield stage["stage"], step


def test_no_secrets_in_azure_or_aws_folders():
    leaks = []
    for path in scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                leaks.append(f"{path.relative_to(REPO_ROOT)} matches {pattern.pattern}")
    assert leaks == []


def test_restored_phase_files_match_git_history():
    for rel in ("phase1.py", "phase3.py", "source-folder/resx_file_10.resx"):
        expected = git_show(f"{RESTORED_FROM}:API_based_HFace_AppInsight/{rel}")
        assert (PHASE_DIR / rel).read_bytes() == expected, rel


def test_docker_inference_code_is_unchanged():
    for rel in ("translation.py", "Dockerfile", "requirements.txt", "quantization_benchmark.py"):
        expected = git_show(f"{HISTORY_COMMIT}:Docker-Local-Inference/{rel}")
        assert (DOCKER_DIR / rel).read_bytes() == expected, rel


def test_azure_configs_keep_their_keys_with_blank_secrets():
    expectations = {
        PHASE_DIR / "config.json": SECRET_KEYS,
        DOCKER_DIR / "config.json": ["app_insights_connection_string"],
        LEGACY_DIR / "config.json": SECRET_KEYS,
    }
    for path, keys in expectations.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            assert data[key] == "", f"{path.relative_to(REPO_ROOT)}: {key}"


def test_pipeline_working_directories_exist():
    dirs = {step["workingDirectory"] for _, step in pipeline_steps() if "workingDirectory" in step}
    assert dirs == {"azure/API_based_HFace_AppInsight", "azure/Docker-Local-Inference"}
    for directory in dirs:
        assert (REPO_ROOT / directory).is_dir(), directory


def test_every_step_using_config_json_sets_its_folder():
    for stage, step in pipeline_steps():
        if "config.json" in step.get("script", ""):
            assert "workingDirectory" in step, f"{stage}: {step.get('displayName')}"


def test_phase1_secrets_are_injected_into_phase1_config():
    inject = [step for stage, step in pipeline_steps() if stage == "Phase1" and "jq" in step.get("script", "")]
    assert [step["workingDirectory"] for step in inject] == ["azure/API_based_HFace_AppInsight"]


def test_legacy_powershell_files_were_moved():
    for rel in ("SLALogger.ps1", "Walkthrough.md", "config.json", "azure-pipelines.yml",
                "target-folder/resx_file_20_te-IN.resx"):
        assert (LEGACY_DIR / rel).is_file(), rel
    for old in ("azure-pipelines.yml", "config.json", "SLALogger.ps1", "Walkthrough.md",
                "source-folder/azure-pipelines.yml", "Docker-Local-Inference"):
        assert not (REPO_ROOT / old).exists(), old
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_azure_preservation.py -q`
Expected: FAIL (missing `azure/` files). `test_no_secrets_in_azure_or_aws_folders` may pass at this point because `azure/` does not exist yet.

- [ ] **Step 6: Move the Azure files**

```bash
mkdir -p azure/legacy-powershell
git mv azure-pipelines.yml azure/azure-pipelines.yml
git mv Docker-Local-Inference azure/Docker-Local-Inference
git mv SLALogger.ps1 azure/legacy-powershell/SLALogger.ps1
git mv Walkthrough.md azure/legacy-powershell/Walkthrough.md
git mv config.json azure/legacy-powershell/config.json
git mv source-folder/azure-pipelines.yml azure/legacy-powershell/azure-pipelines.yml
git mv target-folder azure/legacy-powershell/target-folder
```

- [ ] **Step 7: Restore Phase 1 and Phase 3 from history**

```bash
mkdir -p azure/API_based_HFace_AppInsight/source-folder
for f in phase1.py phase3.py config.json source-folder/resx_file_10.resx; do
  git show "8434ded^:API_based_HFace_AppInsight/$f" > "azure/API_based_HFace_AppInsight/$f"
done
```

- [ ] **Step 8: Blank the secrets in the three config files**

```bash
python3 - <<'EOF'
import json

SECRET_KEYS = ["blob_connection_string", "sql_conn_str", "app_insights_connection_string", "hf_api_token"]
targets = {
    "azure/API_based_HFace_AppInsight/config.json": SECRET_KEYS,
    "azure/Docker-Local-Inference/config.json": ["app_insights_connection_string"],
    "azure/legacy-powershell/config.json": SECRET_KEYS,
}
for path, keys in targets.items():
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in keys:
        data[key] = ""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("blanked", path)
EOF
```

- [ ] **Step 9: Update the folder paths in `azure/azure-pipelines.yml`**

Use the Edit tool with `replace_all: true` twice:
- `workingDirectory: 'API_based_HFace_AppInsight'` → `workingDirectory: 'azure/API_based_HFace_AppInsight'`
- `workingDirectory: 'Docker-Local-Inference'` → `workingDirectory: 'azure/Docker-Local-Inference'`

Leave the commented-out Benchmark stage untouched.

Then add the missing working directory to the Phase 1 secret-injection step. Replace:
```yaml
              GITHUB_PAT: $(GITHUB_PAT)
            displayName: "Inject secrets into config.json"

          - script: |
              set -e
              python3 -m pip install azure-storage-blob
              echo "🚀 Starting Phase1 extraction..."
```
with:
```yaml
              GITHUB_PAT: $(GITHUB_PAT)
            workingDirectory: 'azure/API_based_HFace_AppInsight'
            displayName: "Inject secrets into config.json"

          - script: |
              set -e
              python3 -m pip install azure-storage-blob
              echo "🚀 Starting Phase1 extraction..."
```

- [ ] **Step 10: Write `azure/README.md`**

````markdown
# Azure pipeline (preserved)

This folder holds the Azure version of the localization pipeline: the Docker local-inference generation measured in the end-semester report. It was moved here unchanged apart from folder paths and blanked secrets, so it can be re-run for side-by-side comparisons with [the AWS version](../aws/README.md).

| Path | What it is |
|---|---|
| `azure-pipelines.yml` | The three-stage Azure DevOps pipeline (extract → Docker translate → merge) |
| `API_based_HFace_AppInsight/` | Phase 1 (`phase1.py`) and Phase 3 (`phase3.py`), restored from git history, plus their input `source-folder/resx_file_10.resx` |
| `Docker-Local-Inference/` | Phase 2: Dockerfile and `translation.py` (NLLB-200 600M, CTranslate2 INT8), plus the quantization benchmark |
| `legacy-powershell/` | Archive of the earlier PowerShell generation. Not runnable: its `scripts/function1.ps1` and `function2.ps1` are not in the repo. |

## Running it again

1. In Azure DevOps, open the pipeline → **Edit** → **⋮** → **Settings**, and set **YAML file path** to `azure/azure-pipelines.yml`.
2. Make sure these pipeline variables exist (mark them secret): `AZURE_STORAGE_CONN`, `AZURE_SQL_CONN`, `HUGGINGFACE_TOKEN`, `APPINSIGHTS_CONNECTION_STRING`. The pipeline writes them into the `config.json` files at run time; the committed files contain empty values.
3. Run the pipeline manually (`trigger: none`).

## What changed compared with the report runs

- Folder paths gained an `azure/` prefix.
- The Phase 1 "Inject secrets" step now writes into `API_based_HFace_AppInsight/config.json`. It used to write into an unused root `config.json`, and Phase 1 only worked because its own config had keys hard-coded in it.
- No Python code changed.

Known issues kept on purpose, so the Azure behaviour matches the report:
- The "Commit & Push" step only sets the git identity; localized files are not committed.
- Phase 2 deletes a raw input even when translating it failed.
- `quantization_benchmark.py` forces `▁es` instead of `spa_Latn` as the target-language token.

**Security:** keys that were committed earlier are still in git history. Rotate the Azure storage key, the Azure SQL password and the Hugging Face tokens.
````

- [ ] **Step 11: Add ignore rules for AWS build outputs**

Append to `.gitignore`:
```
# --- AWS pipeline build outputs ---
aws/translator/nllb_ct2/
aws/translator/config.json
/localized/
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_azure_preservation.py -q`
Expected: 8 passed.

- [ ] **Step 13: Commit**

```bash
git add -A azure aws/requirements-dev.txt aws/pytest.ini aws/tests/helpers.py aws/tests/test_azure_preservation.py .gitignore
git status --short   # verify: only moves/renames, the restored folder, the new aws/ files and .gitignore
git commit -m "refactor: preserve Azure pipeline under azure/ and restore Phase 1/3" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: S3 run store

**Files:**
- Create: `aws/translator/s3_store.py`, `aws/tests/conftest.py`, `aws/tests/test_s3_store.py`

**Interfaces:**
- Consumes: `helpers.BUCKET`, `helpers.REGION`
- Produces:
  - `s3_store.RAW = "raw"`, `TRANSLATED = "translated"`, `OUTPUT = "output"`
  - `s3_store.StoredFile(name, key)`: namedtuple; `name` is the bare file name, `key` the full S3 key
  - `s3_store.RunStore(bucket, run_id, client=None)` with: `.bucket`, `.run_id`, `.client`, `prefix(stage) -> str`, `.raw_prefix`, `key(stage, name) -> str`, `upload_raw(local_path, name) -> str`, `list_raw() -> list[StoredFile]` (sorted), `download_raw(name, local_path)`, `delete_raw(name)`, `upload_translated(local_path, name) -> str`, `list_translated() -> list[StoredFile]`, `download_translated(name, local_path)`, `upload_output(local_path, relative_name) -> str`
  - pytest fixtures `aws_env` (fake credentials, region `ap-south-1`) and `s3` (moto client with bucket `test-bucket`), and `stub_modules` (installs fake modules into `sys.modules`)

- [ ] **Step 1: Create the shared fixtures**

`aws/tests/conftest.py`:
```python
import sys
import types

import boto3
import pytest
from moto import mock_aws

from helpers import BUCKET, REGION


@pytest.fixture
def aws_env(monkeypatch):
    """Fake credentials and region so nothing can reach a real AWS account."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def s3(aws_env):
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield client


@pytest.fixture
def stub_modules(monkeypatch):
    """Install fake modules, e.g. stub_modules(pyodbc={"connect": fn})."""
    def install(**modules):
        for name, attrs in modules.items():
            module = types.ModuleType(name)
            for attr, value in attrs.items():
                setattr(module, attr, value)
            monkeypatch.setitem(sys.modules, name, module)
    return install
```

- [ ] **Step 2: Write the failing tests**

`aws/tests/test_s3_store.py`:
```python
import pytest

from helpers import BUCKET
from s3_store import RunStore, StoredFile


def keys(s3, prefix=""):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


@pytest.fixture
def store(s3):
    return RunStore(BUCKET, "run-1", client=s3)


def test_keys_are_scoped_to_the_run(store):
    assert store.raw_prefix == "runs/run-1/raw/"
    assert store.key("translated", "a.es-ES.xlf") == "runs/run-1/translated/a.es-ES.xlf"


@pytest.mark.parametrize("run_id", ["", "a/b", None])
def test_rejects_unsafe_run_ids(s3, run_id):
    with pytest.raises(ValueError):
        RunStore(BUCKET, run_id, client=s3)


def test_rejects_missing_bucket(s3):
    with pytest.raises(ValueError):
        RunStore("", "run-1", client=s3)


def test_upload_list_and_download_raw(store, tmp_path):
    src = tmp_path / "a.es-ES.xlf"
    src.write_text("hello", encoding="utf-8")
    assert store.upload_raw(str(src), "a.es-ES.xlf") == "runs/run-1/raw/a.es-ES.xlf"
    assert store.list_raw() == [StoredFile("a.es-ES.xlf", "runs/run-1/raw/a.es-ES.xlf")]
    dest = tmp_path / "copy.xlf"
    store.download_raw("a.es-ES.xlf", str(dest))
    assert dest.read_text(encoding="utf-8") == "hello"


def test_list_ignores_other_runs_other_stages_and_nested_keys(s3, store):
    for key in ("runs/run-1/raw/a.xlf", "runs/run-1/raw/nested/b.xlf",
                "runs/run-2/raw/c.xlf", "runs/run-1/translated/d.xlf"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    assert [f.name for f in store.list_raw()] == ["a.xlf"]
    assert [f.name for f in store.list_translated()] == ["d.xlf"]


def test_list_paginates_past_1000_objects(s3, store):
    for i in range(1001):
        s3.put_object(Bucket=BUCKET, Key=f"runs/run-1/raw/f{i:04d}.xlf", Body=b"x")
    names = [f.name for f in store.list_raw()]
    assert len(names) == 1001
    assert names[0] == "f0000.xlf" and names[-1] == "f1000.xlf"


def test_delete_raw_removes_only_that_file(s3, store):
    for name in ("a.xlf", "b.xlf"):
        s3.put_object(Bucket=BUCKET, Key=f"runs/run-1/raw/{name}", Body=b"x")
    store.delete_raw("a.xlf")
    assert keys(s3, "runs/run-1/raw/") == ["runs/run-1/raw/b.xlf"]


def test_translated_round_trip(store, tmp_path):
    src = tmp_path / "a.es-ES.xlf"
    src.write_text("hola", encoding="utf-8")
    store.upload_translated(str(src), "a.es-ES.xlf")
    dest = tmp_path / "back.xlf"
    store.download_translated("a.es-ES.xlf", str(dest))
    assert dest.read_text(encoding="utf-8") == "hola"


def test_upload_output_keeps_language_subfolder(s3, store, tmp_path):
    src = tmp_path / "resx_file_10.resx"
    src.write_text("<root/>", encoding="utf-8")
    assert store.upload_output(str(src), "es-ES/resx_file_10.resx") == "runs/run-1/output/es-ES/resx_file_10.resx"
    assert keys(s3, "runs/run-1/output/") == ["runs/run-1/output/es-ES/resx_file_10.resx"]


def test_failed_upload_raises_and_leaves_raw_in_place(s3, store, tmp_path):
    s3.put_object(Bucket=BUCKET, Key="runs/run-1/raw/a.xlf", Body=b"x")
    with pytest.raises(Exception):
        store.upload_translated(str(tmp_path / "missing.xlf"), "a.xlf")
    assert keys(s3, "runs/run-1/raw/") == ["runs/run-1/raw/a.xlf"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_s3_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 's3_store'`.

- [ ] **Step 4: Implement the store**

`aws/translator/s3_store.py`:
```python
"""S3 layout for one pipeline run: runs/<run_id>/{raw,translated,output}/."""
from collections import namedtuple

import boto3

RAW = "raw"
TRANSLATED = "translated"
OUTPUT = "output"

StoredFile = namedtuple("StoredFile", ["name", "key"])


class RunStore:
    def __init__(self, bucket, run_id, client=None):
        if not bucket:
            raise ValueError("bucket is required")
        if not run_id or "/" in run_id:
            raise ValueError(f"invalid run_id: {run_id!r}")
        self.bucket = bucket
        self.run_id = run_id
        self.client = client or boto3.client("s3")

    def prefix(self, stage):
        return f"runs/{self.run_id}/{stage}/"

    @property
    def raw_prefix(self):
        return self.prefix(RAW)

    def key(self, stage, name):
        return self.prefix(stage) + name

    def _list(self, stage):
        prefix = self.prefix(stage)
        found = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if name and "/" not in name:
                    found.append(StoredFile(name, obj["Key"]))
        return sorted(found)

    def _upload(self, stage, local_path, name):
        key = self.key(stage, name)
        self.client.upload_file(local_path, self.bucket, key)
        return key

    def upload_raw(self, local_path, name):
        return self._upload(RAW, local_path, name)

    def list_raw(self):
        return self._list(RAW)

    def download_raw(self, name, local_path):
        self.client.download_file(self.bucket, self.key(RAW, name), local_path)

    def delete_raw(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self.key(RAW, name))

    def upload_translated(self, local_path, name):
        return self._upload(TRANSLATED, local_path, name)

    def list_translated(self):
        return self._list(TRANSLATED)

    def download_translated(self, name, local_path):
        self.client.download_file(self.bucket, self.key(TRANSLATED, name), local_path)

    def upload_output(self, local_path, relative_name):
        return self._upload(OUTPUT, local_path, relative_name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_s3_store.py -q`
Expected: 12 passed.

- [ ] **Step 6: Commit**

```bash
git add aws/translator/s3_store.py aws/tests/conftest.py aws/tests/test_s3_store.py
git commit -m "feat(aws): add per-run S3 store" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: AWS runtime helpers (SQL connection string, host info)

**Files:**
- Create: `aws/translator/aws_runtime.py`, `aws/tests/test_aws_runtime.py`

**Interfaces:**
- Produces:
  - `aws_runtime.DEFAULT_DB_NAME = "localization"`
  - `aws_runtime.db_name() -> str` (reads `DB_NAME`, validates it, raises `ValueError` if unsafe)
  - `aws_runtime.sql_conn_str(database=None) -> str` (reads `DB_HOST`, `DB_USER`, `DB_PASSWORD`)
  - `aws_runtime.cpu_limit() -> int`, `aws_runtime.cpu_model(cpuinfo_path="/proc/cpuinfo") -> str`
  - `aws_runtime.extra_fields() -> dict` with exactly the keys `cloud_provider`, `region`, `run_id`, `cpu_limit`, `cpu_model`

- [ ] **Step 1: Write the failing tests**

`aws/tests/test_aws_runtime.py`:
```python
import pytest

import aws_runtime


@pytest.fixture
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_USER", "dbadmin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.delenv("DB_NAME", raising=False)


def test_connection_string_matches_azure_odbc_options(db_env):
    conn = aws_runtime.sql_conn_str()
    assert conn.startswith("DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:db.example.com,1433;")
    assert "DATABASE=localization;" in conn
    assert "UID={dbadmin};" in conn and "PWD={secret};" in conn
    for option in ("Encrypt=yes;", "TrustServerCertificate=yes;", "Connection Timeout=30;",
                   "MultipleActiveResultSets=False;", "Persist Security Info=False;"):
        assert option in conn


def test_password_with_odbc_special_characters_is_braced(db_env, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "p;a}ss")
    assert "PWD={p;a}}ss};" in aws_runtime.sql_conn_str()


def test_explicit_database_overrides_default(db_env):
    assert "DATABASE=master;" in aws_runtime.sql_conn_str("master")


def test_db_name_comes_from_environment(db_env, monkeypatch):
    monkeypatch.setenv("DB_NAME", "tm_experiments")
    assert aws_runtime.db_name() == "tm_experiments"
    assert "DATABASE=tm_experiments;" in aws_runtime.sql_conn_str()


@pytest.mark.parametrize("bad", ["x];DROP DATABASE y;--", "has space", "1starts_with_digit", ""])
def test_unsafe_db_names_are_rejected(db_env, monkeypatch, bad):
    monkeypatch.setenv("DB_NAME", bad)
    with pytest.raises(ValueError):
        aws_runtime.db_name()


def test_cpu_model_reads_proc_cpuinfo_format(tmp_path):
    info = tmp_path / "cpuinfo"
    info.write_text("processor\t: 0\nmodel name\t: AMD EPYC 7R13 Processor\nflags\t: fpu\n")
    assert aws_runtime.cpu_model(str(info)) == "AMD EPYC 7R13 Processor"


def test_cpu_model_falls_back_when_unavailable(tmp_path):
    assert aws_runtime.cpu_model(str(tmp_path / "missing")) == "unknown"


def test_extra_fields(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("RUN_ID", "run-42")
    fields = aws_runtime.extra_fields()
    assert set(fields) == {"cloud_provider", "region", "run_id", "cpu_limit", "cpu_model"}
    assert fields["cloud_provider"] == "aws"
    assert fields["region"] == "ap-south-1"
    assert fields["run_id"] == "run-42"
    assert isinstance(fields["cpu_limit"], int) and fields["cpu_limit"] >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_aws_runtime.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aws_runtime'`.

- [ ] **Step 3: Implement**

`aws/translator/aws_runtime.py`:
```python
"""AWS-specific runtime settings: SQL connection string and host facts for telemetry."""
import os
import re

DEFAULT_DB_NAME = "localization"
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def db_name():
    name = os.environ.get("DB_NAME", DEFAULT_DB_NAME)
    if not _DB_NAME_RE.match(name):
        raise ValueError(f"invalid DB_NAME: {name!r}")
    return name


def _odbc_value(value):
    # Braces let the value contain ';' and other separators; '}' is escaped by doubling.
    return "{" + value.replace("}", "}}") + "}"


def sql_conn_str(database=None):
    """Same ODBC options as the Azure connection string, pointed at RDS."""
    database = database or db_name()
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER=tcp:{os.environ['DB_HOST']},1433;"
        f"DATABASE={database};"
        "Persist Security Info=False;"
        f"UID={_odbc_value(os.environ['DB_USER'])};"
        f"PWD={_odbc_value(os.environ['DB_PASSWORD'])};"
        "MultipleActiveResultSets=False;Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=30;"
    )


def cpu_limit():
    """CPU cores this process may run on (respects docker --cpuset-cpus)."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def cpu_model(cpuinfo_path="/proc/cpuinfo"):
    try:
        with open(cpuinfo_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def extra_fields():
    """RESEARCH_DATA fields that exist only in the AWS version."""
    return {
        "cloud_provider": "aws",
        "region": os.environ.get("AWS_REGION", "unknown"),
        "run_id": os.environ.get("RUN_ID", "unknown"),
        "cpu_limit": cpu_limit(),
        "cpu_model": cpu_model(),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_aws_runtime.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add aws/translator/aws_runtime.py aws/tests/test_aws_runtime.py
git commit -m "feat(aws): add runtime helpers for RDS connection and host telemetry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Phase 1 (extract to S3) and the AWS config

**Files:**
- Create: `aws/config.json`, `aws/phase1.py`, `aws/tests/astutil.py`, `aws/tests/test_parity.py`, `aws/tests/test_phase1.py`

**Interfaces:**
- Consumes: `translator.s3_store.RAW`, `translator.s3_store.RunStore` (Task 2)
- Produces:
  - `phase1.resx_to_xlf(resx_path, lang_code) -> ElementTree` (identical to Azure)
  - `phase1.select_input_files(source_path, input_files) -> list[str]`
  - `phase1.main(config_file, store=None) -> int` (number of failures; reads `BUCKET` and `RUN_ID` when `store` is None)
  - `astutil.parse(path)`, `astutil.function_dump(path, name)`, `astutil.shared_top_level(path, exclude=("main",))`, `astutil.import_dumps(path)`, `astutil.assigned_value_dump(path, func_name, target)`, `astutil.statement_slice(path, func_name, first_target, last_target)`

- [ ] **Step 1: Create the AWS config**

`aws/config.json`:
```json
{
  "SourceRepoPath": "source-folder",
  "InputFiles": ["resx_file_10.resx"],
  "TargetLanguages": ["fr-FR", "es-ES", "hi-IN", "de-DE", "ja-JP", "ru-RU", "zh-CN"],
  "TargetResxPath": "localized"
}
```

- [ ] **Step 2: Create the AST helpers used by the parity tests**

`aws/tests/astutil.py`:
```python
"""Compare Python files structurally (comments and formatting are ignored)."""
import ast
from pathlib import Path


def parse(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _is_main_guard(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise LookupError(f"function {name!r} not found")


def function_dump(path, name):
    return ast.dump(_find_function(parse(path), name))


def shared_top_level(path, exclude=("main",)):
    """Top-level statements except imports, the excluded functions and the __main__ guard."""
    out = []
    for node in parse(path).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_main_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exclude:
            continue
        out.append(ast.dump(node))
    return out


def import_dumps(path):
    return {ast.dump(node) for node in parse(path).body if isinstance(node, (ast.Import, ast.ImportFrom))}


def _assigned_names(stmt):
    if isinstance(stmt, ast.Assign):
        return {target.id for target in stmt.targets if isinstance(target, ast.Name)}
    return set()


def assigned_value_dump(path, func_name, target):
    for node in ast.walk(_find_function(parse(path), func_name)):
        if isinstance(node, ast.Assign) and target in _assigned_names(node):
            return ast.dump(node.value)
    raise LookupError(f"{target!r} is never assigned in {func_name}()")


def statement_slice(path, func_name, first_target, last_target):
    """Dumps of the statements from `first_target = ...` to `last_target = ...` (inclusive)."""
    for node in ast.walk(_find_function(parse(path), func_name)):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            index = {}
            for i, stmt in enumerate(stmts):
                for name in _assigned_names(stmt):
                    index.setdefault(name, i)
            if first_target in index and last_target in index:
                return [ast.dump(s) for s in stmts[index[first_target]:index[last_target] + 1]]
    raise LookupError(f"no block assigns both {first_target!r} and {last_target!r} in {func_name}()")
```

- [ ] **Step 3: Write the failing parity test**

`aws/tests/test_parity.py`:
```python
"""The AWS pipeline must keep the Azure logic so results stay comparable."""
from astutil import (assigned_value_dump, function_dump, import_dumps, shared_top_level,
                     statement_slice)
from helpers import AWS_DIR, AZURE_DIR

AZ_PHASES = AZURE_DIR / "API_based_HFace_AppInsight"
AZ_DOCKER = AZURE_DIR / "Docker-Local-Inference"
AWS_TRANSLATOR = AWS_DIR / "translator"


def test_resx_to_xlf_is_identical_to_azure():
    assert function_dump(AWS_DIR / "phase1.py", "resx_to_xlf") == function_dump(AZ_PHASES / "phase1.py", "resx_to_xlf")
```

- [ ] **Step 4: Write the failing Phase 1 tests**

`aws/tests/test_phase1.py`:
```python
import json
import xml.etree.ElementTree as ET

import pytest

import phase1
from helpers import AWS_DIR, BUCKET, REPO_ROOT, write_resx

REPORT_LANGS = ["fr-FR", "es-ES", "hi-IN", "de-DE", "ja-JP", "ru-RU", "zh-CN"]


def raw_keys(s3):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="runs/run-1/raw/")
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


@pytest.fixture
def run_env(s3, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("RUN_ID", "run-1")
    return tmp_path


def write_config(tmp_path, **overrides):
    config = json.loads((AWS_DIR / "config.json").read_text(encoding="utf-8"))
    config.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def test_default_config_produces_the_report_workload(s3, run_env):
    config = write_config(run_env, SourceRepoPath=str(REPO_ROOT / "source-folder"))
    assert phase1.main(config) == 0
    assert raw_keys(s3) == sorted(f"runs/run-1/raw/resx_file_10.{lang}.xlf" for lang in REPORT_LANGS)
    body = s3.get_object(Bucket=BUCKET, Key="runs/run-1/raw/resx_file_10.es-ES.xlf")["Body"].read()
    root = ET.fromstring(body)
    assert root.find("file").get("target-language") == "es-ES"
    assert len(root.findall(".//trans-unit")) == 50
    assert list(run_env.glob("*.xlf")) == []  # temporary files are cleaned up


def test_empty_input_list_means_every_resx_in_the_folder(s3, run_env):
    source = run_env / "src"
    source.mkdir()
    write_resx(source / "a.resx", ["One"])
    write_resx(source / "b.resx", ["Two"])
    config = write_config(run_env, SourceRepoPath=str(source), InputFiles=[], TargetLanguages=["de-DE"])
    assert phase1.main(config) == 0
    assert raw_keys(s3) == ["runs/run-1/raw/a.de-DE.xlf", "runs/run-1/raw/b.de-DE.xlf"]


def test_missing_input_file_fails_but_uploads_the_rest(s3, run_env):
    source = run_env / "src"
    source.mkdir()
    write_resx(source / "a.resx", ["One"])
    config = write_config(run_env, SourceRepoPath=str(source), InputFiles=["a.resx", "missing.resx"],
                          TargetLanguages=["de-DE"])
    assert phase1.main(config) == 1
    assert raw_keys(s3) == ["runs/run-1/raw/a.de-DE.xlf"]


def test_empty_language_list_fails(s3, run_env):
    config = write_config(run_env, SourceRepoPath=str(REPO_ROOT / "source-folder"), TargetLanguages=[])
    assert phase1.main(config) == 1
    assert raw_keys(s3) == []


def test_nothing_to_upload_fails(s3, run_env):
    empty = run_env / "empty"
    empty.mkdir()
    config = write_config(run_env, SourceRepoPath=str(empty), InputFiles=[])
    assert phase1.main(config) == 1
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_phase1.py tests/test_parity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'phase1'` and a missing-file error from the parity test.

- [ ] **Step 6: Implement Phase 1**

`aws/phase1.py`:
```python
import os
import sys
import json
import xml.etree.ElementTree as ET

from translator.s3_store import RAW, RunStore


# Identical to azure/API_based_HFace_AppInsight/phase1.py (enforced by tests/test_parity.py).
def resx_to_xlf(resx_path, lang_code):
    tree = ET.parse(resx_path)
    root = tree.getroot()

    xliff = ET.Element("xliff", version="1.2")
    file_elem = ET.SubElement(xliff, "file", {
        "source-language": "en",
        "target-language": lang_code,
        "datatype": "plaintext",
        "original": os.path.basename(resx_path)
    })
    body = ET.SubElement(file_elem, "body")

    for data in root.findall("data"):
        source_text = data.find("value").text if data.find("value") is not None else ""
        if not source_text:
            continue
        trans_unit = ET.SubElement(body, "trans-unit", id=data.attrib.get("name", ""))
        ET.SubElement(trans_unit, "source").text = source_text
        ET.SubElement(trans_unit, "target").text = ""

    return ET.ElementTree(xliff)


def select_input_files(source_path, input_files):
    if input_files:
        return [os.path.join(source_path, name) for name in input_files]
    found = []
    for root, _, files in os.walk(source_path):
        for file in files:
            if file.endswith(".resx"):
                found.append(os.path.join(root, file))
    return sorted(found)


def main(config_file, store=None):
    with open(config_file, "r") as f:
        config = json.load(f)

    source_path = config.get("SourceRepoPath", "source-folder")
    target_langs = config.get("TargetLanguages", [])
    if not target_langs:
        print("❌ ERROR: TargetLanguages is empty in config.json")
        return 1
    if store is None:
        store = RunStore(os.environ["BUCKET"], os.environ["RUN_ID"])

    failures = 0
    uploaded = 0
    for resx_file in select_input_files(source_path, config.get("InputFiles", [])):
        if not os.path.isfile(resx_file):
            print(f"❌ ERROR: input file not found: {resx_file}")
            failures += 1
            continue
        base = os.path.splitext(os.path.basename(resx_file))[0]
        for lang in target_langs:
            xlf_filename = f"{base}.{lang}.xlf"
            try:
                resx_to_xlf(resx_file, lang).write(xlf_filename, encoding="utf-8", xml_declaration=True)
                store.upload_raw(xlf_filename, xlf_filename)
                uploaded += 1
                print(f"✅ Uploaded {xlf_filename} → s3://{store.bucket}/{store.key(RAW, xlf_filename)}")
            except Exception as e:
                failures += 1
                print(f"⚠️ ERROR processing {resx_file} for {lang}: {e}")
            finally:
                if os.path.exists(xlf_filename):
                    os.remove(xlf_filename)

    if uploaded == 0:
        print("❌ ERROR: no XLIFF files were uploaded")
        return max(failures, 1)
    print(f"🎉 Phase1 complete: {uploaded} XLIFF files uploaded, {failures} failures.")
    return failures


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase1.py config.json")
        sys.exit(2)
    sys.exit(1 if main(sys.argv[1]) else 0)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_phase1.py tests/test_parity.py -q`
Expected: 6 passed.

- [ ] **Step 8: Commit**

```bash
git add aws/config.json aws/phase1.py aws/tests/astutil.py aws/tests/test_parity.py aws/tests/test_phase1.py
git commit -m "feat(aws): add Phase 1 extract-to-S3 with Azure parity check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Phase 3 (merge to localized .resx)

**Files:**
- Create: `aws/phase3.py`, `aws/tests/test_phase3.py`
- Modify: `aws/tests/test_parity.py` (append one test)

**Interfaces:**
- Consumes: `RunStore.list_translated()`, `download_translated()`, `upload_output()` (Task 2); `phase1.resx_to_xlf` (Task 4, test only)
- Produces: `phase3.xlf_to_resx(xlf_path, resx_path, target_lang, target_resx_path) -> str` (identical to Azure); `phase3.main(config_file, store=None) -> int`

- [ ] **Step 1: Append the failing parity test**

Add to the end of `aws/tests/test_parity.py`:
```python


def test_xlf_to_resx_is_identical_to_azure():
    assert function_dump(AWS_DIR / "phase3.py", "xlf_to_resx") == function_dump(AZ_PHASES / "phase3.py", "xlf_to_resx")
```

- [ ] **Step 2: Write the failing Phase 3 tests**

`aws/tests/test_phase3.py`:
```python
import json
import xml.etree.ElementTree as ET

import pytest

import phase1
import phase3
from helpers import BUCKET, write_resx


def output_keys(s3):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="runs/run-1/output/")
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


def values(resx_path):
    return [data.find("value").text for data in ET.parse(resx_path).getroot().findall("data")]


@pytest.fixture
def workspace(s3, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("RUN_ID", "run-1")
    source = tmp_path / "source"
    source.mkdir()
    write_resx(source / "resx_file_10.resx", ["Hello", "Bye"])
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "SourceRepoPath": str(source),
        "TargetResxPath": str(tmp_path / "localized"),
    }), encoding="utf-8")
    return tmp_path, source, str(config)


def upload_translated(s3, tmp_path, source, name, lang, translations):
    tree = phase1.resx_to_xlf(str(source / "resx_file_10.resx"), lang)
    for unit in tree.getroot().iter("trans-unit"):
        text = unit.find("source").text
        if text in translations:
            unit.find("target").text = translations[text]
    local = tmp_path / f"upload-{name}"
    tree.write(str(local), encoding="utf-8", xml_declaration=True)
    s3.upload_file(str(local), BUCKET, f"runs/run-1/translated/{name}")


def test_merges_translations_and_uploads_output(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.es-ES.xlf", "es-ES", {"Hello": "Hola", "Bye": "Adiós"})
    assert phase3.main(config) == 0
    out = tmp_path / "localized" / "es-ES" / "resx_file_10.resx"
    assert values(out) == ["Hola", "Adiós"]
    assert output_keys(s3) == ["runs/run-1/output/es-ES/resx_file_10.resx"]
    assert list(tmp_path.glob("*.xlf")) == []


def test_untranslated_strings_keep_the_source_text(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.de-DE.xlf", "de-DE", {"Hello": "Hallo"})
    assert phase3.main(config) == 0
    assert values(tmp_path / "localized" / "de-DE" / "resx_file_10.resx") == ["Hallo", "Bye"]


def test_no_translated_files_fails(s3, workspace):
    _, _, config = workspace
    assert phase3.main(config) == 1


def test_file_without_matching_source_fails_but_others_merge(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.fr-FR.xlf", "fr-FR", {"Hello": "Bonjour"})
    upload_translated(s3, tmp_path, source, "orphan.fr-FR.xlf", "fr-FR", {})
    assert phase3.main(config) == 1
    assert output_keys(s3) == ["runs/run-1/output/fr-FR/resx_file_10.resx"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_phase3.py tests/test_parity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'phase3'`.

- [ ] **Step 4: Implement Phase 3**

`aws/phase3.py`:
```python
import os
import sys
import json
import xml.etree.ElementTree as ET

from translator.s3_store import RunStore


# Identical to azure/API_based_HFace_AppInsight/phase3.py (enforced by tests/test_parity.py).
def xlf_to_resx(xlf_path, resx_path, target_lang, target_resx_path):
    tree = ET.parse(xlf_path)
    root = tree.getroot()

    orig_resx = ET.parse(resx_path)
    resx_root = orig_resx.getroot()

    translations = {}
    for tu in root.findall(".//trans-unit"):
        source = tu.find("source").text if tu.find("source") is not None else None
        target = tu.find("target").text if tu.find("target") is not None else None
        if source and target:
            translations[source] = target

    for data in resx_root.findall("data"):
        val_elem = data.find("value")
        if val_elem is not None and val_elem.text in translations:
            val_elem.text = translations[val_elem.text]

    lang_dir = os.path.join(target_resx_path, target_lang)
    os.makedirs(lang_dir, exist_ok=True)

    out_file = os.path.join(lang_dir, os.path.basename(resx_path))
    orig_resx.write(out_file, encoding="utf-8", xml_declaration=True)
    return out_file


def main(config_file, store=None):
    with open(config_file, "r") as f:
        config = json.load(f)

    source_path = config.get("SourceRepoPath", "source-folder")
    target_resx_path = config.get("TargetResxPath", "localized")
    if store is None:
        store = RunStore(os.environ["BUCKET"], os.environ["RUN_ID"])

    translated = store.list_translated()
    if not translated:
        print(f"❌ ERROR: no translated XLF files in s3://{store.bucket}/{store.prefix('translated')}")
        return 1

    failures = 0
    for item in translated:
        parts = item.name.split(".")
        target_lang = parts[-2] if len(parts) >= 3 else "unknown"
        base_resx = os.path.join(source_path, parts[0] + ".resx")
        if len(parts) < 3 or not os.path.exists(base_resx):
            print(f"❌ ERROR: cannot map {item.name} to a source .resx ({base_resx})")
            failures += 1
            continue

        local_xlf = item.name
        try:
            store.download_translated(item.name, local_xlf)
            out_file = xlf_to_resx(local_xlf, base_resx, target_lang, target_resx_path)
            store.upload_output(out_file, f"{target_lang}/{os.path.basename(out_file)}")
            print(f"✅ Generated localized resx: {out_file}")
        except Exception as e:
            failures += 1
            print(f"❌ ERROR converting {item.name}: {e}")
        finally:
            if os.path.exists(local_xlf):
                os.remove(local_xlf)

    print(f"🎉 Phase3 complete: {len(translated) - failures} localized files, {failures} failures.")
    return failures


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase3.py config.json")
        sys.exit(2)
    sys.exit(1 if main(sys.argv[1]) else 0)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_phase3.py tests/test_parity.py -q`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add aws/phase3.py aws/tests/test_phase3.py aws/tests/test_parity.py
git commit -m "feat(aws): add Phase 3 merge with S3 output and Azure parity check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Phase 2 translator (Docker image + `translation.py`)

**Files:**
- Create (copied from Azure, then edited): `aws/translator/Dockerfile`, `aws/translator/requirements.txt`, `aws/translator/translation.py`
- Create: `aws/tests/test_translation_main.py`
- Modify: `aws/tests/test_parity.py` (append tests)

**Interfaces:**
- Consumes: `RunStore` (Task 2), `aws_runtime.sql_conn_str()`, `aws_runtime.extra_fields()` (Task 3)
- Produces: `translation.main(config_path)` (coroutine) returning `int` failure count; the `__main__` block exits `1` on failures and `0` otherwise. Environment variables: `BUCKET`, `RUN_ID`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, optional `DB_NAME`.

- [ ] **Step 1: Append the failing parity tests**

Add to the end of `aws/tests/test_parity.py`:
```python


AZ_TRANSLATION = AZ_DOCKER / "translation.py"
AWS_TRANSLATION = AWS_TRANSLATOR / "translation.py"
AZURE_ONLY_IMPORTS = {"from azure.storage.blob import BlobServiceClient"}
AWS_ONLY_IMPORTS = {"import aws_runtime", "from s3_store import RunStore"}


def _dumps(statements):
    import ast
    return {ast.dump(ast.parse(s).body[0]) for s in statements}


def test_translation_shared_code_is_identical():
    assert shared_top_level(AWS_TRANSLATION) == shared_top_level(AZ_TRANSLATION)


def test_translation_imports_differ_only_by_storage_and_runtime():
    azure, aws = import_dumps(AZ_TRANSLATION), import_dumps(AWS_TRANSLATION)
    assert azure - aws == _dumps(AZURE_ONLY_IMPORTS)
    assert aws - azure == _dumps(AWS_ONLY_IMPORTS)


def test_metrics_payload_and_its_inputs_match_azure():
    assert assigned_value_dump(AWS_TRANSLATION, "main", "metrics_payload") == \
        assigned_value_dump(AZ_TRANSLATION, "main", "metrics_payload")
    assert statement_slice(AWS_TRANSLATION, "main", "file_duration", "metrics_payload") == \
        statement_slice(AZ_TRANSLATION, "main", "file_duration", "metrics_payload")


def _requirement_lines(path):
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def test_requirements_are_azure_plus_boto3():
    # boto3 is the only addition: s3_store.py needs it inside the container.
    assert _requirement_lines(AWS_TRANSLATOR / "requirements.txt") == \
        _requirement_lines(AZ_DOCKER / "requirements.txt") | {"boto3==1.35.36"}


def test_dockerfile_differs_only_in_base_image():
    azure = (AZ_DOCKER / "Dockerfile").read_text(encoding="utf-8").splitlines()
    aws = (AWS_TRANSLATOR / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert len(azure) == len(aws)
    differing = [(a, b) for a, b in zip(azure, aws) if a != b]
    assert differing == [("FROM python:3.10-slim", "FROM public.ecr.aws/docker/library/python:3.10-slim")]
```

- [ ] **Step 2: Write the failing behaviour tests for `main()`**

`aws/tests/test_translation_main.py`:
```python
import asyncio
import json

import pytest

from helpers import AWS_DIR, BUCKET, forbidden_connect, load_module

XLF = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    '<xliff version="1.2"><file source-language="en" target-language="es-ES" datatype="plaintext" '
    'original="resx_file_10.resx"><body><trans-unit id="S0"><source>Hello</source><target /></trans-unit>'
    "</body></file></xliff>"
)


class FakeTranslator:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class FakeAutoTokenizer:
    @staticmethod
    def from_pretrained(name):
        return object()


class FakeConn:
    closed = False

    def close(self):
        self.closed = True


async def _no_sleep(_seconds):
    return None


@pytest.fixture
def translation(stub_modules, monkeypatch):
    stub_modules(
        ctranslate2={"Translator": FakeTranslator},
        transformers={"AutoTokenizer": FakeAutoTokenizer},
        pyodbc={"connect": forbidden_connect},
    )
    module = load_module("aws_translation_under_test", AWS_DIR / "translator" / "translation.py")
    monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)          # skip the 2 s telemetry pause
    monkeypatch.setattr(module.logging, "shutdown", lambda: None)    # keep pytest's log handlers open
    return module


@pytest.fixture
def run_env(s3, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in {"BUCKET": BUCKET, "RUN_ID": "run-1", "DB_HOST": "db.example",
                       "DB_USER": "dbadmin", "DB_PASSWORD": "secret"}.items():
        monkeypatch.setenv(key, value)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    return str(config)


def put_raw(s3, name):
    s3.put_object(Bucket=BUCKET, Key=f"runs/run-1/raw/{name}", Body=XLF.encode("utf-8"))


def keys(s3, stage):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"runs/run-1/{stage}/")
    return sorted(obj["Key"].rsplit("/", 1)[1] for obj in resp.get("Contents", []))


def fake_processor(fail_for=()):
    async def process(local_path, target_lang, conn, exporter):
        if target_lang in fail_for:
            raise RuntimeError("inference failed")
        stats = {"total_api_time_ms": 1.0, "total_sql_time_ms": 1.0, "total_commit_time_ms": 1.0}
        return stats, 0.1, 0.1, 5, 5
    return process


def research_payloads(output):
    return [json.loads(line.split("RESEARCH_DATA: ", 1)[1])
            for line in output.splitlines() if "RESEARCH_DATA: " in line]


def run(translation, config):
    return asyncio.run(translation.main(config))


def test_success_uploads_translation_deletes_raw_and_emits_aws_fields(translation, run_env, s3, monkeypatch, capsys):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    conn = FakeConn()
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (conn, 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor())

    assert run(translation, run_env) == 0
    assert keys(s3, "raw") == []
    assert keys(s3, "translated") == ["resx_file_10.es-ES.xlf"]
    assert conn.closed
    [payload] = research_payloads(capsys.readouterr().out)
    assert payload["file_name"] == "resx_file_10.es-ES.xlf"
    assert payload["target_lang"] == "es-ES"
    assert payload["cloud_provider"] == "aws"
    assert payload["run_id"] == "run-1"
    assert payload["region"] == "ap-south-1"
    assert payload["cpu_limit"] >= 1


def test_failed_file_keeps_its_raw_input_and_reports_failure(translation, run_env, s3, monkeypatch):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(fail_for={"fr-FR"}))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.fr-FR.xlf"]
    assert keys(s3, "translated") == ["resx_file_10.es-ES.xlf"]


def test_retry_after_partial_failure_only_redoes_failed_file(translation, run_env, s3, monkeypatch):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(fail_for={"fr-FR"}))
    assert run(translation, run_env) == 1

    seen = []
    succeed = fake_processor()

    async def record(local_path, target_lang, conn, exporter):
        seen.append(target_lang)
        return await succeed(local_path, target_lang, conn, exporter)

    monkeypatch.setattr(translation, "process_xlf_file", record)
    assert run(translation, run_env) == 0
    assert seen == ["fr-FR"]
    assert keys(s3, "raw") == []
    assert keys(s3, "translated") == ["resx_file_10.es-ES.xlf", "resx_file_10.fr-FR.xlf"]


def test_database_unreachable_fails_before_touching_files(translation, run_env, s3, monkeypatch):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (None, 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor())

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.es-ES.xlf"]
    assert keys(s3, "translated") == []


def test_missing_bucket_setting_fails(translation, run_env, monkeypatch):
    monkeypatch.delenv("BUCKET")
    assert run(translation, run_env) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_translation_main.py tests/test_parity.py -q`
Expected: FAIL (files under `aws/translator/` do not exist).

- [ ] **Step 4: Copy the Azure image files and switch the base image**

```bash
cp azure/Docker-Local-Inference/Dockerfile aws/translator/Dockerfile
cp azure/Docker-Local-Inference/requirements.txt aws/translator/requirements.txt
cp azure/Docker-Local-Inference/translation.py aws/translator/translation.py
```

In `aws/translator/Dockerfile`, replace `FROM python:3.10-slim` with `FROM public.ecr.aws/docker/library/python:3.10-slim`. Change nothing else; the parity test checks it.

Add boto3 to the image requirements (the Azure file has no trailing newline, hence the leading `\n`):
```bash
printf '\n\n# --- AWS storage (S3) ---\nboto3==1.35.36\n' >> aws/translator/requirements.txt
```

- [ ] **Step 5: Swap the storage import and cut off the Azure `main()`**

```bash
python3 - <<'EOF'
from pathlib import Path

path = Path("aws/translator/translation.py")
text = path.read_text(encoding="utf-8")
old_import = "from azure.storage.blob import BlobServiceClient\n"
assert text.count(old_import) == 1
text = text.replace(old_import, "import aws_runtime\nfrom s3_store import RunStore\n")
head, found, _ = text.partition("async def main(config_path):")
assert found, "async def main(config_path): not found"
path.write_text(head, encoding="utf-8")
print("kept", len(head.splitlines()), "lines before main()")
EOF
```
Expected: `kept 422 lines before main()`.

- [ ] **Step 6: Append the AWS `main()`**

```bash
cat >> aws/translator/translation.py <<'EOF'
async def main(config_path):
    profiler.start_timer("total_execution")
    if not os.path.exists(config_path): 
        print("❌ Config file not found!")
        return 1

    with open(config_path, 'r') as f: config = json.load(f)
    logger, az_handler = setup_logging(config)
    failures = 0
    
    try:
        store = RunStore(os.environ['BUCKET'], os.environ['RUN_ID'])
        raw_container_name = store.raw_prefix
        
        profiler.start_timer("cold_start")
        conn, _ = get_sql_connection(aws_runtime.sql_conn_str())
        cold_start_ms = profiler.stop_timer("cold_start")
        if not conn: 
            print("❌ Exiting due to SQL Connection Failure.")
            return 1
    except Exception as e:
        print(f"❌ Init Error: {e}")
        return 1

    try:
        exporter = metrics_exporter.new_metrics_exporter(connection_string=config['app_insights_connection_string'])
        register_metrics_views(exporter)
    except: exporter = None

    try:
        blobs = store.list_raw()
        
        if not blobs:
            logger.warning(f"⚠️ ZERO files found in '{raw_container_name}'. Are you sure Phase 1 ran?")
        else:
            logger.info(f"🔎 Found {len(blobs)} files in '{raw_container_name}'.")

        for blob in blobs:
            if blob.name.lower().endswith('.xlf'):
                logger.info(f"⬇️ Downloading {blob.name}...")
                
                profiler.start_timer("blob_download")
                local_safe_name = os.path.basename(blob.name)
                store.download_raw(blob.name, local_safe_name)
                download_ms = profiler.stop_timer("blob_download")

                parts = blob.name.split('.')
                if len(parts) >= 3:
                    target_lang = parts[-2]
                else:
                    logger.warning(f"⚠️ Could not deduce language from filename {blob.name}. Skipping.")
                    failures += 1
                    if os.path.exists(local_safe_name): os.remove(local_safe_name)
                    continue

                succeeded = False
                try:
                    profiler.reset_file_metrics() 
                    profiler.start_timer(f"file_{local_safe_name}_{target_lang}")
                    
                    logger.info(f"Translating {local_safe_name} to {target_lang}...")
                    
                    file_stats, xml_parse_ms, xml_write_ms, total_source_chars, total_translated_chars = await process_xlf_file(local_safe_name, target_lang, conn, exporter)
                    
                    logger.info(f"⬆️ Uploading processed {blob.name} to translated folder...")
                    profiler.start_timer("blob_upload")
                    store.upload_translated(local_safe_name, blob.name)
                    upload_ms = profiler.stop_timer("blob_upload")
                    
                    file_duration = profiler.stop_timer(f"file_{local_safe_name}_{target_lang}")
                    summary = profiler.get_summary_stats()
                    
                    total_tokens = profiler.counters["tokens_processed"]
                    hf_time = file_stats["total_api_time_ms"]
                    ms_per_token = round(hf_time / max(1, total_tokens), 2) if total_tokens else 0.0
                    avg_batch_size = round(total_tokens / max(1, profiler.counters["total_batches"]), 2) if profiler.counters["total_batches"] else 0.0
                    avg_segment_len = round(total_source_chars / max(1, summary["total_segments"]), 2)
                    
                    total_chars = total_source_chars + total_translated_chars
                    char_token_ratio = round(total_chars / max(1, total_tokens), 2) if total_tokens else 0.0
                    
                    sleep_time_ms = profiler.counters.get("sleep_time_ms", 0.0)
                    total_input_t = profiler.counters["total_input_tokens"]
                    total_output_t = profiler.counters["total_output_tokens"]
                    batches = max(1, profiler.counters["total_batches"])
                    elapsed_s = max(1, file_duration / 1000)
                    total_network_wait_ms = file_stats["total_sql_time_ms"] + file_stats["total_commit_time_ms"] + download_ms + upload_ms
                    compute_time_ms = max(0, file_duration - total_network_wait_ms - sleep_time_ms)
                    io_bound_ratio = round((total_network_wait_ms / max(1, file_duration)) * 100, 2)
                    
                    sys_stats = profiler.get_system_impact()
                    
                    metrics_payload = {
                        "run_invocation_id": str(uuid.uuid4()), 
                        "file_name": blob.name,
                        "target_lang": target_lang,
                        "throughput_seg_per_hr": summary['overall_throughput_seg_per_hr'],
                        "throughput_input_tokens_per_hr": round((total_input_t / elapsed_s) * 3600, 2),
                        "total_api_call_logically": profiler.counters["total_batches"], 
                        "actual_api_call": profiler.counters["api_calls"],    
                        "total_segments": summary["total_segments"],
                        "avg_segment_char_length": avg_segment_len,
                        "text_expansion_ratio": round(total_translated_chars / max(1, total_source_chars), 2),
                        "tokens_processed_in_whole_language": total_tokens,
                        "total_input_tokens_source": round(total_input_t, 2),
                        "total_output_tokens_target": round(total_output_t, 2),
                        "avg_input_tokens_per_batch": round(total_input_t / batches, 2),
                        "avg_output_tokens_per_batch": round(total_output_t / batches, 2),
                        "char_per_token_ratio": char_token_ratio,   
                        "total_token_input_plus_output_per_batch": avg_batch_size,
                        "time_per_token": ms_per_token,
                        "nmt_calls": summary["total_nmt_calls"],
                        "transational_memory_hits": summary["total_tm_hits"],
                        "cache_rate": (profiler.counters["cache_hits"] / max(1, profiler.counters["cache_hits"] + profiler.counters["cache_misses"])) * 100,
                        "total_time": file_duration,
                        "cold_start_init_ms": cold_start_ms,
                        "compute_time_ms": compute_time_ms,
                        "total_sleep_time_ms": sleep_time_ms, 
                        "network_wait_time_ms": total_network_wait_ms,
                        "io_bound_pipeline_pct": io_bound_ratio,
                        "hf_api_wait_ms": hf_time,
                        "azure_sql_query_ms": file_stats["total_sql_time_ms"],
                        "azure_sql_commit_ms": file_stats["total_commit_time_ms"],
                        "xml_parse_ms": xml_parse_ms,
                        "xml_disk_write_ms": xml_write_ms,
                        "blob_download_ms": download_ms, 
                        "blob_upload_ms": upload_ms,
                        "telemetry_overhead_ms": profiler.counters["telemetry_overhead_ms"],
                        "cpu_usage_pct": sys_stats['cpu_percent'],
                        "memory_mb": sys_stats['memory_mb'],
                        "serverless_cost_gb_sec": round((sys_stats['memory_mb'] / 1024) * (file_duration / 1000), 4),
                        "api_retries": profiler.counters["api_retries"],
                        "api_failures": profiler.counters["api_failures"],
                        "api_throttled_429": profiler.counters.get("api_throttled", 0)
                    }
                    metrics_payload.update(aws_runtime.extra_fields())
                    
                    logger.info(f"📊 RESEARCH_DATA: {json.dumps(metrics_payload)}")
                    if az_handler:
                        try:
                            az_handler.flush()
                        except Exception as e:
                            pass
                    s_start = time.perf_counter()
                    await asyncio.sleep(2)
                    profiler.counters["sleep_time_ms"] += (time.perf_counter() - s_start) * 1000
                    succeeded = True

                except Exception as loop_e:
                    failures += 1
                    logger.error(f"❌ Error processing {target_lang} for {blob.name}: {loop_e}")
            
                # Unlike Azure, keep the raw file when anything failed so a stage retry can redo it.
                if succeeded:
                    store.delete_raw(blob.name)
                if os.path.exists(local_safe_name): os.remove(local_safe_name)
                
                gc.collect()

    except Exception as e:
        failures += 1
        logger.error(f"❌ Pipeline Failure: {e}")

    if conn: conn.close()
    
    print("⏳ Finalizing telemetry...")
    logging.shutdown()
    return failures

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python translation.py config.json")
        sys.exit(2)
    sys.exit(1 if asyncio.run(main(sys.argv[1])) else 0)
EOF
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_translation_main.py tests/test_parity.py -q`
Expected: 12 passed. If `test_translation_shared_code_is_identical` fails, diff the two files (`diff azure/Docker-Local-Inference/translation.py aws/translator/translation.py`); the only differences allowed are the import swap and `main()` / `__main__`.

- [ ] **Step 8: Commit**

```bash
git add aws/translator/Dockerfile aws/translator/requirements.txt aws/translator/translation.py aws/tests/test_translation_main.py aws/tests/test_parity.py
git commit -m "feat(aws): add Phase 2 translator (Azure logic, S3 storage, retry-safe raw handling)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Database setup command (`db_admin.py`)

**Files:**
- Create: `aws/translator/db_admin.py`, `aws/tests/test_db_admin.py`

**Interfaces:**
- Consumes: `aws_runtime.db_name()`, `aws_runtime.sql_conn_str(database)` (Task 3)
- Produces: `db_admin.init(connect=None)`, `db_admin.reset(connect=None)`, `db_admin.main(argv) -> int` (`0` ok, `2` usage error). CLI: `python db_admin.py init|reset`.

- [ ] **Step 1: Write the failing tests**

`aws/tests/test_db_admin.py`:
```python
import pytest

from helpers import AWS_DIR, forbidden_connect, load_module


class RecordingConnect:
    def __init__(self):
        self.calls = []

    def __call__(self, conn_str, autocommit=False):
        recorder = self

        class Conn:
            def execute(self, sql):
                recorder.calls.append((conn_str, autocommit, sql))

            def close(self):
                pass

        return Conn()


@pytest.fixture
def db_admin(stub_modules, monkeypatch):
    stub_modules(pyodbc={"connect": forbidden_connect})
    monkeypatch.setenv("DB_HOST", "db.example")
    monkeypatch.setenv("DB_USER", "dbadmin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.delenv("DB_NAME", raising=False)
    return load_module("db_admin_under_test", AWS_DIR / "translator" / "db_admin.py")


def test_init_creates_database_on_master_then_table(db_admin):
    connect = RecordingConnect()
    db_admin.init(connect)
    (first_conn, first_auto, first_sql), (second_conn, second_auto, second_sql) = connect.calls
    assert "DATABASE=master;" in first_conn and first_auto is True
    assert first_sql == "IF DB_ID(N'localization') IS NULL CREATE DATABASE [localization];"
    assert "DATABASE=localization;" in second_conn and second_auto is True
    assert "CREATE TABLE dbo.Translations" in second_sql
    assert "PRIMARY KEY (SourceText, TargetLang)" in second_sql
    assert "IF OBJECT_ID(N'dbo.Translations', N'U') IS NULL" in second_sql


def test_table_has_every_column_the_merge_writes(db_admin):
    for column in ("SourceText", "TargetLang", "TranslatedText", "ModelName", "LastUpdated"):
        assert column in db_admin.CREATE_TABLE_SQL


def test_reset_empties_the_table(db_admin):
    connect = RecordingConnect()
    db_admin.reset(connect)
    assert connect.calls == [(connect.calls[0][0], True, "DELETE FROM dbo.Translations;")]
    assert "DATABASE=localization;" in connect.calls[0][0]


def test_unsafe_database_name_is_rejected_before_connecting(db_admin, monkeypatch):
    monkeypatch.setenv("DB_NAME", "x];DROP DATABASE y;--")
    connect = RecordingConnect()
    with pytest.raises(ValueError):
        db_admin.init(connect)
    assert connect.calls == []


def test_cli_usage(db_admin):
    assert db_admin.main(["db_admin.py"]) == 2
    assert db_admin.main(["db_admin.py", "drop"]) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_db_admin.py -q`
Expected: FAIL with `FileNotFoundError` for `db_admin.py`.

- [ ] **Step 3: Implement**

`aws/translator/db_admin.py`:
```python
"""Translation-memory database setup for the AWS pipeline.

    python db_admin.py init    create the database and dbo.Translations if missing
    python db_admin.py reset   delete every row (start a run at a 0% cache rate)
"""
import sys
from contextlib import closing

import pyodbc

import aws_runtime

CREATE_DATABASE_SQL = "IF DB_ID(N'{name}') IS NULL CREATE DATABASE [{name}];"
CREATE_TABLE_SQL = """IF OBJECT_ID(N'dbo.Translations', N'U') IS NULL
CREATE TABLE dbo.Translations (
    SourceText     NVARCHAR(450) NOT NULL,
    TargetLang     NVARCHAR(16)  NOT NULL,
    TranslatedText NVARCHAR(MAX) NULL,
    ModelName      NVARCHAR(100) NULL,
    LastUpdated    DATETIME2     NULL,
    CONSTRAINT PK_Translations PRIMARY KEY (SourceText, TargetLang)
);"""
RESET_SQL = "DELETE FROM dbo.Translations;"


def _execute(database, sql, connect):
    # autocommit: CREATE DATABASE cannot run inside a transaction.
    with closing(connect(aws_runtime.sql_conn_str(database), autocommit=True)) as conn:
        conn.execute(sql)


def init(connect=None):
    connect = connect or pyodbc.connect
    name = aws_runtime.db_name()
    _execute("master", CREATE_DATABASE_SQL.format(name=name), connect)
    _execute(name, CREATE_TABLE_SQL, connect)
    print(f"✅ Database '{name}' and table dbo.Translations are ready.")


def reset(connect=None):
    connect = connect or pyodbc.connect
    name = aws_runtime.db_name()
    _execute(name, RESET_SQL, connect)
    print(f"🧹 Emptied dbo.Translations in '{name}' (run starts at 0% cache).")


COMMANDS = {"init": init, "reset": reset}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print("Usage: python db_admin.py init|reset")
        return 2
    COMMANDS[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_db_admin.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add aws/translator/db_admin.py aws/tests/test_db_admin.py
git commit -m "feat(aws): add translation-memory database setup command" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Model cache in S3 (`model_cache.py`)

**Files:**
- Create: `aws/model_cache.py`, `aws/tests/test_model_cache.py`

**Interfaces:**
- Produces: `model_cache.MARKER = "_COMPLETE"`, `model_cache.EXIT_MISSING = 3`, `cache_prefix(model_key) -> str`, `is_complete(client, bucket, model_key) -> bool`, `fetch(client, bucket, model_key, dest) -> bool`, `publish(client, bucket, model_key, src)`, `invalidate(client, bucket, model_key)`, `main(argv=None) -> int`. CLI: `python aws/model_cache.py fetch|publish|invalidate [--dir DIR] [--bucket B] [--model-key K]` (bucket and key default to `BUCKET` and `MODEL_KEY`).

- [ ] **Step 1: Write the failing tests**

`aws/tests/test_model_cache.py`:
```python
import pytest

import model_cache
from helpers import BUCKET

KEY = "nllb-200-distilled-600M_int8_ct2-4.7.1"
PREFIX = f"models/{KEY}/"


class RecordingClient:
    """Wraps a boto3 client and records which keys were written, in order."""

    def __init__(self, client):
        self._client = client
        self.writes = []

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name == "upload_file":
            def upload_file(path, bucket, key, *args, **kwargs):
                self.writes.append(key)
                return attr(path, bucket, key, *args, **kwargs)
            return upload_file
        if name == "put_object":
            def put_object(**kwargs):
                self.writes.append(kwargs["Key"])
                return attr(**kwargs)
            return put_object
        return attr


def make_model(directory):
    (directory / "nested").mkdir(parents=True)
    (directory / "model.bin").write_bytes(b"weights")
    (directory / "config.json").write_text("{}", encoding="utf-8")
    (directory / "nested" / "extra.txt").write_text("x", encoding="utf-8")


def test_fetch_on_empty_cache_returns_false(s3, tmp_path):
    assert model_cache.fetch(s3, BUCKET, KEY, str(tmp_path / "out")) is False


def test_publish_then_fetch_restores_every_file(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    model_cache.publish(s3, BUCKET, KEY, str(src))
    dest = tmp_path / "dest"
    assert model_cache.fetch(s3, BUCKET, KEY, str(dest)) is True
    assert (dest / "model.bin").read_bytes() == b"weights"
    assert (dest / "nested" / "extra.txt").read_text(encoding="utf-8") == "x"
    assert not (dest / model_cache.MARKER).exists()


def test_partial_upload_without_marker_is_not_fetched(s3, tmp_path):
    s3.put_object(Bucket=BUCKET, Key=PREFIX + "model.bin", Body=b"half")
    dest = tmp_path / "dest"
    assert model_cache.fetch(s3, BUCKET, KEY, str(dest)) is False
    assert not dest.exists()


def test_publish_writes_the_marker_last(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    client = RecordingClient(s3)
    model_cache.publish(client, BUCKET, KEY, str(src))
    assert client.writes[-1] == PREFIX + model_cache.MARKER
    assert len(client.writes) == 4


def test_publish_of_empty_directory_is_refused(s3, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        model_cache.publish(s3, BUCKET, KEY, str(tmp_path / "empty"))


def test_invalidate_makes_the_cache_incomplete(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    model_cache.publish(s3, BUCKET, KEY, str(src))
    model_cache.invalidate(s3, BUCKET, KEY)
    assert model_cache.is_complete(s3, BUCKET, KEY) is False


def test_cli_fetch_reports_missing_with_exit_code_3(s3, tmp_path, monkeypatch):
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("MODEL_KEY", KEY)
    assert model_cache.main(["fetch", "--dir", str(tmp_path / "dest")]) == model_cache.EXIT_MISSING


@pytest.mark.parametrize("bad", ["", "a/b"])
def test_unsafe_model_keys_are_rejected(bad):
    with pytest.raises(ValueError):
        model_cache.cache_prefix(bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_model_cache.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_cache'`.

- [ ] **Step 3: Implement**

`aws/model_cache.py`:
```python
"""Keep the converted CTranslate2 model in S3 so later Phase 2 runs skip conversion.

A cached model counts as complete only when its _COMPLETE marker exists. The
marker is written last, so an interrupted upload is never mistaken for a
usable model.
"""
import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

MARKER = "_COMPLETE"
EXIT_MISSING = 3


def cache_prefix(model_key):
    if not model_key or "/" in model_key:
        raise ValueError(f"invalid model key: {model_key!r}")
    return f"models/{model_key}/"


def is_complete(client, bucket, model_key):
    try:
        client.head_object(Bucket=bucket, Key=cache_prefix(model_key) + MARKER)
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def fetch(client, bucket, model_key, dest):
    if not is_complete(client, bucket, model_key):
        return False
    prefix = cache_prefix(model_key)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            relative = obj["Key"][len(prefix):]
            if not relative or relative == MARKER:
                continue
            local_path = os.path.join(dest, *relative.split("/"))
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            client.download_file(bucket, obj["Key"], local_path)
    return True


def invalidate(client, bucket, model_key):
    client.delete_object(Bucket=bucket, Key=cache_prefix(model_key) + MARKER)


def publish(client, bucket, model_key, src):
    files = []
    for root, _, names in os.walk(src):
        for name in names:
            files.append(os.path.join(root, name))
    if not files:
        raise ValueError(f"no model files to publish in {src}")
    prefix = cache_prefix(model_key)
    invalidate(client, bucket, model_key)
    for path in sorted(files):
        relative = os.path.relpath(path, src).replace(os.sep, "/")
        client.upload_file(path, bucket, prefix + relative)
    client.put_object(Bucket=bucket, Key=prefix + MARKER, Body=b"")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch, publish or invalidate the cached model in S3.")
    parser.add_argument("command", choices=["fetch", "publish", "invalidate"])
    parser.add_argument("--dir", help="Local model directory (fetch destination or publish source).")
    parser.add_argument("--bucket", default=os.environ.get("BUCKET"))
    parser.add_argument("--model-key", default=os.environ.get("MODEL_KEY"))
    args = parser.parse_args(argv)
    if not args.bucket or not args.model_key:
        parser.error("--bucket (or BUCKET) and --model-key (or MODEL_KEY) are required")
    if args.command in ("fetch", "publish") and not args.dir:
        parser.error(f"{args.command} needs --dir")

    client = boto3.client("s3")
    if args.command == "fetch":
        if fetch(client, args.bucket, args.model_key, args.dir):
            print(f"✅ Restored cached model {args.model_key} into {args.dir}")
            return 0
        print(f"ℹ️ No complete cached model for {args.model_key}")
        return EXIT_MISSING
    if args.command == "publish":
        publish(client, args.bucket, args.model_key, args.dir)
        print(f"✅ Published model to s3://{args.bucket}/{cache_prefix(args.model_key)}")
        return 0
    invalidate(client, args.bucket, args.model_key)
    print(f"🧹 Invalidated cached model {args.model_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_model_cache.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add aws/model_cache.py aws/tests/test_model_cache.py
git commit -m "feat(aws): cache the converted NLLB model in S3 with a completion marker" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: CodeBuild buildspecs and the Phase 2 runner script

**Files:**
- Create: `aws/buildspecs/phase1.yml`, `aws/buildspecs/phase2.yml`, `aws/buildspecs/phase3.yml`, `aws/scripts/run_phase2.sh`, `aws/tests/test_buildspecs.py`

**Interfaces:**
- Consumes: `aws/phase1.py`, `aws/phase3.py`, `aws/config.json`, `aws/model_cache.py` (CLI, exit code 3 = missing), `aws/translator/` image with `db_admin.py init|reset` and `translation.py config.json`
- Produces: buildspec paths referenced by the CloudFormation template (Task 10). Environment contract for Phase 2: `BUCKET`, `RUN_ID`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `PHASE2_CPUSET`, `RESET_TM`, `MODEL_KEY`.

- [ ] **Step 1: Write the failing tests**

`aws/tests/test_buildspecs.py`:
```python
import json
import re
import subprocess

import yaml

from helpers import AWS_DIR, REPO_ROOT

BUILDSPECS = AWS_DIR / "buildspecs"
RUNNER = AWS_DIR / "scripts" / "run_phase2.sh"


def load(name):
    return yaml.safe_load((BUILDSPECS / name).read_text(encoding="utf-8"))


def pinned(package):
    requirements = (AWS_DIR / "translator" / "requirements.txt").read_text(encoding="utf-8")
    return re.search(rf"^{package}==([\w.]+)", requirements, re.M).group(1)


def test_buildspecs_use_bash():
    for name in ("phase1.yml", "phase2.yml", "phase3.yml"):
        spec = load(name)
        assert spec["version"] == 0.2
        assert spec["env"]["shell"] == "bash"


def test_files_referenced_by_buildspecs_exist():
    for name in ("phase1.yml", "phase2.yml", "phase3.yml"):
        text = (BUILDSPECS / name).read_text(encoding="utf-8")
        paths = re.findall(r"(aws/[\w/.-]+\.(?:py|sh|json))", text)
        assert paths, name
        for path in paths:
            assert (REPO_ROOT / path).is_file(), f"{name}: {path}"


def test_phase3_artifact_matches_configured_output_folder():
    config = json.loads((AWS_DIR / "config.json").read_text(encoding="utf-8"))
    assert load("phase3.yml")["artifacts"]["files"] == [f"{config['TargetResxPath']}/**/*"]


def test_model_key_and_converter_pins_match_the_image():
    ct2, transformers = pinned("ctranslate2"), pinned("transformers")
    assert load("phase2.yml")["env"]["variables"]["MODEL_KEY"] == f"nllb-200-distilled-600M_int8_ct2-{ct2}"
    script = RUNNER.read_text(encoding="utf-8")
    assert f'"ctranslate2=={ct2}"' in script
    assert f'"transformers=={transformers}"' in script


def test_translation_runs_on_pinned_cores_with_exit_code_kept():
    script = RUNNER.read_text(encoding="utf-8")
    assert 'PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"' in script
    assert '--cpuset-cpus="$PHASE2_CPUSET"' in script
    assert "set -euo pipefail" in script


def test_shell_scripts_parse():
    scripts = sorted(p for p in AWS_DIR.rglob("*.sh") if ".venv" not in p.parts)
    assert scripts
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_buildspecs.py -q`
Expected: FAIL (buildspec files missing).

- [ ] **Step 3: Write the buildspecs**

`aws/buildspecs/phase1.yml`:
```yaml
version: 0.2

env:
  shell: bash

phases:
  install:
    runtime-versions:
      python: 3.11
    commands:
      - pip install --quiet "boto3>=1.34,<2"
  build:
    commands:
      - echo "Run $RUN_ID → s3://$BUCKET/runs/$RUN_ID/raw/"
      - python aws/phase1.py aws/config.json
```

`aws/buildspecs/phase2.yml`:
```yaml
version: 0.2

env:
  shell: bash
  variables:
    MODEL_KEY: "nllb-200-distilled-600M_int8_ct2-4.7.1"

phases:
  install:
    runtime-versions:
      python: 3.11
    commands:
      - pip install --quiet "boto3>=1.34,<2"
  build:
    commands:
      - bash aws/scripts/run_phase2.sh
```

`aws/buildspecs/phase3.yml`:
```yaml
version: 0.2

env:
  shell: bash

phases:
  install:
    runtime-versions:
      python: 3.11
    commands:
      - pip install --quiet "boto3>=1.34,<2"
  build:
    commands:
      - python aws/phase3.py aws/config.json

artifacts:
  files:
    - "localized/**/*"
```

- [ ] **Step 4: Write the Phase 2 runner**

`aws/scripts/run_phase2.sh`:
```bash
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
BASE_IMAGE="public.ecr.aws/docker/library/python:3.10-slim"
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_buildspecs.py -q`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add aws/buildspecs aws/scripts/run_phase2.sh aws/tests/test_buildspecs.py
git commit -m "feat(aws): add CodeBuild buildspecs and Phase 2 runner" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: CloudFormation stack, firewall sync, deploy and teardown scripts

**Files:**
- Create: `aws/infra/pipeline.yaml`, `aws/infra/sync_prefix_list.py`, `aws/infra/deploy.sh`, `aws/infra/teardown.sh`, `aws/tests/test_pipeline_template.py`, `aws/tests/test_sync_prefix_list.py`

**Interfaces:**
- Consumes: buildspec paths `aws/buildspecs/phase{1,2,3}.yml` (Task 9)
- Produces:
  - Stack outputs: `PipelineName`, `DataBucketName`, `ArtifactBucketName`, `GitHubConnectionArn`, `DbEndpoint`, `DbSecretArn`, `DbPrefixListId`, `LogGroupName`
  - CodeBuild environment: `BUCKET` (all phases); `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `PHASE2_CPUSET` (Phase 2); `RUN_ID` (all phases, from the pipeline); `RESET_TM` (Phase 2, from the pipeline)
  - `sync_prefix_list.codebuild_cidrs(ip_ranges, region) -> list[str]`, `normalize_cidr(value) -> str`, `plan_changes(current, desired) -> (add, remove)`, `sync(client, prefix_list_id, desired, poll_seconds=2, max_polls=60) -> (add, remove)`, `main(argv=None) -> int`
  - Defaults: stack `cloud-loc`, project `cloud-loc`, region `ap-south-1`, log group `/cloud-loc/codebuild`, pipeline `cloud-loc-pipeline`

- [ ] **Step 1: Write the failing template tests**

`aws/tests/test_pipeline_template.py`:
```python
import json
import subprocess
import sys
from pathlib import Path

import yaml

from helpers import AWS_DIR, REPO_ROOT

TEMPLATE = AWS_DIR / "infra" / "pipeline.yaml"
CFN_LINT = Path(sys.executable).parent / "cfn-lint"


class CfnLoader(yaml.SafeLoader):
    """Reads CloudFormation short-form tags (!Ref, !Sub, ...) as {"Ref": value} etc."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {suffix: value}


CfnLoader.add_multi_constructor("!", _tag)


def template():
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=CfnLoader)


def resources():
    return template()["Resources"]


def env_vars(project_name):
    return {v["Name"]: v for v in resources()[project_name]["Properties"]["Environment"]["EnvironmentVariables"]}


def action_env(stage):
    return {v["name"]: v["value"] for v in json.loads(stage["Actions"][0]["Configuration"]["EnvironmentVariables"])}


def test_template_passes_cfn_lint():
    result = subprocess.run([str(CFN_LINT), str(TEMPLATE)], capture_output=True, text=True)
    assert result.returncode in (0, 4, 8, 12), result.stdout + result.stderr  # no error bit (2), no crash (1)


def test_template_does_not_hardcode_a_region():
    assert "ap-south-1" not in TEMPLATE.read_text(encoding="utf-8")


def test_every_buildspec_exists():
    for name in ("Phase1Project", "Phase2Project", "Phase3Project"):
        buildspec = resources()[name]["Properties"]["Source"]["BuildSpec"]
        assert (REPO_ROOT / buildspec).is_file(), buildspec


def test_phase2_runs_docker_on_medium_with_database_secrets():
    environment = resources()["Phase2Project"]["Properties"]["Environment"]
    assert environment["PrivilegedMode"] is True
    assert environment["ComputeType"] == "BUILD_GENERAL1_MEDIUM"
    env = env_vars("Phase2Project")
    assert env["DB_USER"]["Type"] == "SECRETS_MANAGER"
    assert env["DB_USER"]["Value"]["Sub"].endswith(":username")
    assert env["DB_PASSWORD"]["Type"] == "SECRETS_MANAGER"
    assert env["DB_PASSWORD"]["Value"]["Sub"].endswith(":password")
    assert env["PHASE2_CPUSET"]["Value"] == {"Ref": "Phase2CpuSet"}
    assert template()["Parameters"]["Phase2CpuSet"]["Default"] == "0,1"


def test_phases_1_and_3_are_small_and_unprivileged():
    for name in ("Phase1Project", "Phase3Project"):
        environment = resources()[name]["Properties"]["Environment"]
        assert environment["ComputeType"] == "BUILD_GENERAL1_SMALL"
        assert not environment.get("PrivilegedMode", False)


def test_database_is_disposable_sql_server_express():
    database = resources()["Database"]
    assert database["DeletionPolicy"] == "Delete"
    assert database["UpdateReplacePolicy"] == "Delete"
    props = database["Properties"]
    assert props["Engine"] == "sqlserver-ex"
    assert props["ManageMasterUserPassword"] is True
    assert props["PubliclyAccessible"] is True
    assert "MasterUserPassword" not in props


def test_database_firewall_only_allows_the_prefix_list():
    [rule] = resources()["DbSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert rule["FromPort"] == 1433 and rule["ToPort"] == 1433
    assert rule["SourcePrefixListId"] == {"Ref": "DbPrefixList"}
    assert "CidrIp" not in rule


def test_buckets_block_public_access():
    for name in ("DataBucket", "ArtifactBucket"):
        block = resources()[name]["Properties"]["PublicAccessBlockConfiguration"]
        assert all(block.values()), name


def test_pipeline_is_manual_with_four_stages_and_shared_run_id():
    pipeline = resources()["Pipeline"]["Properties"]
    assert pipeline["PipelineType"] == "V2"
    stages = pipeline["Stages"]
    assert [s["Name"] for s in stages] == ["Source", "Phase1", "Phase2", "Phase3"]
    assert stages[0]["Actions"][0]["Configuration"]["DetectChanges"] == "false"
    [variable] = pipeline["Variables"]
    assert variable["Name"] == "RESET_TM" and variable["DefaultValue"] == "true"
    for stage in stages[1:]:
        assert action_env(stage)["RUN_ID"] == "#{codepipeline.PipelineExecutionId}"
    assert action_env(stages[2])["RESET_TM"] == "#{variables.RESET_TM}"
```

- [ ] **Step 2: Write the failing firewall-sync tests**

`aws/tests/test_sync_prefix_list.py`:
```python
import pytest

import sync_prefix_list as spl

IP_RANGES = {
    "prefixes": [
        {"ip_prefix": "13.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "13.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "15.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "99.0.0.0/28", "region": "us-east-1", "service": "CODEBUILD"},
        {"ip_prefix": "13.1.0.0/16", "region": "ap-south-1", "service": "EC2"},
    ]
}


class FakeEc2:
    def __init__(self, entries, max_entries=20, version=3):
        self.entries = list(entries)
        self.max_entries = max_entries
        self.version = version
        self.modifications = []

    def describe_managed_prefix_lists(self, PrefixListIds):
        return {"PrefixLists": [{"PrefixListId": PrefixListIds[0], "MaxEntries": self.max_entries,
                                 "Version": self.version, "State": "modify-complete"}]}

    def get_managed_prefix_list_entries(self, PrefixListId, **kwargs):
        return {"Entries": [{"Cidr": cidr} for cidr in self.entries]}

    def modify_managed_prefix_list(self, **kwargs):
        self.modifications.append(kwargs)
        return {"PrefixList": {"State": "modify-in-progress"}}


def test_codebuild_cidrs_filters_by_service_and_region():
    assert spl.codebuild_cidrs(IP_RANGES, "ap-south-1") == ["13.0.0.0/28", "15.0.0.0/28"]


def test_normalize_cidr():
    assert spl.normalize_cidr("203.0.113.5") == "203.0.113.5/32"
    assert spl.normalize_cidr("203.0.113.0/24") == "203.0.113.0/24"
    with pytest.raises(ValueError):
        spl.normalize_cidr("not-an-ip")


def test_plan_changes():
    assert spl.plan_changes(["a", "b"], ["b", "c"]) == (["c"], ["a"])


def test_sync_adds_and_removes_with_current_version():
    ec2 = FakeEc2(["1.1.1.0/24", "9.9.9.0/24"])
    assert spl.sync(ec2, "pl-1", ["1.1.1.0/24", "2.2.2.0/24"], poll_seconds=0) == (["2.2.2.0/24"], ["9.9.9.0/24"])
    [request] = ec2.modifications
    assert request["PrefixListId"] == "pl-1"
    assert request["CurrentVersion"] == 3
    assert [e["Cidr"] for e in request["AddEntries"]] == ["2.2.2.0/24"]
    assert request["RemoveEntries"] == [{"Cidr": "9.9.9.0/24"}]


def test_sync_does_nothing_when_already_in_sync():
    ec2 = FakeEc2(["1.1.1.0/24"])
    assert spl.sync(ec2, "pl-1", ["1.1.1.0/24"], poll_seconds=0) == ([], [])
    assert ec2.modifications == []


def test_sync_refuses_more_cidrs_than_the_list_holds():
    ec2 = FakeEc2([], max_entries=1)
    with pytest.raises(ValueError):
        spl.sync(ec2, "pl-1", ["1.1.1.0/24", "2.2.2.0/24"], poll_seconds=0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_pipeline_template.py tests/test_sync_prefix_list.py -q`
Expected: FAIL (template and module missing).

- [ ] **Step 4: Write the CloudFormation template**

`aws/infra/pipeline.yaml`:
```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: >-
  AWS version of the cloud localization pipeline: Phase 1 extract, Phase 2 Docker
  NLLB/CTranslate2 translate, Phase 3 merge. Started manually; one run per execution.

Parameters:
  ProjectName:
    Type: String
    Default: cloud-loc
    AllowedPattern: "^[a-z][a-z0-9-]{2,19}$"
    Description: Lower-case prefix for resource names (3-20 characters).
  GitHubRepository:
    Type: String
    Default: AayushGupta56/docker-cloud-rp
    Description: owner/repo of the GitHub repository the pipeline builds.
  GitHubBranch:
    Type: String
    Default: main
    Description: Branch the pipeline checks out.
  DBInstanceClass:
    Type: String
    Default: db.t3.small
    Description: RDS instance class for the SQL Server Express translation memory.
  Phase2CpuSet:
    Type: String
    Default: "0,1"
    Description: Cores the Phase 2 container may use (docker --cpuset-cpus). "0,1" matches the 2-vCPU Azure agent.

Resources:
  DataBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "${ProjectName}-data-${AWS::AccountId}-${AWS::Region}"
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      LifecycleConfiguration:
        Rules:
          - Id: expire-run-files
            Status: Enabled
            Prefix: runs/
            ExpirationInDays: 30

  DataBucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref DataBucket
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Sid: DenyInsecureTransport
            Effect: Deny
            Principal: "*"
            Action: "s3:*"
            Resource:
              - !GetAtt DataBucket.Arn
              - !Sub "${DataBucket.Arn}/*"
            Condition:
              Bool:
                aws:SecureTransport: "false"

  ArtifactBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "${ProjectName}-artifacts-${AWS::AccountId}-${AWS::Region}"
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      LifecycleConfiguration:
        Rules:
          - Id: expire-pipeline-artifacts
            Status: Enabled
            ExpirationInDays: 30

  ArtifactBucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref ArtifactBucket
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Sid: DenyInsecureTransport
            Effect: Deny
            Principal: "*"
            Action: "s3:*"
            Resource:
              - !GetAtt ArtifactBucket.Arn
              - !Sub "${ArtifactBucket.Arn}/*"
            Condition:
              Bool:
                aws:SecureTransport: "false"

  GitHubConnection:
    Type: AWS::CodeStarConnections::Connection
    Properties:
      ConnectionName: !Sub "${ProjectName}-github"
      ProviderType: GitHub

  BuildLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/${ProjectName}/codebuild"
      RetentionInDays: 90

  ResearchDataQuery:
    Type: AWS::Logs::QueryDefinition
    Properties:
      Name: !Sub "${ProjectName}/RESEARCH_DATA per file"
      LogGroupNames:
        - !Ref BuildLogGroup
      QueryString: |
        fields @timestamp
        | filter @message like /RESEARCH_DATA/
        | parse @message /"run_id": "(?<run_id>[^"]+)"/
        | parse @message /"target_lang": "(?<target_lang>[^"]+)"/
        | parse @message /"total_time": (?<total_time_ms>[0-9.]+)/
        | parse @message /"throughput_seg_per_hr": (?<seg_per_hr>[0-9]+)/
        | parse @message /"cache_rate": (?<cache_rate>[0-9.]+)/
        | parse @message /"nmt_calls": (?<nmt_calls>[0-9]+)/
        | parse @message /"azure_sql_query_ms": (?<sql_query_ms>[0-9.]+)/
        | parse @message /"blob_download_ms": (?<download_ms>[0-9.]+)/
        | parse @message /"blob_upload_ms": (?<upload_ms>[0-9.]+)/
        | display @timestamp, run_id, target_lang, total_time_ms, seg_per_hr, cache_rate, nmt_calls, sql_query_ms, download_ms, upload_ms
        | sort @timestamp desc

  DbPrefixList:
    Type: AWS::EC2::PrefixList
    Properties:
      PrefixListName: !Sub "${ProjectName}-db-clients"
      AddressFamily: IPv4
      MaxEntries: 20

  DbSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: SQL Server access for CodeBuild IP ranges (and an optional admin CIDR) only
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 1433
          ToPort: 1433
          SourcePrefixListId: !Ref DbPrefixList
          Description: Entries are synced by aws/infra/deploy.sh

  Database:
    Type: AWS::RDS::DBInstance
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      DBInstanceIdentifier: !Sub "${ProjectName}-tm"
      Engine: sqlserver-ex
      LicenseModel: license-included
      DBInstanceClass: !Ref DBInstanceClass
      AllocatedStorage: "20"
      StorageType: gp3
      MasterUsername: dbadmin
      ManageMasterUserPassword: true
      PubliclyAccessible: true
      MultiAZ: false
      BackupRetentionPeriod: 0
      DeletionProtection: false
      VPCSecurityGroups:
        - !GetAtt DbSecurityGroup.GroupId

  CodeBuildRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: codebuild.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: localization-build
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Sid: WriteBuildLogs
                Effect: Allow
                Action:
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: !GetAtt BuildLogGroup.Arn
              - Sid: RunFilesAndModelCache
                Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                  - s3:DeleteObject
                Resource: !Sub "${DataBucket.Arn}/*"
              - Sid: ListRunFiles
                Effect: Allow
                Action: s3:ListBucket
                Resource: !GetAtt DataBucket.Arn
              - Sid: PipelineArtifacts
                Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:GetObjectVersion
                  - s3:PutObject
                Resource: !Sub "${ArtifactBucket.Arn}/*"
              - Sid: PipelineArtifactBucket
                Effect: Allow
                Action:
                  - s3:GetBucketAcl
                  - s3:GetBucketLocation
                  - s3:ListBucket
                Resource: !GetAtt ArtifactBucket.Arn
              - Sid: DatabaseSecret
                Effect: Allow
                Action: secretsmanager:GetSecretValue
                Resource: !GetAtt Database.MasterUserSecret.SecretArn

  PipelineRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: codepipeline.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: localization-pipeline
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Sid: Artifacts
                Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:GetObjectVersion
                  - s3:PutObject
                Resource: !Sub "${ArtifactBucket.Arn}/*"
              - Sid: ArtifactBucket
                Effect: Allow
                Action:
                  - s3:GetBucketVersioning
                  - s3:GetBucketAcl
                  - s3:GetBucketLocation
                  - s3:ListBucket
                Resource: !GetAtt ArtifactBucket.Arn
              - Sid: RunBuilds
                Effect: Allow
                Action:
                  - codebuild:StartBuild
                  - codebuild:BatchGetBuilds
                Resource:
                  - !GetAtt Phase1Project.Arn
                  - !GetAtt Phase2Project.Arn
                  - !GetAtt Phase3Project.Arn
              - Sid: ReadGitHub
                Effect: Allow
                Action:
                  - codestar-connections:UseConnection
                  - codeconnections:UseConnection
                Resource: !Ref GitHubConnection

  Phase1Project:
    Type: AWS::CodeBuild::Project
    Properties:
      Name: !Sub "${ProjectName}-phase1"
      Description: Phase 1 - convert .resx to XLIFF and upload to S3
      ServiceRole: !GetAtt CodeBuildRole.Arn
      Source:
        Type: CODEPIPELINE
        BuildSpec: aws/buildspecs/phase1.yml
      Artifacts:
        Type: CODEPIPELINE
      Environment:
        Type: LINUX_CONTAINER
        ComputeType: BUILD_GENERAL1_SMALL
        Image: aws/codebuild/standard:7.0
        EnvironmentVariables:
          - Name: BUCKET
            Type: PLAINTEXT
            Value: !Ref DataBucket
      LogsConfig:
        CloudWatchLogs:
          Status: ENABLED
          GroupName: !Ref BuildLogGroup
          StreamName: phase1
      TimeoutInMinutes: 30

  Phase2Project:
    Type: AWS::CodeBuild::Project
    Properties:
      Name: !Sub "${ProjectName}-phase2"
      Description: Phase 2 - translate in Docker with NLLB-200 600M (CTranslate2 INT8)
      ServiceRole: !GetAtt CodeBuildRole.Arn
      Source:
        Type: CODEPIPELINE
        BuildSpec: aws/buildspecs/phase2.yml
      Artifacts:
        Type: CODEPIPELINE
      Environment:
        Type: LINUX_CONTAINER
        ComputeType: BUILD_GENERAL1_MEDIUM
        Image: aws/codebuild/standard:7.0
        PrivilegedMode: true
        EnvironmentVariables:
          - Name: BUCKET
            Type: PLAINTEXT
            Value: !Ref DataBucket
          - Name: DB_HOST
            Type: PLAINTEXT
            Value: !GetAtt Database.Endpoint.Address
          - Name: DB_NAME
            Type: PLAINTEXT
            Value: localization
          - Name: DB_USER
            Type: SECRETS_MANAGER
            Value: !Sub "${Database.MasterUserSecret.SecretArn}:username"
          - Name: DB_PASSWORD
            Type: SECRETS_MANAGER
            Value: !Sub "${Database.MasterUserSecret.SecretArn}:password"
          - Name: PHASE2_CPUSET
            Type: PLAINTEXT
            Value: !Ref Phase2CpuSet
      LogsConfig:
        CloudWatchLogs:
          Status: ENABLED
          GroupName: !Ref BuildLogGroup
          StreamName: phase2
      TimeoutInMinutes: 120

  Phase3Project:
    Type: AWS::CodeBuild::Project
    Properties:
      Name: !Sub "${ProjectName}-phase3"
      Description: Phase 3 - merge translated XLIFF into localized .resx
      ServiceRole: !GetAtt CodeBuildRole.Arn
      Source:
        Type: CODEPIPELINE
        BuildSpec: aws/buildspecs/phase3.yml
      Artifacts:
        Type: CODEPIPELINE
      Environment:
        Type: LINUX_CONTAINER
        ComputeType: BUILD_GENERAL1_SMALL
        Image: aws/codebuild/standard:7.0
        EnvironmentVariables:
          - Name: BUCKET
            Type: PLAINTEXT
            Value: !Ref DataBucket
      LogsConfig:
        CloudWatchLogs:
          Status: ENABLED
          GroupName: !Ref BuildLogGroup
          StreamName: phase3
      TimeoutInMinutes: 30

  Pipeline:
    Type: AWS::CodePipeline::Pipeline
    Properties:
      Name: !Sub "${ProjectName}-pipeline"
      PipelineType: V2
      ExecutionMode: QUEUED
      RoleArn: !GetAtt PipelineRole.Arn
      ArtifactStore:
        Type: S3
        Location: !Ref ArtifactBucket
      Variables:
        - Name: RESET_TM
          DefaultValue: "true"
          Description: "true empties the translation memory before Phase 2 (0% cache start)."
      Stages:
        - Name: Source
          Actions:
            - Name: GitHub
              ActionTypeId:
                Category: Source
                Owner: AWS
                Provider: CodeStarSourceConnection
                Version: "1"
              Configuration:
                ConnectionArn: !Ref GitHubConnection
                FullRepositoryId: !Ref GitHubRepository
                BranchName: !Ref GitHubBranch
                DetectChanges: "false"
                OutputArtifactFormat: CODE_ZIP
              OutputArtifacts:
                - Name: SourceOutput
        - Name: Phase1
          Actions:
            - Name: ExtractAndUpload
              ActionTypeId:
                Category: Build
                Owner: AWS
                Provider: CodeBuild
                Version: "1"
              InputArtifacts:
                - Name: SourceOutput
              Configuration:
                ProjectName: !Ref Phase1Project
                EnvironmentVariables: '[{"name":"RUN_ID","value":"#{codepipeline.PipelineExecutionId}","type":"PLAINTEXT"}]'
        - Name: Phase2
          Actions:
            - Name: TranslateInDocker
              ActionTypeId:
                Category: Build
                Owner: AWS
                Provider: CodeBuild
                Version: "1"
              InputArtifacts:
                - Name: SourceOutput
              Configuration:
                ProjectName: !Ref Phase2Project
                EnvironmentVariables: '[{"name":"RUN_ID","value":"#{codepipeline.PipelineExecutionId}","type":"PLAINTEXT"},{"name":"RESET_TM","value":"#{variables.RESET_TM}","type":"PLAINTEXT"}]'
        - Name: Phase3
          Actions:
            - Name: MergeToResx
              ActionTypeId:
                Category: Build
                Owner: AWS
                Provider: CodeBuild
                Version: "1"
              InputArtifacts:
                - Name: SourceOutput
              OutputArtifacts:
                - Name: LocalizedResx
              Configuration:
                ProjectName: !Ref Phase3Project
                EnvironmentVariables: '[{"name":"RUN_ID","value":"#{codepipeline.PipelineExecutionId}","type":"PLAINTEXT"}]'

Outputs:
  PipelineName:
    Value: !Ref Pipeline
  DataBucketName:
    Value: !Ref DataBucket
  ArtifactBucketName:
    Value: !Ref ArtifactBucket
  GitHubConnectionArn:
    Value: !Ref GitHubConnection
  DbEndpoint:
    Value: !GetAtt Database.Endpoint.Address
  DbSecretArn:
    Value: !GetAtt Database.MasterUserSecret.SecretArn
  DbPrefixListId:
    Value: !Ref DbPrefixList
  LogGroupName:
    Value: !Ref BuildLogGroup
```

- [ ] **Step 5: Write the firewall sync helper**

`aws/infra/sync_prefix_list.py`:
```python
"""Keep the database firewall prefix list in sync with CodeBuild's published IP ranges."""
import argparse
import ipaddress
import json
import sys
import time
import urllib.request

import boto3

IP_RANGES_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"


def codebuild_cidrs(ip_ranges, region):
    return sorted({
        prefix["ip_prefix"]
        for prefix in ip_ranges.get("prefixes", [])
        if prefix.get("service") == "CODEBUILD" and prefix.get("region") == region
    })


def normalize_cidr(value):
    return str(ipaddress.ip_network(value, strict=False))


def plan_changes(current, desired):
    current, desired = set(current), set(desired)
    return sorted(desired - current), sorted(current - desired)


def _describe(client, prefix_list_id):
    return client.describe_managed_prefix_lists(PrefixListIds=[prefix_list_id])["PrefixLists"][0]


def _current_cidrs(client, prefix_list_id):
    cidrs, kwargs = [], {"PrefixListId": prefix_list_id}
    while True:
        resp = client.get_managed_prefix_list_entries(**kwargs)
        cidrs.extend(entry["Cidr"] for entry in resp.get("Entries", []))
        token = resp.get("NextToken")
        if not token:
            return cidrs
        kwargs["NextToken"] = token


def sync(client, prefix_list_id, desired, poll_seconds=2, max_polls=60):
    desired = sorted(set(desired))
    info = _describe(client, prefix_list_id)
    if len(desired) > info["MaxEntries"]:
        raise ValueError(f"{len(desired)} CIDRs needed but the prefix list holds at most {info['MaxEntries']}")
    add, remove = plan_changes(_current_cidrs(client, prefix_list_id), desired)
    if not add and not remove:
        return add, remove

    request = {"PrefixListId": prefix_list_id, "CurrentVersion": info["Version"]}
    if add:
        request["AddEntries"] = [{"Cidr": cidr, "Description": "localization pipeline database client"} for cidr in add]
    if remove:
        request["RemoveEntries"] = [{"Cidr": cidr} for cidr in remove]
    client.modify_managed_prefix_list(**request)

    for _ in range(max_polls):
        state = _describe(client, prefix_list_id)["State"]
        if state.endswith("-complete"):
            return add, remove
        if state.endswith("-failed"):
            raise RuntimeError(f"prefix list update failed: {state}")
        time.sleep(poll_seconds)
    raise TimeoutError("prefix list update did not finish in time")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix-list-id", required=True)
    parser.add_argument("--extra-cidr", action="append", default=[],
                        help="Additional CIDR allowed to reach the database (repeatable).")
    args = parser.parse_args(argv)

    with urllib.request.urlopen(IP_RANGES_URL, timeout=30) as resp:
        cidrs = codebuild_cidrs(json.load(resp), args.region)
    if not cidrs:
        print(f"❌ AWS publishes no CODEBUILD IP ranges for {args.region}", file=sys.stderr)
        return 1
    desired = cidrs + [normalize_cidr(cidr) for cidr in args.extra_cidr]

    add, remove = sync(boto3.client("ec2", region_name=args.region), args.prefix_list_id, desired)
    print(f"🔐 Database firewall: {len(desired)} allowed ranges (+{len(add)} / -{len(remove)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Write the deploy script**

`aws/infra/deploy.sh`:
```bash
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

aws --version 2>&1 | grep -q '^aws-cli/2' || { echo "❌ AWS CLI v2 is required."; exit 1; }
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
     Add --variables name=RESET_TM,value=false to keep the translation memory from earlier runs.
EOF
```

- [ ] **Step 7: Write the teardown script**

`aws/infra/teardown.sh`:
```bash
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
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_pipeline_template.py tests/test_sync_prefix_list.py tests/test_buildspecs.py -q`
Expected: 21 passed. If cfn-lint reports an error, fix the template. In particular, an error saying `db.t3.small` is not valid for `sqlserver-ex` means the default `DBInstanceClass` must change (pick the smallest class cfn-lint accepts and update `deploy.sh`'s default to match).

- [ ] **Step 9: Commit**

```bash
git add aws/infra aws/tests/test_pipeline_template.py aws/tests/test_sync_prefix_list.py
git commit -m "feat(aws): add CloudFormation stack, firewall sync, deploy and teardown" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Telemetry export to CSV

**Files:**
- Create: `aws/tools/export_telemetry.py`, `aws/tests/test_export_telemetry.py`

**Interfaces:**
- Consumes: `RESEARCH_DATA: {json}` log lines written by Task 6; log group `/cloud-loc/codebuild` (Task 10)
- Produces: `export_telemetry.AZURE_FIELDS` (41 keys in Azure order), `AWS_FIELDS`, `parse_research_line(message) -> dict | None`, `collect(logs_client, log_group, run_id) -> list[dict]`, `write_csv(rows, out)`, `main(argv=None) -> int`. CLI: `python aws/tools/export_telemetry.py --run-id ID [--out FILE] [--log-group G] [--region R]`.

- [ ] **Step 1: Write the failing tests**

`aws/tests/test_export_telemetry.py`:
```python
import ast
import csv
import io
import json

import export_telemetry as et
from helpers import AZURE_DIR


def azure_payload_keys():
    tree = ast.parse((AZURE_DIR / "Docker-Local-Inference" / "translation.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "metrics_payload" for t in node.targets):
            return [key.value for key in node.value.keys]
    raise LookupError("metrics_payload not found")


def line(payload):
    return f"2026-10-16 10:00:00,000 [INFO] 📊 RESEARCH_DATA: {json.dumps(payload)}"


class FakeLogs:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def test_azure_fields_match_the_azure_payload_in_order():
    assert et.AZURE_FIELDS == azure_payload_keys()


def test_parse_research_line():
    assert et.parse_research_line(line({"run_id": "r1", "total_time": 5})) == {"run_id": "r1", "total_time": 5}
    assert et.parse_research_line("[INFO] Translating resx_file_10.es-ES.xlf") is None
    assert et.parse_research_line("RESEARCH_DATA: {not json") is None


def test_collect_follows_pages_and_keeps_only_the_run():
    logs = FakeLogs([
        {"events": [{"message": line({"run_id": "r1", "target_lang": "de-DE"})},
                    {"message": line({"run_id": "other", "target_lang": "fr-FR"})}],
         "nextToken": "t2"},
        {"events": [{"message": line({"run_id": "r1", "target_lang": "es-ES"})}]},
    ])
    rows = et.collect(logs, "/cloud-loc/codebuild", "r1")
    assert [r["target_lang"] for r in rows] == ["de-DE", "es-ES"]
    assert logs.calls[1]["nextToken"] == "t2"
    assert logs.calls[0]["logGroupName"] == "/cloud-loc/codebuild"


def test_write_csv_uses_azure_order_then_aws_fields():
    out = io.StringIO()
    et.write_csv([{"run_id": "r1", "total_time": 5, "unexpected": "dropped"}], out)
    rows = list(csv.reader(io.StringIO(out.getvalue())))
    assert rows[0] == et.AZURE_FIELDS + et.AWS_FIELDS
    record = dict(zip(rows[0], rows[1]))
    assert record["total_time"] == "5"
    assert record["run_id"] == "r1"
    assert record["file_name"] == ""


def test_main_fails_when_the_run_has_no_telemetry(monkeypatch):
    monkeypatch.setattr(et.boto3, "client", lambda *args, **kwargs: FakeLogs([{"events": []}]))
    assert et.main(["--run-id", "missing"]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd aws && .venv/bin/python -m pytest tests/test_export_telemetry.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'export_telemetry'`.

- [ ] **Step 3: Implement**

`aws/tools/export_telemetry.py`:
```python
"""Export the RESEARCH_DATA lines of one AWS pipeline run to CSV (Azure column order first)."""
import argparse
import csv
import json
import sys

import boto3

MARKER = "RESEARCH_DATA: "

# Same keys and order as metrics_payload in the Azure translation.py (checked by tests).
AZURE_FIELDS = [
    "run_invocation_id", "file_name", "target_lang", "throughput_seg_per_hr",
    "throughput_input_tokens_per_hr", "total_api_call_logically", "actual_api_call",
    "total_segments", "avg_segment_char_length", "text_expansion_ratio",
    "tokens_processed_in_whole_language", "total_input_tokens_source", "total_output_tokens_target",
    "avg_input_tokens_per_batch", "avg_output_tokens_per_batch", "char_per_token_ratio",
    "total_token_input_plus_output_per_batch", "time_per_token", "nmt_calls",
    "transational_memory_hits", "cache_rate", "total_time", "cold_start_init_ms",
    "compute_time_ms", "total_sleep_time_ms", "network_wait_time_ms", "io_bound_pipeline_pct",
    "hf_api_wait_ms", "azure_sql_query_ms", "azure_sql_commit_ms", "xml_parse_ms",
    "xml_disk_write_ms", "blob_download_ms", "blob_upload_ms", "telemetry_overhead_ms",
    "cpu_usage_pct", "memory_mb", "serverless_cost_gb_sec", "api_retries", "api_failures",
    "api_throttled_429",
]
AWS_FIELDS = ["cloud_provider", "region", "run_id", "cpu_limit", "cpu_model"]


def parse_research_line(message):
    if MARKER not in message:
        return None
    try:
        payload = json.loads(message.split(MARKER, 1)[1].strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def collect(logs_client, log_group, run_id):
    rows = []
    kwargs = {"logGroupName": log_group, "filterPattern": f'"RESEARCH_DATA" "{run_id}"'}
    while True:
        resp = logs_client.filter_log_events(**kwargs)
        for event in resp.get("events", []):
            payload = parse_research_line(event["message"])
            if payload and payload.get("run_id") == run_id:
                rows.append(payload)
        token = resp.get("nextToken")
        if not token:
            return rows
        kwargs["nextToken"] = token


def write_csv(rows, out):
    writer = csv.DictWriter(out, fieldnames=AZURE_FIELDS + AWS_FIELDS, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="CodePipeline execution ID of the run.")
    parser.add_argument("--log-group", default="/cloud-loc/codebuild")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--out", help="CSV file to write (default: stdout).")
    args = parser.parse_args(argv)

    rows = collect(boto3.client("logs", region_name=args.region), args.log_group, args.run_id)
    if not rows:
        print(f"No RESEARCH_DATA found for run {args.run_id} in {args.log_group}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            write_csv(rows, f)
        print(f"Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    else:
        write_csv(rows, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd aws && .venv/bin/python -m pytest tests/test_export_telemetry.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add aws/tools/export_telemetry.py aws/tests/test_export_telemetry.py
git commit -m "feat(aws): export RESEARCH_DATA telemetry to CSV in Azure column order" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Documentation and full offline verification

**Files:**
- Create: `aws/README.md`, `aws/tests/test_docs.py`
- Modify: `README.md` (rewrite)

**Interfaces:**
- Consumes: everything above; documents commands and defaults exactly as defined in Tasks 1–11.

- [ ] **Step 1: Write the failing docs test**

`aws/tests/test_docs.py`:
```python
import re

from helpers import AWS_DIR, AZURE_DIR, REPO_ROOT

LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def test_readme_links_resolve():
    for readme in (REPO_ROOT / "README.md", AWS_DIR / "README.md", AZURE_DIR / "README.md"):
        text = readme.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://")):
                continue
            assert (readme.parent / target).exists(), f"{readme.relative_to(REPO_ROOT)} → {target}"


def test_aws_readme_documents_the_essential_commands():
    text = (AWS_DIR / "README.md").read_text(encoding="utf-8")
    for command in ("aws/infra/deploy.sh", "aws/infra/teardown.sh", "start-pipeline-execution",
                    "export_telemetry.py", "RESET_TM"):
        assert command in text, command
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd aws && .venv/bin/python -m pytest tests/test_docs.py -q`
Expected: FAIL (`aws/README.md` missing).

- [ ] **Step 3: Write `aws/README.md`**

````markdown
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

- **Phase 2 cannot reach S3 from inside the container** (`NoCredentialsError` or `Unable to locate credentials`): in `scripts/run_phase2.sh`, after the `DOCKER_ENV=(...)` line, add
  ```bash
  eval "$(aws configure export-credentials --format env)"
  DOCKER_ENV+=(-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN)
  ```
- **Phase 2 times out connecting to the database:** CodeBuild's IP ranges may have changed; run `deploy.sh` again (it re-syncs the firewall).
- **The cached model is broken:** `aws s3 rm s3://<data-bucket>/models/<model-key>/_COMPLETE` forces a fresh conversion on the next run.

## Tests

```bash
cd aws && .venv/bin/python -m pytest -q
```
````

- [ ] **Step 4: Rewrite the root `README.md`**

Replace the whole file with:
````markdown
# Cloud Localization Pipeline

Translates English `.resx` resource strings into other languages in three phases:

1. **Extract:** convert each `.resx` file into one XLIFF (`.xlf`) file per target language.
2. **Translate:** look each string up in a SQL translation memory and translate the misses with NLLB-200 (distilled 600M, CTranslate2 INT8) inside Docker; write new translations back to the memory.
3. **Merge:** turn the translated XLIFF files back into localized `.resx` files.

## Two implementations

| Folder | Cloud | Status |
|---|---|---|
| [azure/](azure/README.md) | Azure DevOps Pipelines, Blob Storage, Azure SQL, Application Insights | Preserved: the Docker generation measured in the end-semester report |
| [aws/](aws/README.md) | CodePipeline + CodeBuild, S3, RDS for SQL Server, CloudWatch | Current: used for future work |

Both use the same model, inference settings, workload and telemetry fields, so their results can be compared directly. The AWS design is in [the design spec](docs/superpowers/specs/2026-10-16-aws-migration-design.md).

## Shared inputs

[source-folder/](source-folder/) holds the 20 English `.resx` files. The comparison workload is `resx_file_10.resx` translated into `fr-FR`, `es-ES`, `hi-IN`, `de-DE`, `ja-JP`, `ru-RU` and `zh-CN`.

## Project documents

- [ENDSEM_REPORT_README.md](ENDSEM_REPORT_README.md): source material for the end-semester report.
- [idea.md](idea.md) and [implementation-checklist.md](implementation-checklist.md): the earlier optimization plan.

## Secrets

Never commit keys, passwords or tokens. Azure secrets are injected by the pipeline at run time; AWS uses IAM roles and Secrets Manager. Keys that were committed earlier remain in git history and must be rotated.
````

- [ ] **Step 5: Run the full offline test suite**

Run: `cd aws && .venv/bin/python -m pytest -q`
Expected: 94 passed, including the Task 1 secret scan over the new `aws/` files.

- [ ] **Step 6: Commit**

```bash
git add README.md aws/README.md aws/tests/test_docs.py
git commit -m "docs: add AWS runbook and rewrite project README for both clouds" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Live deployment and acceptance (needs the user's AWS account)

This task creates billable resources and pushes code. **Every step marked STOP needs the user's explicit approval first.**

**Files:**
- Modify: `aws/README.md` (add a "Verified" section with findings), and `aws/scripts/run_phase2.sh` or `aws/infra/pipeline.yaml` only if a check below fails.

- [ ] **Step 1: Read-only pre-flight checks** (no resources created)

```bash
aws sts get-caller-identity
aws rds describe-orderable-db-instance-options --region ap-south-1 --engine sqlserver-ex \
  --db-instance-class db.t3.small --query 'OrderableDBInstanceOptions[0].DBInstanceClass' --output text
aws ec2 describe-vpcs --region ap-south-1 --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text
curl -s https://ip-ranges.amazonaws.com/ip-ranges.json | python3 -c "import json,sys; print([p['ip_prefix'] for p in json.load(sys.stdin)['prefixes'] if p['service']=='CODEBUILD' and p['region']=='ap-south-1'])"
```
Expected: an account ID; `db.t3.small` (if `None`, pick a class that `describe-orderable-db-instance-options --engine sqlserver-ex` lists and pass it as `DB_INSTANCE_CLASS`); a `vpc-…` ID; a non-empty list of CIDRs. Report the results to the user.

- [ ] **Step 2: STOP — ask the user** to approve (a) pushing branch `aws-migration` to `origin` and (b) deploying the stack, which starts billing (about $1/day while it exists).

- [ ] **Step 3: Push and deploy** (after approval)

```bash
git push -u origin aws-migration
GITHUB_BRANCH=aws-migration PYTHON=aws/.venv/bin/python bash aws/infra/deploy.sh
```
Expected: `✅ Stack ready.` plus next steps.

- [ ] **Step 4: STOP — ask the user** to approve the GitHub connection in the AWS console (only they can do this), and wait for confirmation.

- [ ] **Step 5: First run at 0% cache**

```bash
RUN1=$(aws codepipeline start-pipeline-execution --region ap-south-1 --name cloud-loc-pipeline \
  --variables name=RESET_TM,value=true --query pipelineExecutionId --output text); echo "$RUN1"
aws codepipeline get-pipeline-execution --region ap-south-1 --pipeline-name cloud-loc-pipeline \
  --pipeline-execution-id "$RUN1" --query pipelineExecution.status --output text
```
Re-check the status until it is `Succeeded` or `Failed` (first run: roughly 30–45 minutes including model conversion). On failure, read the failing phase's log in the `/cloud-loc/codebuild` log group, fix, commit, push, and start a new run. If Phase 2 shows `Unable to locate credentials` inside the container, apply the fallback from `aws/README.md` → Troubleshooting.

- [ ] **Step 6: Verify success criterion 1**

```bash
BUCKET=$(aws cloudformation describe-stacks --region ap-south-1 --stack-name cloud-loc \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text)
aws s3 ls "s3://$BUCKET/runs/$RUN1/output/" --recursive
aws/.venv/bin/python aws/tools/export_telemetry.py --run-id "$RUN1" --out "$RUN1.csv"
python3 - "$RUN1.csv" <<'EOF'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
assert len(rows) == 7, len(rows)
for r in rows:
    assert r["total_segments"] == "50" and r["nmt_calls"] == "50", r["target_lang"]
    assert r["transational_memory_hits"] == "0" and float(r["cache_rate"]) == 0.0, r["target_lang"]
    assert r["cpu_limit"] == "2", r["cpu_limit"]
print("criterion 1 OK:", sorted(r["target_lang"] for r in rows))
EOF
```
Expected: 7 `.resx` files listed (one per language) and `criterion 1 OK`.

- [ ] **Step 7: Second run keeps the translation memory (success criterion 2)**

```bash
RUN2=$(aws codepipeline start-pipeline-execution --region ap-south-1 --name cloud-loc-pipeline \
  --variables name=RESET_TM,value=false --query pipelineExecutionId --output text); echo "$RUN2"
# re-check status until Succeeded, then:
aws/.venv/bin/python aws/tools/export_telemetry.py --run-id "$RUN2" --out "$RUN2.csv"
python3 - "$RUN2.csv" <<'EOF'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
assert len(rows) == 7
for r in rows:
    assert float(r["cache_rate"]) == 100.0 and r["nmt_calls"] == "0", r["target_lang"]
print("criterion 2 OK")
EOF
```

- [ ] **Step 8: Record the findings**

Append to `aws/README.md`:
```markdown
## Verified

Checked on <date> in ap-south-1 (runs <RUN1>, <RUN2>):
- Container credentials passthrough: <worked as designed | needed the troubleshooting fallback>
- RDS class: <db.t3.small | the class actually used>
- Firewall prefix list after a stack update: re-synced by deploy.sh
- Model conversion with ctranslate2 4.7.1 / transformers 4.38.2 / torch 2.2.2 (CPU): <ok | what changed>
- Run 1 (RESET_TM=true): 7 languages × 50 segments, 0% cache, cpu_limit 2
- Run 2 (RESET_TM=false): 100% cache, 0 NMT calls
```
Fill in the angle-bracket values from what actually happened. Commit:
```bash
git add aws/README.md
git commit -m "docs(aws): record live verification results" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Leave the two CSV files untracked and tell the user where they are.

- [ ] **Step 9: STOP — ask the user** whether to keep the stack for more experiments or tear it down now. If tearing down:

```bash
bash aws/infra/teardown.sh --yes
aws cloudformation describe-stacks --region ap-south-1 --stack-name cloud-loc 2>&1 | grep -q "does not exist" && echo "stack gone"
aws rds describe-db-instances --region ap-south-1 --query "DBInstances[?DBInstanceIdentifier=='cloud-loc-tm']" --output text
```
Expected: `✅ Stack deleted.`, `stack gone`, and empty output from the RDS query (success criterion 4).
