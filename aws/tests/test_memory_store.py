"""Translation memory as per-language files in S3: one base file plus one file per run."""
import gzip
import hashlib
import json

import pytest

import memory_store as ms
from helpers import BUCKET

ENGINE = "facebook/nllb-200-distilled-600M|ct2-int8|beam=2|cap=2x+10|placeholders=square|unk=english"


def key(text):
    return hashlib.sha256(text.encode("utf-8")).digest()


def entry(source, translation, lang="de-DE", model="nllb-600M-ct2"):
    return ms.Entry(key(source), lang, source, translation, model)


def memory(s3, run_id="run-1", engine=ENGINE):
    return ms.S3Memory(s3, BUCKET, run_id, engine)


def as_dict(rows):
    return {source: (translation, model) for _, source, translation, model in rows}


def test_saved_entries_are_found_by_the_next_run(s3):
    first = memory(s3, "run-1")
    first.save([entry("Open", "Öffnen"), entry("Close", "Schließen")])
    first.commit()
    first.close()

    rows = memory(s3, "run-2").load("de-DE")

    assert as_dict(rows) == {"Open": ("Öffnen", "nllb-600M-ct2"), "Close": ("Schließen", "nllb-600M-ct2")}
    assert {k for k, *_ in rows} == {key("Open"), key("Close")}


def test_entries_wait_for_commit_and_languages_are_kept_apart(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen"), entry("Open", "Ouvrir", lang="fr-FR")])
    assert memory(s3, "run-2").load("de-DE") == []
    run.commit()
    run.close()

    assert as_dict(memory(s3, "run-2").load("fr-FR")) == {"Open": ("Ouvrir", "nllb-600M-ct2")}


def test_another_engine_version_starts_empty(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()

    assert memory(s3, "run-2", engine=ENGINE.replace("beam=2", "beam=5")).load("de-DE") == []


def test_two_runs_writing_at_the_same_time_lose_nothing(s3):
    # Each run writes its own file, so neither can overwrite the other.
    a, b = memory(s3, "run-a"), memory(s3, "run-b")
    a.save([entry("Open", "Öffnen")])
    b.save([entry("Close", "Schließen")])
    a.commit()
    b.commit()
    a.close()
    b.close()

    assert set(as_dict(memory(s3, "run-c").load("de-DE"))) == {"Open", "Close"}


def test_a_later_run_overrides_an_earlier_one(s3):
    for run_id, translation in (("run-1", "Öffne"), ("run-2", "Öffnen")):
        run = memory(s3, run_id)
        run.save([entry("Open", translation)])
        run.close()

    assert as_dict(memory(s3, "run-3").load("de-DE"))["Open"][0] == "Öffnen"


def test_kept_in_english_entries_round_trip(s3):
    run = memory(s3)
    run.save([entry("crab", "", lang="zh-CN", model="nllb-600M-ct2+english")])
    run.close()

    assert as_dict(memory(s3, "run-2").load("zh-CN")) == {"crab": ("", "nllb-600M-ct2+english")}


def test_each_commit_of_a_run_writes_a_new_file(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()
    run.save([entry("Close", "Schließen")])
    run.commit()
    run.close()

    assert len(run_files(s3, "de-DE")) == 2
    assert set(as_dict(memory(s3, "run-2").load("de-DE"))) == {"Open", "Close"}


def run_files(s3, lang):
    prefix = f"memory/{ms.engine_key(ENGINE)}/{lang}/runs/"
    return [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])]


def bases(s3, lang):
    prefix = f"memory/{ms.engine_key(ENGINE)}/{lang}/base-"
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", []))


def fill_runs(s3, count, start=0):
    for i in range(start, start + count):
        run = memory(s3, f"run-{i:03d}")
        run.save([entry(f"String {i}", f"Zeile {i}")])
        run.close()


def test_many_run_files_are_merged_into_a_new_base(s3, monkeypatch):
    monkeypatch.setattr(ms, "COMPACT_AFTER", 3)
    fill_runs(s3, 4)

    rows = memory(s3, "reader").load("de-DE")

    assert len(rows) == 4 and bases(s3, "de-DE")[-1].endswith("base-000001.jsonl.gz")
    assert len(memory(s3, "reader-2").load("de-DE")) == 4        # read back from the base


def test_run_files_already_in_an_older_base_are_removed(s3, monkeypatch):
    monkeypatch.setattr(ms, "COMPACT_AFTER", 3)
    fill_runs(s3, 4)
    memory(s3, "reader").load("de-DE")                           # base 1 holds runs 0-3
    fill_runs(s3, 4, start=4)

    rows = memory(s3, "reader-2").load("de-DE")                  # base 2 holds runs 0-7

    assert len(rows) == 8
    assert len(run_files(s3, "de-DE")) == 4                      # runs 0-3 were already in base 1
    assert len(memory(s3, "reader-3").load("de-DE")) == 8


class LosesMergeRace:
    """The S3 client of a run whose base write is refused because another run created that base first."""

    def __init__(self, client):
        self.client = client

    def put_object(self, **kwargs):
        if "/base-" in kwargs["Key"]:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "PreconditionFailed", "Message": "At least one of the pre-conditions you specified did not hold"}}, "PutObject")
        return self.client.put_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def test_a_merge_that_loses_the_race_changes_nothing(s3, monkeypatch):
    # Two runs merging at once: S3 lets only one create the next base; the other just carries on.
    monkeypatch.setattr(ms, "COMPACT_AFTER", 3)
    fill_runs(s3, 4)

    rows = ms.S3Memory(LosesMergeRace(s3), BUCKET, "reader", ENGINE).load("de-DE")

    assert len(rows) == 4
    assert bases(s3, "de-DE") == [] and len(run_files(s3, "de-DE")) == 4   # nothing written or deleted


