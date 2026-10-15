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
