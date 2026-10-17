"""Translation-memory database setup for the AWS pipeline.

    python db_admin.py init    create the database and dbo.Translations if missing
    python db_admin.py reset   delete every row (start a run at a 0% cache rate)
"""
import sys
from contextlib import closing

import pyodbc

import aws_runtime

CREATE_DATABASE_SQL = "IF DB_ID(N'{name}') IS NULL CREATE DATABASE [{name}];"
# Keyed by a SHA-256 of the text (translation.tm_key): a key holds at most 900 bytes, so keying by the
# text itself capped strings at 434 characters. The memory is a cache, so a table in that old layout
# is dropped and recreated.
CREATE_TABLE_SQL = """IF OBJECT_ID(N'dbo.Translations', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.Translations', N'SourceHash') IS NULL
    DROP TABLE dbo.Translations;
IF OBJECT_ID(N'dbo.Translations', N'U') IS NULL
CREATE TABLE dbo.Translations (
    SourceHash     BINARY(32)    NOT NULL,
    SourceText     NVARCHAR(MAX) NOT NULL,
    TargetLang     NVARCHAR(16)  NOT NULL,
    TranslatedText NVARCHAR(MAX) NULL,
    ModelName      NVARCHAR(100) NULL,
    LastUpdated    DATETIME2     NULL,
    CONSTRAINT PK_Translations PRIMARY KEY (SourceHash, TargetLang)
);"""
RESET_SQL = "DELETE FROM dbo.Translations;"


def _execute(database, sql, connect):
    # autocommit: CREATE DATABASE cannot run inside a transaction.
    with closing(connect(aws_runtime.sql_conn_str(database), autocommit=True)) as conn:
        conn.execute(sql)


def init(connect=None):
    connect = connect or pyodbc.connect
    name = aws_runtime.db_name()
    _execute("master", CREATE_DATABASE_SQL.format(name=name), connect)
    _execute(name, CREATE_TABLE_SQL, connect)
    print(f"✅ Database '{name}' and table dbo.Translations are ready.")


def reset(connect=None):
    connect = connect or pyodbc.connect
    name = aws_runtime.db_name()
    _execute(name, RESET_SQL, connect)
    print(f"🧹 Emptied dbo.Translations in '{name}' (run starts at 0% cache).")


COMMANDS = {"init": init, "reset": reset}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print("Usage: python db_admin.py init|reset")
        return 2
    COMMANDS[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
