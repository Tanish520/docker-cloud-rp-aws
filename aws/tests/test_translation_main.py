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

    def cursor(self):
        return self

    def commit(self):
        pass

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
                       "DB_USER": "dbadmin", "DB_PASSWORD": "secret", "TM_STORE": "sql"}.items():
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
    def __init__(self, memory_rows=()):
        self.saved, self.queries = [], []
        self.memory_rows = list(memory_rows)       # (SourceHash, TranslatedText) the database holds
        self.fast_executemany = False

    def execute(self, query, params):
        self.queries.append((query, params))

    def fetchall(self):
        return self.memory_rows

    def executemany(self, query, rows):
        self.saved.extend(rows)


class RecordingConn:
    def __init__(self, memory_rows=()):
        self.cursor_ = RecordingCursor(memory_rows)

    def cursor(self):
        return self.cursor_

    def commit(self):
        pass


def xlf_with(tmp_path, sources, name="kde4.es-ES.xlf"):
    units = "".join(f'<trans-unit id="S{i}"><source>{text}</source><target /></trans-unit>' for i, text in enumerate(sources))
    path = tmp_path / name
    path.write_text(f"<xliff version=\"1.2\"><file><body>{units}</body></file></xliff>", encoding="utf-8")
    return path


def targets(path):
    import xml.etree.ElementTree as ET
    return [unit.find("target").text for unit in ET.parse(path).getroot().iter("trans-unit")]


def test_file_is_translated_in_chunks_of_similar_length(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, lambda s: s.upper())
    monkeypatch.setattr(translation, "CHUNK_SIZE", 2)
    monkeypatch.setattr(translation, "ENGINE_WORKERS", 1)                     # one batch per chunk, to see the chunks
    path = xlf_with(tmp_path, ["c c c", "a", "d d d d", "b b"])

    asyncio.run(translation.process_xlf_file(str(path), "es-ES", translation.SqlMemory(RecordingConn()), None))

    assert [sources for sources, _, _ in model.batches] == [["a", "b b"], ["c c c", "d d d d"]]
    assert targets(path) == ["C C C", "A", "D D D D", "B B"]


def test_memory_is_keyed_by_a_hash_of_the_text(translation):
    key = translation.tm_key("Open %1")
    assert len(key) == 32 and key == translation.tm_key("Open %1") and key != translation.tm_key("Open %2")


def test_long_strings_are_looked_up_and_saved_by_their_hash(translation, monkeypatch, tmp_path):
    use_model(translation, monkeypatch, lambda s: s.upper())
    conn = RecordingConn()
    long_text = " ".join(["word"] * 150)                                      # 749 characters
    path = xlf_with(tmp_path, ["short", long_text])

    asyncio.run(translation.process_xlf_file(str(path), "es-ES", translation.SqlMemory(conn), None))

    [(query, params)] = conn.cursor_.queries
    assert params == ["es-ES", translation.engine_version()]
    saved = {saved_source(row): row for row in conn.cursor_.saved}
    assert set(saved) == {"short", long_text}
    key, version = translation.tm_key(long_text), translation.engine_version()
    assert saved[long_text] == (key, "es-ES", version, long_text.upper(), key, long_text, "es-ES", version, long_text.upper(), "nllb-600M-ct2")


def test_output_with_a_character_the_model_cannot_write_is_dropped(translation, monkeypatch):
    # NLLB lacks some CJK characters and writes <unk>; decoding deleted it, leaving "" or a word with a hole.
    rules = {"Candle": "<unk>", "Crab claws": "<unk> 爪", "Open": "打开"}
    use_model(translation, monkeypatch, lambda s: rules[s])

    translations, _, _ = translation.translate_local(["Candle", "Crab claws", "Open"], "zh-CN")

    assert translations == ["", "", "打开"]


def test_untranslatable_strings_stay_empty_and_are_remembered_and_reported(translation, monkeypatch, tmp_path, capsys):
    use_model(translation, monkeypatch, lambda s: {"crab": "<unk>", "Open": "打开"}[s])
    conn = RecordingConn()
    path = xlf_with(tmp_path, ["crab", "Open"])

    asyncio.run(translation.process_xlf_file(str(path), "zh-CN", translation.SqlMemory(conn), None))

    assert targets(path) == [None, "打开"]                    # Phase 3 keeps the English for an empty target
    saved = {saved_source(row): row for row in conn.cursor_.saved}
    assert saved["Open"][3] == "打开"
    assert saved["crab"][3] == "" and saved["crab"][-1] == "nllb-600M-ct2+english"   # remembered as kept in English
    assert "1 string(s) kept in English" in capsys.readouterr().out


