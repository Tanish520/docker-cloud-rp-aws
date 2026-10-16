"""Run scripts/run_phase2.sh against stand-in docker/python/aws commands that log their calls."""
import os
import subprocess

import pytest

from helpers import AWS_DIR

RUNNER = AWS_DIR / "scripts" / "run_phase2.sh"

STUBS = {
    "docker": r"""#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
args="$*"
case "$args" in
  *head_bucket*)
    if [ "${STUB_PROBE:-ok}" = "fail" ]; then exit 1; fi
    if [ "${STUB_PROBE:-ok}" = "needs-keys" ] && [[ "$args" != *AWS_ACCESS_KEY_ID* ]]; then exit 1; fi
    exit 0 ;;
  *translation.py*)
    echo "translated"
    exit "${STUB_TRANSLATE_EXIT:-0}" ;;
esac
exit 0
""",
    "python": r"""#!/bin/bash
echo "python $*" >> "$STUB_LOG"
exit 0
""",
    "aws": r"""#!/bin/bash
echo "aws $*" >> "$STUB_LOG"
if [ "$1 $2" = "configure export-credentials" ]; then
  echo "export AWS_ACCESS_KEY_ID=AKIDTEST"
  echo "export AWS_SECRET_ACCESS_KEY=secret"
  echo "export AWS_SESSION_TOKEN=token"
fi
""",
}


@pytest.fixture
def run_phase2(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "aws" / "translator").mkdir(parents=True)
    (repo / "aws" / "config.json").write_text("{}", encoding="utf-8")
    log = tmp_path / "calls.log"

    def run(**overrides):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_LOG": str(log),
               "BUCKET": "bucket", "RUN_ID": "run-1", "DB_HOST": "db", "DB_USER": "user",
               "DB_PASSWORD": "pw", "MODEL_KEY": "model-key"}
        env.update(overrides)
        result = subprocess.run(["bash", str(RUNNER)], cwd=repo, env=env, capture_output=True, text=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    return run


def first(calls, needle):
    return next(i for i, call in enumerate(calls) if needle in call)


def translation_call(calls):
    [call] = [c for c in calls if "translation.py" in c]
    return call


def test_probe_runs_before_model_work_and_uses_the_role_endpoint(run_phase2):
    result, calls = run_phase2()
    assert result.returncode == 0, result.stdout + result.stderr
    assert first(calls, "head_bucket") < first(calls, "model_cache.py fetch")
    assert not any("export-credentials" in c for c in calls)
    call = translation_call(calls)
    assert "--cpuset-cpus=0,1" in call
    assert "AWS_ACCESS_KEY_ID" not in call


def test_probe_failure_falls_back_to_short_lived_keys(run_phase2):
    result, calls = run_phase2(STUB_PROBE="needs-keys")
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c == "aws configure export-credentials --format env" for c in calls)
    assert "-e AWS_ACCESS_KEY_ID" in translation_call(calls)


def test_unreachable_s3_stops_before_any_model_or_translation_work(run_phase2):
    result, calls = run_phase2(STUB_PROBE="fail")
    assert result.returncode != 0
    assert not any("model_cache.py" in c for c in calls)
    assert not any("translation.py" in c for c in calls)


def test_translation_failure_fails_the_script_despite_tee(run_phase2):
    result, _ = run_phase2(STUB_TRANSLATE_EXIT="7")
    assert result.returncode != 0


def test_bench_task_skips_the_database_and_uploads_benchmark_results(run_phase2):
    result, calls = run_phase2(PHASE2_TASK="bench")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("db_admin.py" in c or "translation.py" in c for c in calls)
    [bench] = [c for c in calls if "batching_bench.py" in c]
    assert "--cpuset-cpus=0,1" in bench and "/workload:ro" in bench
    assert any(c.startswith("aws s3 cp") and "s3://bucket/runs/run-1/bench/results.json" in c for c in calls)


def test_bench_task_passes_benchmark_arguments_into_the_container(run_phase2):
    result, calls = run_phase2(PHASE2_TASK="bench", BENCH_ARGS="--kde4 s3://bucket/datasets/kde4.xlsx")
    assert result.returncode == 0, result.stdout + result.stderr
    [bench] = [c for c in calls if "batching_bench.py" in c]
    assert "-e BENCH_ARGS" in bench and "$BENCH_ARGS" in bench


def test_translator_gets_the_chosen_config(run_phase2, tmp_path):
    (tmp_path / "repo" / "aws" / "config.kde4.json").write_text('{"InputFiles": ["kde4.resx"]}', encoding="utf-8")
    result, _ = run_phase2(PIPELINE_CONFIG="aws/config.kde4.json")
    assert result.returncode == 0, result.stdout + result.stderr
    copied = tmp_path / "repo" / "aws" / "translator" / "config.json"
    assert copied.read_text(encoding="utf-8") == '{"InputFiles": ["kde4.resx"]}'
