import pytest

import model_cache
from helpers import BUCKET

KEY = "nllb-200-distilled-600M_int8_ct2-4.7.1"
PREFIX = f"models/{KEY}/"


class RecordingClient:
    """Wraps a boto3 client and records which keys were written, in order."""

    def __init__(self, client):
        self._client = client
        self.writes = []

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name == "upload_file":
            def upload_file(path, bucket, key, *args, **kwargs):
                self.writes.append(key)
                return attr(path, bucket, key, *args, **kwargs)
            return upload_file
        if name == "put_object":
            def put_object(**kwargs):
                self.writes.append(kwargs["Key"])
                return attr(**kwargs)
            return put_object
        return attr


def make_model(directory):
    (directory / "nested").mkdir(parents=True)
    (directory / "model.bin").write_bytes(b"weights")
    (directory / "config.json").write_text("{}", encoding="utf-8")
    (directory / "nested" / "extra.txt").write_text("x", encoding="utf-8")


def test_fetch_on_empty_cache_returns_false(s3, tmp_path):
    assert model_cache.fetch(s3, BUCKET, KEY, str(tmp_path / "out")) is False


def test_publish_then_fetch_restores_every_file(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    model_cache.publish(s3, BUCKET, KEY, str(src))
    dest = tmp_path / "dest"
    assert model_cache.fetch(s3, BUCKET, KEY, str(dest)) is True
    assert (dest / "model.bin").read_bytes() == b"weights"
    assert (dest / "nested" / "extra.txt").read_text(encoding="utf-8") == "x"
    assert not (dest / model_cache.MARKER).exists()


def test_partial_upload_without_marker_is_not_fetched(s3, tmp_path):
    s3.put_object(Bucket=BUCKET, Key=PREFIX + "model.bin", Body=b"half")
    dest = tmp_path / "dest"
    assert model_cache.fetch(s3, BUCKET, KEY, str(dest)) is False
    assert not dest.exists()


def test_publish_writes_the_marker_last(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    client = RecordingClient(s3)
    model_cache.publish(client, BUCKET, KEY, str(src))
    assert client.writes[-1] == PREFIX + model_cache.MARKER
    assert len(client.writes) == 4


def test_publish_of_empty_directory_is_refused(s3, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        model_cache.publish(s3, BUCKET, KEY, str(tmp_path / "empty"))


def test_invalidate_makes_the_cache_incomplete(s3, tmp_path):
    src = tmp_path / "src"
    make_model(src)
    model_cache.publish(s3, BUCKET, KEY, str(src))
    model_cache.invalidate(s3, BUCKET, KEY)
    assert model_cache.is_complete(s3, BUCKET, KEY) is False


def test_cli_fetch_reports_missing_with_exit_code_3(s3, tmp_path, monkeypatch):
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("MODEL_KEY", KEY)
    assert model_cache.main(["fetch", "--dir", str(tmp_path / "dest")]) == model_cache.EXIT_MISSING


@pytest.mark.parametrize("bad", ["", "a/b"])
def test_unsafe_model_keys_are_rejected(bad):
    with pytest.raises(ValueError):
        model_cache.cache_prefix(bad)


def test_a_marked_local_model_is_present_only_for_its_key(tmp_path):
    model_dir = tmp_path / "nllb_ct2"
    model_dir.mkdir()
    assert model_cache.present(str(model_dir), KEY) is False
    model_cache.mark(str(model_dir), KEY)
    assert model_cache.present(str(model_dir), KEY) is True
    assert model_cache.present(str(model_dir), "other-model_int8_ct2-9.9.9") is False


def test_the_local_mark_is_never_published(s3, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.bin").write_bytes(b"weights")
    model_cache.mark(str(src), KEY)
    model_cache.publish(s3, BUCKET, KEY, str(src))
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)["Contents"]]
    assert not any(k.endswith(model_cache.LOCAL_MARK) for k in keys)
