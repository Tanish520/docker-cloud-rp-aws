import asyncio
import json
import types

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


def fake_processor(translation, fail_for=(), segments=1, failed_batches=0):
    """Stands in for process_xlf_file, recording segments and swallowed batch failures like the real one."""
    async def process(local_path, target_lang, conn, exporter):
        if target_lang in fail_for:
            raise RuntimeError("inference failed")
        for _ in range(segments):
            translation.profiler.add_segment_stats(tm_hit=False)
        translation.profiler.counters["api_failures"] += failed_batches
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
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

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
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation, fail_for={"fr-FR"}))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.fr-FR.xlf"]
    assert keys(s3, "translated") == ["resx_file_10.es-ES.xlf"]


def test_retry_after_partial_failure_only_redoes_failed_file(translation, run_env, s3, monkeypatch):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation, fail_for={"fr-FR"}))
    assert run(translation, run_env) == 1

    seen = []
    succeed = fake_processor(translation)

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
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.es-ES.xlf"]
    assert keys(s3, "translated") == []


def test_missing_bucket_setting_fails(translation, run_env, monkeypatch):
    monkeypatch.delenv("BUCKET")
    assert run(translation, run_env) == 1


def test_swallowed_inference_failure_fails_the_file(translation, run_env, s3, monkeypatch, capsys):
    # translate_local catches CTranslate2 errors and only bumps api_failures; that must not pass as success.
    put_raw(s3, "resx_file_10.es-ES.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation, failed_batches=1))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.es-ES.xlf"]
    assert keys(s3, "translated") == []
    assert research_payloads(capsys.readouterr().out) == []


def test_file_with_no_translatable_segments_fails(translation, run_env, s3, monkeypatch):
    # process_xlf_file returns zeros for an unreadable XLIFF; an empty result must not count as translated.
    put_raw(s3, "resx_file_10.es-ES.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation, segments=0))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.es-ES.xlf"]
    assert keys(s3, "translated") == []


def flaky_store(fail_download=(), fail_delete=()):
    from s3_store import RunStore

    class FlakyStore(RunStore):
        def download_raw(self, name, local_path):
            if name in fail_download:
                raise RuntimeError("S3 download timed out")
            return super().download_raw(name, local_path)

        def delete_raw(self, name):
            if name in fail_delete:
                raise RuntimeError("S3 delete failed")
            return super().delete_raw(name)

    return FlakyStore


def test_download_error_on_one_file_does_not_stop_the_others(translation, run_env, s3, monkeypatch):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    monkeypatch.setattr(translation, "RunStore", flaky_store(fail_download={"resx_file_10.es-ES.xlf"}))
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

    assert run(translation, run_env) == 1
    assert keys(s3, "raw") == ["resx_file_10.es-ES.xlf"]
    assert keys(s3, "translated") == ["resx_file_10.fr-FR.xlf"]


def test_delete_error_after_success_keeps_the_translation(translation, run_env, s3, monkeypatch, capsys):
    put_raw(s3, "resx_file_10.es-ES.xlf")
    monkeypatch.setattr(translation, "RunStore", flaky_store(fail_delete={"resx_file_10.es-ES.xlf"}))
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

    assert run(translation, run_env) == 0
    assert keys(s3, "translated") == ["resx_file_10.es-ES.xlf"]
    assert len(research_payloads(capsys.readouterr().out)) == 1


def test_metric_views_are_registered_without_an_exporter(translation, run_env, s3, monkeypatch):
    # Azure records into registered OpenCensus views; AWS must do the same in-process work for a comparable overhead.
    calls = []
    monkeypatch.setattr(translation, "register_metrics_views", lambda exporter: calls.append(exporter))
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))
    put_raw(s3, "resx_file_10.es-ES.xlf")

    assert run(translation, run_env) == 0
    assert calls == [None]


def test_run_with_no_files_at_all_fails(translation, run_env, s3, monkeypatch):
    # Nothing to translate and nothing translated: Phase 1 did not run, or RUN_ID is wrong.
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    assert run(translation, run_env) == 1


def test_retry_after_full_success_with_nothing_left_passes(translation, run_env, s3, monkeypatch):
    s3.put_object(Bucket=BUCKET, Key="runs/run-1/translated/resx_file_10.es-ES.xlf", Body=XLF.encode("utf-8"))
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    assert run(translation, run_env) == 0


class WordTokenizer:
    """Splits on spaces so a fake model can work on whole words."""
    src_lang = None

    def encode(self, text, **kwargs):
        return text.split()

    def convert_ids_to_tokens(self, ids):
        return list(ids)

    def convert_tokens_to_ids(self, tokens):
        return list(tokens)

    def decode(self, ids, skip_special_tokens=False):
        return " ".join(ids)


class FakeModel:
    """Translates with `rule(source text)`; records every text and every batch it was given."""

    def __init__(self, rule):
        self.rule, self.seen, self.batches = rule, [], []

    def translate_batch(self, batch, target_prefix, **options):
        self.batches.append(([" ".join(tokens) for tokens in batch], target_prefix, options))
        results = []
        for tokens, prefix in zip(batch, target_prefix):
            source = " ".join(tokens)
            self.seen.append(source)
            result = types.SimpleNamespace(hypotheses=[prefix + self.rule(source).split()])
            results.append(types.SimpleNamespace(result=lambda r=result: r) if options.get("asynchronous") else result)
        return results


