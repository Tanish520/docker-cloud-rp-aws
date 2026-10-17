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
    assert "PRIMARY KEY (TargetLang, EngineVersion, SourceHash)" in second_sql
    assert "IF OBJECT_ID(N'dbo.Translations', N'U') IS NULL" in second_sql


def test_table_has_every_column_the_merge_writes(db_admin):
    for column in ("SourceHash", "SourceText", "TargetLang", "EngineVersion", "TranslatedText", "ModelName", "LastUpdated"):
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


def test_key_is_a_fixed_size_hash_so_any_text_length_fits(db_admin):
    # The text itself was the key, and SQL Server keys hold at most 900 bytes (434 characters);
    # KDE4 has strings up to 660 characters. The key is now a 32-byte SHA-256 of the text.
    assert "SourceHash     BINARY(32)    NOT NULL" in db_admin.CREATE_TABLE_SQL
    assert "SourceText     NVARCHAR(MAX) NOT NULL" in db_admin.CREATE_TABLE_SQL


def test_init_replaces_a_table_from_the_old_text_keyed_layout(db_admin):
    # The memory is a cache, so an old-layout table is dropped and recreated rather than migrated.
    sql = db_admin.CREATE_TABLE_SQL
    assert "COL_LENGTH(N'dbo.Translations', N'EngineVersion') IS NULL" in sql
    assert sql.index("DROP TABLE dbo.Translations") < sql.index("CREATE TABLE dbo.Translations")


def test_each_translation_records_the_engine_that_made_it(db_admin):
    assert "EngineVersion  NVARCHAR(200) NOT NULL" in db_admin.CREATE_TABLE_SQL
