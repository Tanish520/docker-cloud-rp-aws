import pytest

import aws_runtime


@pytest.fixture
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_USER", "dbadmin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.delenv("DB_NAME", raising=False)


def test_connection_string_matches_azure_odbc_options(db_env):
    conn = aws_runtime.sql_conn_str()
    assert conn.startswith("DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:db.example.com,1433;")
    assert "DATABASE=localization;" in conn
    assert "UID={dbadmin};" in conn and "PWD={secret};" in conn
    for option in ("Encrypt=yes;", "TrustServerCertificate=yes;", "Connection Timeout=30;",
                   "MultipleActiveResultSets=False;", "Persist Security Info=False;"):
        assert option in conn


def test_password_with_odbc_special_characters_is_braced(db_env, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "p;a}ss")
    assert "PWD={p;a}}ss};" in aws_runtime.sql_conn_str()


def test_explicit_database_overrides_default(db_env):
    assert "DATABASE=master;" in aws_runtime.sql_conn_str("master")


def test_db_name_comes_from_environment(db_env, monkeypatch):
    monkeypatch.setenv("DB_NAME", "tm_experiments")
    assert aws_runtime.db_name() == "tm_experiments"
    assert "DATABASE=tm_experiments;" in aws_runtime.sql_conn_str()


@pytest.mark.parametrize("bad", ["x];DROP DATABASE y;--", "has space", "1starts_with_digit", ""])
def test_unsafe_db_names_are_rejected(db_env, monkeypatch, bad):
    monkeypatch.setenv("DB_NAME", bad)
    with pytest.raises(ValueError):
        aws_runtime.db_name()


def test_cpu_model_reads_proc_cpuinfo_format(tmp_path):
    info = tmp_path / "cpuinfo"
    info.write_text("processor\t: 0\nmodel name\t: AMD EPYC 7R13 Processor\nflags\t: fpu\n")
    assert aws_runtime.cpu_model(str(info)) == "AMD EPYC 7R13 Processor"


def test_cpu_model_falls_back_when_unavailable(tmp_path):
    assert aws_runtime.cpu_model(str(tmp_path / "missing")) == "unknown"


def test_extra_fields(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("RUN_ID", "run-42")
    fields = aws_runtime.extra_fields()
    assert set(fields) == {"cloud_provider", "region", "run_id", "cpu_limit", "cpu_model",
                           "host_cpu_count", "omp_num_threads"}
    assert fields["cloud_provider"] == "aws"
    assert fields["region"] == "ap-south-1"
    assert fields["run_id"] == "run-42"
    assert isinstance(fields["cpu_limit"], int) and fields["cpu_limit"] >= 1


def test_extra_fields_expose_host_cores_and_openmp_setting(monkeypatch):
    # cpu_limit is what docker --cpuset-cpus allows; host_cpu_count is what thread pools may size themselves to.
    import os
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    fields = aws_runtime.extra_fields()
    assert fields["host_cpu_count"] == os.cpu_count()
    assert fields["omp_num_threads"] == "unset"
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    assert aws_runtime.extra_fields()["omp_num_threads"] == "2"