def use_model(translation, monkeypatch, rule):
    model = FakeModel(rule)
    monkeypatch.setattr(translation, "tokenizer", WordTokenizer())
    monkeypatch.setattr(translation, "translator", model)
    return model


def test_placeholders_are_masked_for_the_model_and_put_back(translation, monkeypatch):
    model = use_model(translation, monkeypatch, lambda s: s.replace("Open", "Ouvrir").replace("now", "maintenant"))

    translations, _, _ = translation.translate_local(["Open %1 now", "Open now"], "fr-FR")

    assert sorted(model.seen) == ["Open [0] now", "Open now"]
    assert translations == ["Ouvrir %1 maintenant", "Ouvrir maintenant"]


def test_a_lost_marker_falls_back_to_the_unmasked_translation(translation, monkeypatch):
    # The model drops "[1]"; that one string is translated again as written, so %2 is not lost.
    model = use_model(translation, monkeypatch, lambda s: s.replace(" [1]", "").replace("Copy", "Kopiere"))

    translations, _, _ = translation.translate_local(["Copy [3] %1 to %2", "Copy %1 to %2"], "de-DE")

    assert sorted(model.seen[:2]) == ["Copy [0] to [1]", "Copy [3] %1 to %2"]
    assert model.seen[2:] == ["Copy %1 to %2"]
    assert translations == ["Kopiere [3] %1 to %2", "Kopiere %1 to %2"]


def test_model_works_on_two_batches_at_once_one_per_core(translation):
    assert translation.translator.kwargs == {"device": "auto", "compute_type": "int8",
                                             "inter_threads": 2, "intra_threads": 1}


def test_strings_are_batched_shortest_first_and_queued_together(translation, monkeypatch):
    model = use_model(translation, monkeypatch, lambda s: s.upper())
    monkeypatch.setattr(translation, "TOKEN_LIMIT", 5)

    texts = ["c c c", "a", "d d d d", "b b"]
    translations, _, _ = translation.translate_local(texts, "es-ES")

    assert [sources for sources, _, _ in model.batches] == [["a", "b b"], ["c c c"], ["d d d d"]]
    assert [options for _, _, options in model.batches] == [
        {"beam_size": 2, "max_decoding_length": 2 * 2 + 10, "asynchronous": True},
        {"beam_size": 2, "max_decoding_length": 2 * 3 + 10, "asynchronous": True},
        {"beam_size": 2, "max_decoding_length": 2 * 4 + 10, "asynchronous": True},
    ]
    assert translations == ["C C C", "A", "D D D D", "B B"]          # back in the caller's order


def test_engine_batches_are_counted(translation, monkeypatch):
    use_model(translation, monkeypatch, lambda s: s)
    monkeypatch.setattr(translation, "TOKEN_LIMIT", 2)
    translation.profiler.counters["total_batches"] = 0

    translation.translate_local(["a b", "c d", "e"], "es-ES")

    assert translation.profiler.counters["total_batches"] == 3


def test_punjabi_targets_its_nllb_language(translation, monkeypatch):
    model = use_model(translation, monkeypatch, lambda s: s)
    translation.translate_local(["Open"], "pa-IN")
    assert model.batches[0][1] == [["pan_Guru"]]


class RecordingCursor:
    def __init__(self):
        self.saved = []
        self.fast_executemany = False

    def execute(self, query, params):
        pass

    def fetchall(self):
        return []                                   # empty translation memory

    def executemany(self, query, rows):
        self.saved.extend(row[0] for row in rows)


class RecordingConn:
    def __init__(self):
        self.cursor_ = RecordingCursor()

    def cursor(self):
        return self.cursor_

    def commit(self):
        pass


def xlf_with(tmp_path, sources):
    units = "".join(f'<trans-unit id="S{i}"><source>{text}</source><target /></trans-unit>' for i, text in enumerate(sources))
    path = tmp_path / "kde4.es-ES.xlf"
    path.write_text(f"<xliff version=\"1.2\"><file><body>{units}</body></file></xliff>", encoding="utf-8")
    return path


def targets(path):
    import xml.etree.ElementTree as ET
    return [unit.find("target").text for unit in ET.parse(path).getroot().iter("trans-unit")]


def test_file_is_translated_in_chunks_of_similar_length(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, lambda s: s.upper())
    monkeypatch.setattr(translation, "CHUNK_SIZE", 2)
    path = xlf_with(tmp_path, ["c c c", "a", "d d d d", "b b"])

    asyncio.run(translation.process_xlf_file(str(path), "es-ES", RecordingConn(), None))

    assert [sources for sources, _, _ in model.batches] == [["a", "b b"], ["c c c", "d d d d"]]
    assert targets(path) == ["C C C", "A", "D D D D", "B B"]


def test_strings_too_long_for_the_memory_key_are_translated_but_not_saved(translation, monkeypatch, tmp_path):
    use_model(translation, monkeypatch, lambda s: s.upper())
    monkeypatch.setattr(translation, "TM_KEY_LIMIT", 5)
    conn = RecordingConn()
    path = xlf_with(tmp_path, ["short", "much longer"])

    asyncio.run(translation.process_xlf_file(str(path), "es-ES", conn, None))

    assert targets(path) == ["SHORT", "MUCH LONGER"]
    assert conn.cursor_.saved == ["short"]
