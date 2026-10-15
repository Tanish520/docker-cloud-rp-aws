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
