"""Run runner/run_all.sh in a fake source folder with stand-in phases and a stand-in aws command."""
import shutil
import subprocess

import pytest

from helpers import AWS_DIR

RUN_ALL = AWS_DIR / "runner" / "run_all.sh"

AWS_STUB = r"""#!/bin/bash
case "$*" in
  "secretsmanager get-secret-value"*) printf '%s\n' "$STUB_SECRET" ;;
  "s3 cp "*) echo "aws $*" >> "$STUB_LOG"; printf '<root/>' > "$4" ;;
  *) exit 1 ;;
esac
"""
PHASE_PY = """import os, sys
with open(os.environ["STUB_LOG"], "a") as log:
    log.write("{name} " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("STUB_{upper}_EXIT", "0")))
"""
PHASE2_SH = """#!/bin/bash
echo "phase2 MODEL_KEY=$MODEL_KEY DB_USER=$DB_USER DB_PASSWORD=$DB_PASSWORD RESET_TM=$RESET_TM CPUSET=$PHASE2_CPUSET REGION=$AWS_DEFAULT_REGION CONFIG=$PIPELINE_CONFIG TASK=${PHASE2_TASK:-}" >> "$STUB_LOG"
"""


@pytest.fixture
def run_all(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(AWS_STUB, encoding="utf-8")
    (bin_dir / "aws").chmod(0o755)
    src = tmp_path / "src"
    (src / "aws" / "buildspecs").mkdir(parents=True)
    (src / "aws" / "scripts").mkdir()
    shutil.copy(AWS_DIR / "buildspecs" / "phase2.yml", src / "aws" / "buildspecs" / "phase2.yml")
    (src / "aws" / "config.json").write_text("{}", encoding="utf-8")
    for name in ("phase1", "phase3"):
        (src / "aws" / f"{name}.py").write_text(PHASE_PY.format(name=name, upper=name.upper()), encoding="utf-8")
    (src / "aws" / "scripts" / "run_phase2.sh").write_text(PHASE2_SH, encoding="utf-8")
    log = tmp_path / "calls.log"

    def run(**overrides):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_LOG": str(log),
               "RUN_ID": "ec2-test", "BUCKET": "bucket", "DB_HOST": "db.example", "AWS_REGION": "ap-south-1",
               "DB_SECRET_ARN": "arn:aws:secretsmanager:ap-south-1:1:secret:rds!db-x",
               "STUB_SECRET": '{"username": "dbadmin", "password": "p;a}ss q"}'}
        env.update(overrides)
        env = {k: v for k, v in env.items() if v is not None}
        result = subprocess.run(["bash", str(RUN_ALL)], cwd=src, env=env, capture_output=True, text=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    return run


def test_runs_the_three_phases_in_order_with_credentials_and_model_key(run_all):
    result, calls = run_all()
    assert result.returncode == 0, result.stdout + result.stderr
    assert [c.split()[0] for c in calls] == ["phase1", "phase2", "phase3"]
    phase2 = calls[1]
    assert "MODEL_KEY=nllb-200-distilled-600M_int8_ct2-4.7.1" in phase2
    assert "DB_USER=dbadmin DB_PASSWORD=p;a}ss q " in phase2
    assert "RESET_TM=true CPUSET=0,1 REGION=ap-south-1" in phase2


def test_phase1_failure_stops_the_run(run_all):
    result, calls = run_all(STUB_PHASE1_EXIT="1")
    assert result.returncode != 0
    assert [c.split()[0] for c in calls] == ["phase1"]


def test_missing_settings_fail_before_any_phase(run_all):
    result, calls = run_all(DB_SECRET_ARN=None)
    assert result.returncode != 0
    assert calls == []


def test_database_password_is_never_printed(run_all):
    result, _ = run_all()
    assert "p;a}ss" not in result.stdout + result.stderr


def test_batching_bench_task_runs_only_phase_2_in_benchmark_mode(run_all):
    result, calls = run_all(RUNNER_TASK="batching-bench")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [c.split()[0] for c in calls] == ["phase2"]
    assert calls[0].endswith("TASK=bench")


def test_phases_use_the_chosen_config(run_all):
    result, calls = run_all(PIPELINE_CONFIG="aws/config.kde4.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[0] == "phase1 aws/config.kde4.json"
    assert "CONFIG=aws/config.kde4.json" in calls[1]
    assert calls[2] == "phase3 aws/config.kde4.json"


def test_input_file_is_fetched_into_the_source_folder_before_phase_1(run_all, tmp_path):
    result, calls = run_all(INPUT_S3="s3://bucket/datasets/kde4/kde4.resx")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[0] == "aws s3 cp s3://bucket/datasets/kde4/kde4.resx source-folder/kde4.resx --only-show-errors"
    assert calls[1].startswith("phase1 ")
    assert (tmp_path / "src" / "source-folder" / "kde4.resx").exists()


def test_several_input_files_can_be_fetched(run_all):
    result, calls = run_all(INPUT_S3="s3://bucket/check/a.resx s3://bucket/check/b.resx")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[:2] == ["aws s3 cp s3://bucket/check/a.resx source-folder/a.resx --only-show-errors",
                         "aws s3 cp s3://bucket/check/b.resx source-folder/b.resx --only-show-errors"]
