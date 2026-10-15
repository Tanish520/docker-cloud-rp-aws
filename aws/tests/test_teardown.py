"""Run infra/teardown.sh against a stand-in aws command that logs its calls."""
import subprocess

import pytest

from helpers import AWS_DIR

TEARDOWN = AWS_DIR / "infra" / "teardown.sh"

AWS_STUB = r"""#!/bin/bash
echo "aws $*" >> "$STUB_LOG"
args="$*"
case "$args" in
  "cloudformation describe-stacks"*"--query"*DataBucketName*) echo "data-bucket" ;;
  "cloudformation describe-stacks"*"--query"*ArtifactBucketName*) echo "artifact-bucket" ;;
  "cloudformation describe-stacks"*) if [ -n "$STUB_NO_STACK" ]; then exit 255; fi ;;
  "s3api head-bucket"*)
    for b in $STUB_MISSING_BUCKETS; do
      case "$args" in *"--bucket $b "*) exit 254 ;; esac
    done ;;
  "s3api list-multipart-uploads"*"--bucket data-bucket "*) printf '%b\n' "${STUB_UPLOADS:-None}" ;;
  "s3api list-multipart-uploads"*) echo "None" ;;
  "s3 rm"*)
    for b in $STUB_MISSING_BUCKETS; do
      case "$args" in *"s3://$b"*) exit 1 ;; esac
    done ;;
esac
exit 0
"""


@pytest.fixture
def teardown(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "aws"
    stub.write_text(AWS_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "calls.log"

    def run(**overrides):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_LOG": str(log)}
        env.update(overrides)
        result = subprocess.run(["bash", str(TEARDOWN), "--yes"], env=env, capture_output=True, text=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    return run


def test_already_deleted_buckets_are_skipped_and_the_stack_is_still_deleted(teardown):
    result, calls = teardown(STUB_MISSING_BUCKETS="data-bucket artifact-bucket")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(c.startswith("aws s3 rm") for c in calls)
    assert any(c.startswith("aws cloudformation delete-stack") for c in calls)


def test_unfinished_uploads_are_aborted_before_the_bucket_is_emptied(teardown):
    result, calls = teardown(STUB_UPLOADS="models/key/model.bin\tUPLOAD1")
    assert result.returncode == 0, result.stdout + result.stderr
    abort = next(i for i, c in enumerate(calls) if c.startswith("aws s3api abort-multipart-upload"))
    assert "--key models/key/model.bin" in calls[abort] and "--upload-id UPLOAD1" in calls[abort]
    assert abort < next(i for i, c in enumerate(calls) if c.startswith("aws s3 rm s3://data-bucket"))


def test_missing_stack_is_a_no_op(teardown):
    result, calls = teardown(STUB_NO_STACK="1")
    assert result.returncode == 0
    assert not any("delete-stack" in c for c in calls)