def saved_source(row):
    # MERGE parameters: (key, lang, version, text, key, source, lang, version, text, model)
    return row[5]


def test_engine_version_names_the_model_and_every_setting_that_changes_output(translation, monkeypatch):
    version = translation.engine_version()
    for part in ("facebook/nllb-200-distilled-600M", "ct2-int8", "beam=2", "cap=2x+10", "placeholders=square", "unk=english"):
        assert part in version
    monkeypatch.setattr(translation, "BEAM_SIZE", 5)
    assert "beam=5" in translation.engine_version()        # a new setting never reuses old translations


def test_a_language_s_memory_is_loaded_once_per_run(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, lambda s: s.upper())
    conn = RecordingConn(memory_rows=[(translation.tm_key("Open"), "Open", "Öffnen", "nllb-600M-ct2")])
    first = xlf_with(tmp_path, ["Open", "Close"], "a.de-DE.xlf")
    second = xlf_with(tmp_path, ["Open", "Close", "Save"], "b.de-DE.xlf")

    asyncio.run(translation.process_xlf_file(str(first), "de-DE", translation.SqlMemory(conn), None))
    stats, *_ = asyncio.run(translation.process_xlf_file(str(second), "de-DE", translation.SqlMemory(conn), None))

    [(query, params)] = conn.cursor_.queries                         # one database query for both files
    assert "SELECT SourceHash, SourceText, TranslatedText, ModelName" in query and "TargetLang=? AND EngineVersion=?" in query
    assert params == ["de-DE", translation.engine_version()]
    assert targets(first) == ["Öffnen", "CLOSE"]
    assert targets(second) == ["Öffnen", "CLOSE", "SAVE"]             # "Close" reused from the first file
    assert model.seen == ["Close", "Save"]
    assert stats["extra"]["tm_db_queries"] == 0 and stats["extra"]["tm_hits_in_run"] == 1


def test_file_reports_lookup_and_real_token_counts(translation, monkeypatch, tmp_path):
    use_model(translation, monkeypatch, lambda s: s + " x")
    monkeypatch.setattr(translation, "TOKEN_LIMIT", 10)
    monkeypatch.setattr(translation, "ENGINE_WORKERS", 1)                     # keep one batch to check padding
    path = xlf_with(tmp_path, ["a", "b b b"])

    stats, *_ = asyncio.run(translation.process_xlf_file(str(path), "es-ES", translation.SqlMemory(RecordingConn()), None))

    extra = stats["extra"]
    assert extra["tm_db_queries"] == 1 and extra["tm_lookup_ms"] >= 0
    assert extra["model_strings"] == 2
    assert extra["real_input_tokens"] == 4 and extra["real_output_tokens"] == 6     # "a x" + "b b b x"
    assert extra["padding_pct"] == 33.33                                      # one batch [1, 3]: 2 of 6 positions padded


def german(text):
    return {"20 Cards": "20 Karten", "8 Cards": "8 Karten (model)", "Border": "Rahmen", "Border:": "Rahmen (model):",
            "Open": "Öffnen"}[text]


def test_a_twin_of_a_memory_entry_is_adapted_without_the_model(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, german)
    conn = RecordingConn(memory_rows=[(translation.tm_key("20 Cards"), "20 Cards", "20 Karten", "nllb-600M-ct2")])
    path = xlf_with(tmp_path, ["8 Cards", "Open"], "a.de-DE.xlf")

    stats, *_ = asyncio.run(translation.process_xlf_file(str(path), "de-DE", translation.SqlMemory(conn), None))

    assert targets(path) == ["8 Karten", "Öffnen"]
    assert model.seen == ["Open"]
    assert stats["extra"]["tm_twin_hits"] == 1
    saved = {saved_source(row): row[-1] for row in conn.cursor_.saved}
    assert saved == {"8 Cards": "nllb-600M-ct2+twin", "Open": "nllb-600M-ct2"}


