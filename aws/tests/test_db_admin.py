import pytest

from helpers import AWS_DIR, forbidden_connect, load_module


class RecordingConnect:
    def __init__(self):
        self.calls = []

    def __call__(self, conn_str, autocommit=False):
        recorder = self

        class Conn:
            def execute(self, sql):
                recorder.calls.append((conn_str, autocommit, sql))

            def close(self):
                pass

        return Conn()


@pytest.fixture
def db_admin(stub_modules, monkeypatch):
    stub_modules(pyodbc={"connect": forbidden_connect})
    monkeypatch.setenv("DB_HOST", "db.example")
    monkeypatch.setenv("DB_USER", "dbadmin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.delenv("DB_NAME", raising=False)
    return load_module("db_admin_under_test", AWS_DIR / "translator" / "db_admin.py")


def test_init_creates_database_on_master_then_table(db_admin):
    connect = RecordingConnect()
    db_admin.init(connect)
    (first_conn, first_auto, first_sql), (second_conn, second_auto, second_sql) = connect.calls
    assert "DATABASE=master;" in first_conn and first_auto is True
    assert first_sql == "IF DB_ID(N'localization') IS NULL CREATE DATABASE [localization];"
    assert "DATABASE=localization;" in second_conn and second_auto is True
    assert "CREATE TABLE dbo.Translations" in second_sql
    assert "PRIMARY KEY (SourceText, TargetLang)" in second_sql
    assert "IF OBJECT_ID(N'dbo.Translations', N'U') IS NULL" in second_sql


def test_table_has_every_column_the_merge_writes(db_admin):
    for column in ("SourceText", "TargetLang", "TranslatedText", "ModelName", "LastUpdated"):
        assert column in db_admin.CREATE_TABLE_SQL


def test_reset_empties_the_table(db_admin):
    connect = RecordingConnect()
    db_admin.reset(connect)
    assert connect.calls == [(connect.calls[0][0], True, "DELETE FROM dbo.Translations;")]
    assert "DATABASE=localization;" in connect.calls[0][0]


def test_unsafe_database_name_is_rejected_before_connecting(db_admin, monkeypatch):
    monkeypatch.setenv("DB_NAME", "x];DROP DATABASE y;--")
    connect = RecordingConnect()
    with pytest.raises(ValueError):
        db_admin.init(connect)
    assert connect.calls == []


def test_cli_usage(db_admin):
    assert db_admin.main(["db_admin.py"]) == 2
    assert db_admin.main(["db_admin.py", "drop"]) == 2
