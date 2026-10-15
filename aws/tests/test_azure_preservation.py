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