def test_twins_in_one_file_go_to_the_model_once(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, german)
    path = xlf_with(tmp_path, ["20 Cards", "Border", "8 Cards", "Border:"], "a.de-DE.xlf")

    stats, *_ = asyncio.run(translation.process_xlf_file(str(path), "de-DE", translation.SqlMemory(RecordingConn()), None))

    assert sorted(model.seen) == ["20 Cards", "Border"]
    assert targets(path) == ["20 Karten", "Rahmen", "8 Karten", "Rahmen:"]
    assert stats["extra"]["tm_twin_hits"] == 2


def test_a_twin_that_cannot_be_adapted_goes_to_the_model(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, lambda s: {"20 Cards": "٢٠ بطاقة", "8 Cards": "٨ بطاقات"}[s])
    path = xlf_with(tmp_path, ["20 Cards", "8 Cards"], "a.ar-SA.xlf")

    stats, *_ = asyncio.run(translation.process_xlf_file(str(path), "ar-SA", translation.SqlMemory(RecordingConn()), None))

    assert sorted(model.seen) == ["20 Cards", "8 Cards"]
    assert targets(path) == ["٢٠ بطاقة", "٨ بطاقات"]
    assert stats["extra"]["tm_twin_hits"] == 0


def test_twin_reuse_can_be_switched_off(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, german)
    monkeypatch.setattr(translation, "TWIN_REUSE", False)
    path = xlf_with(tmp_path, ["20 Cards", "8 Cards"], "a.de-DE.xlf")

    asyncio.run(translation.process_xlf_file(str(path), "de-DE", translation.SqlMemory(RecordingConn()), None))

    assert sorted(model.seen) == ["20 Cards", "8 Cards"]


