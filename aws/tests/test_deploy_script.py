"""infra/deploy.sh pre-flight checks, run against a stand-in aws command."""
import subprocess

from helpers import AWS_DIR

DEPLOY = AWS_DIR / "infra" / "deploy.sh"

AWS_STUB = r"""#!/bin/bash
case "$*" in
  --version) echo "$STUB_VERSION" ;;
  "sts get-caller-identity"*) exit 255 ;;
esac
"""


def run_deploy(tmp_path, version):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aws"
    stub.write_text(AWS_STUB, encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_VERSION": version}
    return subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True)


def test_cli_v1_is_rejected(tmp_path):
    result = run_deploy(tmp_path, "aws-cli/1.29.0 Python/3.9.6 Darwin/25.6.0 botocore/1.31.0")
    assert result.returncode != 0
    assert "AWS CLI v2 is required" in result.stdout


def test_cli_v2_passes_the_version_check(tmp_path):
    # The stub then fails the credential check, which proves the version check was passed.
    result = run_deploy(tmp_path, "aws-cli/2.34.16 Python/3.13.12 Darwin/25.6.0 source/arm64")
    assert result.returncode != 0
    assert "No working AWS credentials" in result.stdout


def test_version_check_does_not_pipe_into_grep_q():
    # Under `set -o pipefail`, `cmd | grep -q` can fail spuriously when grep exits before cmd finishes writing.
    assert "| grep -q" not in DEPLOY.read_text(encoding="utf-8")
