import pytest

from helpers import BUCKET
from s3_store import RunStore, StoredFile


def keys(s3, prefix=""):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


@pytest.fixture
def store(s3):
    return RunStore(BUCKET, "run-1", client=s3)


def test_keys_are_scoped_to_the_run(store):
    assert store.raw_prefix == "runs/run-1/raw/"
    assert store.key("translated", "a.es-ES.xlf") == "runs/run-1/translated/a.es-ES.xlf"


@pytest.mark.parametrize("run_id", ["", "a/b", None])
def test_rejects_unsafe_run_ids(s3, run_id):
    with pytest.raises(ValueError):
        RunStore(BUCKET, run_id, client=s3)


def test_rejects_missing_bucket(s3):
    with pytest.raises(ValueError):
        RunStore("", "run-1", client=s3)


def test_upload_list_and_download_raw(store, tmp_path):
    src = tmp_path / "a.es-ES.xlf"
    src.write_text("hello", encoding="utf-8")
    assert store.upload_raw(str(src), "a.es-ES.xlf") == "runs/run-1/raw/a.es-ES.xlf"
    assert store.list_raw() == [StoredFile("a.es-ES.xlf", "runs/run-1/raw/a.es-ES.xlf")]
    dest = tmp_path / "copy.xlf"
    store.download_raw("a.es-ES.xlf", str(dest))
    assert dest.read_text(encoding="utf-8") == "hello"


def test_list_ignores_other_runs_other_stages_and_nested_keys(s3, store):
    for key in ("runs/run-1/raw/a.xlf", "runs/run-1/raw/nested/b.xlf",
                "runs/run-2/raw/c.xlf", "runs/run-1/translated/d.xlf"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    assert [f.name for f in store.list_raw()] == ["a.xlf"]
    assert [f.name for f in store.list_translated()] == ["d.xlf"]


def test_list_paginates_past_1000_objects(s3, store):
    for i in range(1001):
        s3.put_object(Bucket=BUCKET, Key=f"runs/run-1/raw/f{i:04d}.xlf", Body=b"x")
    names = [f.name for f in store.list_raw()]
    assert len(names) == 1001
    assert names[0] == "f0000.xlf" and names[-1] == "f1000.xlf"


def test_delete_raw_removes_only_that_file(s3, store):
    for name in ("a.xlf", "b.xlf"):
        s3.put_object(Bucket=BUCKET, Key=f"runs/run-1/raw/{name}", Body=b"x")
    store.delete_raw("a.xlf")
    assert keys(s3, "runs/run-1/raw/") == ["runs/run-1/raw/b.xlf"]


def test_translated_round_trip(store, tmp_path):
    src = tmp_path / "a.es-ES.xlf"
    src.write_text("hola", encoding="utf-8")
    store.upload_translated(str(src), "a.es-ES.xlf")
    dest = tmp_path / "back.xlf"
    store.download_translated("a.es-ES.xlf", str(dest))
    assert dest.read_text(encoding="utf-8") == "hola"


def test_upload_output_keeps_language_subfolder(s3, store, tmp_path):
    src = tmp_path / "resx_file_10.resx"
    src.write_text("<root/>", encoding="utf-8")
    assert store.upload_output(str(src), "es-ES/resx_file_10.resx") == "runs/run-1/output/es-ES/resx_file_10.resx"
    assert keys(s3, "runs/run-1/output/") == ["runs/run-1/output/es-ES/resx_file_10.resx"]


def test_failed_upload_raises_and_leaves_raw_in_place(s3, store, tmp_path):
    s3.put_object(Bucket=BUCKET, Key="runs/run-1/raw/a.xlf", Body=b"x")
    with pytest.raises(Exception):
        store.upload_translated(str(tmp_path / "missing.xlf"), "a.xlf")
    assert keys(s3, "runs/run-1/raw/") == ["runs/run-1/raw/a.xlf"]