def test_no_pause_between_files_without_an_azure_exporter(translation, run_env, s3, monkeypatch):
    # The 2 s pause let Azure's log exporter flush; on AWS it only made every run 2 s per file longer.
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    pauses = []

    async def record_sleep(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(translation.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: (FakeConn(), 0))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

    assert run(translation, run_env) == 0
    assert pauses == []


def test_a_remembered_kept_in_english_string_skips_the_model(translation, monkeypatch, tmp_path, capsys):
    # Same engine, same <unk>: retrying it only wasted model time (a third of a small commit's work).
    model = use_model(translation, monkeypatch, lambda s: {"Open": "打开"}[s])
    conn = RecordingConn(memory_rows=[(translation.tm_key("crab"), "crab", "", "nllb-600M-ct2+english")])
    path = xlf_with(tmp_path, ["crab", "Open"], "a.zh-CN.xlf")

    stats, *_ = asyncio.run(translation.process_xlf_file(str(path), "zh-CN", translation.SqlMemory(conn), None))

    assert model.seen == ["Open"]
    assert targets(path) == [None, "打开"]
    assert stats["extra"]["tm_kept_english"] == 1
    assert "1 string(s) kept in English (remembered" in capsys.readouterr().out


def test_kept_in_english_entries_are_never_adapted_as_twins(translation, monkeypatch, tmp_path):
    model = use_model(translation, monkeypatch, lambda s: {"crab 2": "螃蟹 2"}[s])
    conn = RecordingConn(memory_rows=[(translation.tm_key("crab 1"), "crab 1", "", "nllb-600M-ct2+english")])
    path = xlf_with(tmp_path, ["crab 2"], "a.zh-CN.xlf")

    asyncio.run(translation.process_xlf_file(str(path), "zh-CN", translation.SqlMemory(conn), None))

    assert model.seen == ["crab 2"]


def test_a_small_call_is_split_so_every_engine_worker_gets_a_batch(translation, monkeypatch):
    # A small commit gives ~30 new strings per language: one batch, so one of the two workers sat idle.
    model = use_model(translation, monkeypatch, lambda s: s.upper())

    translations, _, _ = translation.translate_local(["a b", "c d e f", "g h", "i j k l"], "es-ES")

    assert [sources for sources, _, _ in model.batches] == [["a b", "g h"], ["c d e f", "i j k l"]]
    assert translations == ["A B", "C D E F", "G H", "I J K L"]


def test_a_large_call_keeps_full_batches(translation, monkeypatch):
    model = use_model(translation, monkeypatch, lambda s: s)
    monkeypatch.setattr(translation, "TOKEN_LIMIT", 4)

    translation.translate_local(["a b", "c d", "e f", "g h", "i j", "k l"], "es-ES")      # 12 tokens > 2 x 4

    assert [len(sources) for sources, _, _ in model.batches] == [2, 2, 2]


def s3_memory(translation, s3, run_id):
    import memory_store
    return memory_store.S3Memory(s3, BUCKET, run_id, translation.engine_version())


def test_translations_saved_to_s3_are_found_by_the_next_run(translation, monkeypatch, tmp_path, s3):
    model = use_model(translation, monkeypatch, lambda s: s.upper())
    first_file = xlf_with(tmp_path, ["Open", "8 Cards"], "a.de-DE.xlf")
    first = s3_memory(translation, s3, "run-1")
    asyncio.run(translation.process_xlf_file(str(first_file), "de-DE", first, None))
    first.close()

    translation.memory.clear()                                    # a new run starts with an empty dictionary
    translation.twins.clear()
    model.seen.clear()
    second_file = xlf_with(tmp_path, ["Open", "20 Cards"], "b.de-DE.xlf")
    stats, *_ = asyncio.run(translation.process_xlf_file(str(second_file), "de-DE", s3_memory(translation, s3, "run-2"), None))

    assert model.seen == []                                       # exact hit + twin, both from S3
    assert targets(second_file) == ["OPEN", "20 CARDS"]
    assert stats["extra"]["tm_twin_hits"] == 1


def test_s3_memory_needs_no_database(translation, run_env, s3, monkeypatch):
    import memory_store
    monkeypatch.setenv("TM_STORE", "s3")
    put_raw(s3, "resx_file_10.es-ES.xlf")
    monkeypatch.setattr(translation, "get_sql_connection", lambda conn_str: pytest.fail("no database in S3 mode"))
    seen = []

    async def processor(local_path, target_lang, memory, exporter):
        seen.append(memory)
        memory.save([memory_store.Entry(translation.tm_key("Hello"), target_lang, "Hello", "Hola", translation.MODEL_NAME)])
        translation.profiler.add_segment_stats(tm_hit=False)
        return {"total_api_time_ms": 1.0, "total_sql_time_ms": 1.0, "total_commit_time_ms": 1.0}, 1.0, 1.0, 5, 4

    monkeypatch.setattr(translation, "process_xlf_file", processor)

    assert run(translation, run_env) == 0
    [memory] = seen
    assert isinstance(memory, memory_store.S3Memory)
    assert as_rows(memory_store.S3Memory(s3, BUCKET, "run-2", translation.engine_version()).load("es-ES")) == {"Hello": "Hola"}


def as_rows(rows):
    return {source: translation for _, source, translation, _ in rows}


class ListMemory:
    """A memory store that returns its entries in a given order."""

    def __init__(self, rows):
        self.rows = rows

    def load(self, target_lang):
        return list(self.rows)


def test_twin_source_does_not_depend_on_the_order_the_store_returns_entries(translation):
    # SQL happened to return entries sorted by hash and S3 in file order; the twin source must be the same.
    rows = [(translation.tm_key(s), s, t, translation.MODEL_NAME) for s, t in (("20 Cards", "20 Karten"), ("8 Cards", "8 Karten"))]

    chosen = []
    for order in (rows, rows[::-1]):
        translation.memory.clear()
        translation.twins.clear()
        translation.reset_file_extra()
        translation.load_memory(ListMemory(order), "de-DE")
        chosen.append(translation.twins["de-DE"][translation.template_of("5 Cards")])

    assert chosen[0] == chosen[1]


def test_the_s3_memory_of_every_language_in_the_run_is_prefetched(translation, run_env, s3, monkeypatch):
    import memory_store
    monkeypatch.setenv("TM_STORE", "s3")
    put_raw(s3, "resx_file_10.es-ES.xlf")
    put_raw(s3, "resx_file_10.fr-FR.xlf")
    prefetched = []
    monkeypatch.setattr(memory_store.S3Memory, "prefetch", lambda self, langs: prefetched.extend(langs))
    monkeypatch.setattr(translation, "process_xlf_file", fake_processor(translation))

    assert run(translation, run_env) == 0
    assert sorted(prefetched) == ["es-ES", "fr-FR"]
