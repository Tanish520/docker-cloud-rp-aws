"""Run infra/run_on_ec2.sh against stand-in aws and git commands that log their calls."""
import json
import subprocess

import pytest

from helpers import AWS_DIR

RUN_ON_EC2 = AWS_DIR / "infra" / "run_on_ec2.sh"

AWS_STUB = r"""#!/bin/bash
echo "aws $*" >> "$STUB_LOG"
args="$*"
case "$args" in
  "cloudformation describe-stacks"*DataBucketName*) echo "bucket" ;;
  "cloudformation describe-stacks"*RunnerLaunchTemplateId*) if [ -n "$STUB_NO_RUNNER" ]; then echo "None"; else echo "lt-123"; fi ;;
  "cloudformation describe-stacks"*RunnerLogGroupName*) echo "/cloud-loc/ec2-runner" ;;
  "cloudformation describe-stacks"*DbEndpoint*) echo "db.example" ;;
  "cloudformation describe-stacks"*DbSecretArn*) echo "arn:aws:secretsmanager:ap-south-1:1:secret:rds!db-x" ;;
  "s3 cp - "*) cat > "$STUB_UPLOAD" ;;
  "ec2 run-instances"*) echo "i-0abc" ;;
  "ec2 wait"*) ;;
  "ssm describe-instance-information"*) echo "Online" ;;
  "ssm send-command"*)
    shift 0
    while [ $# -gt 0 ]; do
      if [ "$1" = "--parameters" ]; then printf '%s' "$2" > "$STUB_PARAMS"; fi
      shift
    done
    echo "cmd-1" ;;
  "ssm get-command-invocation"*"--query Status"*) echo "${STUB_STATUS:-Success}" ;;
  "ssm get-command-invocation"*) echo "boom: phase 2 failed" ;;
  "ec2 terminate-instances"*) ;;
  *) echo "unexpected aws call: $args" >&2; exit 1 ;;
esac
"""
GIT_STUB = r"""#!/bin/bash
case "$1" in
  status) printf '%s' "${STUB_DIRTY:-}" ;;
  archive) printf 'TARBALL' ;;
  rev-parse) echo "abc1234" ;;
esac
"""


@pytest.fixture
def run_on_ec2(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("aws", AWS_STUB), ("git", GIT_STUB)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    log, upload, params = tmp_path / "calls.log", tmp_path / "upload.bin", tmp_path / "params.json"

    def run(**overrides):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_LOG": str(log),
               "STUB_UPLOAD": str(upload), "STUB_PARAMS": str(params), "RUNNER_POLL_SECONDS": "0"}
        env.update(overrides)
        result = subprocess.run(["bash", str(RUN_ON_EC2)], cwd=tmp_path, env=env, capture_output=True, text=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls, upload, params

    return run


def test_successful_run_uploads_source_runs_all_phases_logs_to_cloudwatch_and_terminates(run_on_ec2):
    result, calls, upload, params = run_on_ec2()
    assert result.returncode == 0, result.stdout + result.stderr
    [cp] = [c for c in calls if c.startswith("aws s3 cp - ")]
    assert "s3://bucket/runs/ec2-" in cp and cp.split()[4].endswith("/source.tar.gz")
    assert upload.read_text() == "TARBALL"
    assert any(c.startswith("aws ec2 run-instances") and "LaunchTemplateId=lt-123" in c for c in calls)
    [send] = [c for c in calls if c.startswith("aws ssm send-command")]
    assert "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=/cloud-loc/ec2-runner" in send
    command = json.loads(params.read_text())["commands"][0]
    assert "bash aws/runner/run_all.sh" in command and "RESET_TM=true" in command
    assert "DB_SECRET_ARN='arn:aws:secretsmanager:ap-south-1:1:secret:rds!db-x'" in command  # RDS ARNs contain '!'
    assert calls[-1].startswith("aws ec2 terminate-instances") and "i-0abc" in calls[-1]


def test_failed_run_exits_non_zero_shows_the_error_and_still_terminates(run_on_ec2):
    result, calls, _, _ = run_on_ec2(STUB_STATUS="Failed")
    assert result.returncode != 0
    assert "boom: phase 2 failed" in result.stdout
    assert calls[-1].startswith("aws ec2 terminate-instances")


def test_uncommitted_changes_are_warned_about(run_on_ec2):
    result, _, _, _ = run_on_ec2(STUB_DIRTY=" M aws/config.json\n")
    assert "Uncommitted changes are not included" in result.stdout


def test_missing_runner_stack_outputs_stop_before_launching(run_on_ec2):
    result, calls, _, _ = run_on_ec2(STUB_NO_RUNNER="1")
    assert result.returncode != 0
    assert not any("run-instances" in c for c in calls)


def test_runner_task_is_passed_to_the_machine(run_on_ec2):
    result, _, _, params = run_on_ec2(RUNNER_TASK="batching-bench")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUNNER_TASK=batching-bench" in json.loads(params.read_text())["commands"][0]


def test_timeout_is_configurable_and_the_message_names_the_task(run_on_ec2):
    result, _, _, params = run_on_ec2(RUNNER_TASK="batching-bench", RUNNER_TIMEOUT_SECONDS="10800")
    assert json.loads(params.read_text())["executionTimeout"] == ["10800"]
    assert "Running batching-bench" in result.stdout


def test_benchmark_arguments_reach_the_machine(run_on_ec2):
    result, _, _, params = run_on_ec2(RUNNER_TASK="batching-bench", BENCH_ARGS="--kde4 s3://b/k.xlsx --kde4-strings 1000")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BENCH_ARGS='--kde4 s3://b/k.xlsx --kde4-strings 1000'" in json.loads(params.read_text())["commands"][0]