def test_a_new_base_is_written_only_if_absent(s3, monkeypatch):
    monkeypatch.setattr(ms, "COMPACT_AFTER", 3)
    fill_runs(s3, 4)
    calls = []

    class Recording:
        def put_object(self, **kwargs):
            calls.append(kwargs)
            return s3.put_object(**kwargs)

        def __getattr__(self, name):
            return getattr(s3, name)

    ms.S3Memory(Recording(), BUCKET, "reader", ENGINE).load("de-DE")

    [base_put] = [c for c in calls if "/base-" in c["Key"]]
    assert base_put["IfNoneMatch"] == "*"


def test_files_are_compressed_json_lines(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()
    run.close()

    [name] = run_files(s3, "de-DE")
    body = gzip.decompress(s3.get_object(Bucket=BUCKET, Key=name)["Body"].read()).decode("utf-8")
    assert json.loads(body.splitlines()[0]) == {"hash": key("Open").hex(), "source": "Open",
                                                "translation": "Öffnen", "model": "nllb-600M-ct2"}


def test_the_engine_folder_names_its_engine(s3):
    memory(s3).save([entry("Open", "Öffnen")])
    body = s3.get_object(Bucket=BUCKET, Key=f"memory/{ms.engine_key(ENGINE)}/engine.txt")["Body"].read()
    assert body.decode("utf-8") == ENGINE


def test_reset_empties_the_memory(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()
    run.close()

    ms.reset(s3, BUCKET)

    assert memory(s3, "run-2").load("de-DE") == []
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="memory/")


def test_reads_are_counted(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()
    run.close()
    reader = memory(s3, "run-2")
    reader.load("de-DE")
    assert reader.reads >= 1


def saved(s3, *pairs, run_id="run-1"):
    run = memory(s3, run_id)
    run.save([entry(source, translation, lang=lang) for lang, source, translation in pairs])
    run.commit()
    run.close()


def test_prefetched_languages_are_served_without_waiting_for_s3(s3):
    saved(s3, ("de-DE", "Open", "Öffnen"), ("fr-FR", "Open", "Ouvrir"))
    reader = memory(s3, "run-2")

    reader.prefetch(["de-DE", "fr-FR"])
    reader.wait_for_prefetch()
    reads = reader.reads

    assert as_dict(reader.load("de-DE")) == {"Open": ("Öffnen", "nllb-600M-ct2")}
    assert as_dict(reader.load("fr-FR")) == {"Open": ("Ouvrir", "nllb-600M-ct2")}
    assert reader.reads == reads                                  # both answered from the prefetch


class FailsFirstList:
    """An S3 client whose first listing fails, as a prefetch might on a network error."""

    def __init__(self, client):
        self.client, self.failed = client, False

    def get_paginator(self, name):
        if not self.failed:
            self.failed = True
            raise RuntimeError("network error")
        return self.client.get_paginator(name)

    def __getattr__(self, name):
        return getattr(self.client, name)


def test_a_failed_prefetch_falls_back_to_a_normal_load(s3):
    saved(s3, ("de-DE", "Open", "Öffnen"))
    reader = ms.S3Memory(FailsFirstList(s3), BUCKET, "run-2", ENGINE)

    reader.prefetch(["de-DE"])

    assert as_dict(reader.load("de-DE")) == {"Open": ("Öffnen", "nllb-600M-ct2")}


def test_uploads_run_in_the_background_and_close_waits_for_them(s3):
    run = memory(s3)
    run.save([entry("Open", "Öffnen")])
    run.commit()
    assert run.close() == 0
    assert as_dict(memory(s3, "run-2").load("de-DE")) == {"Open": ("Öffnen", "nllb-600M-ct2")}


class FailsUploads:
    def __init__(self, client):
        self.client = client

    def put_object(self, **kwargs):
        if "/runs/" in kwargs["Key"]:
            raise RuntimeError("upload failed")
        return self.client.put_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def test_a_failed_background_upload_is_reported_by_close(s3, capsys):
    run = ms.S3Memory(FailsUploads(s3), BUCKET, "run-1", ENGINE)
    run.save([entry("Open", "Öffnen")])
    run.commit()

    assert run.close() == 1
    assert "upload failed" in capsys.readouterr().out
