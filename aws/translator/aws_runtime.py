"""AWS-specific runtime settings: SQL connection string and host facts for telemetry."""
import os
import re

DEFAULT_DB_NAME = "localization"
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def db_name():
    name = os.environ.get("DB_NAME", DEFAULT_DB_NAME)
    if not _DB_NAME_RE.match(name):
        raise ValueError(f"invalid DB_NAME: {name!r}")
    return name


def _odbc_value(value):
    # Braces let the value contain ';' and other separators; '}' is escaped by doubling.
    return "{" + value.replace("}", "}}") + "}"


def sql_conn_str(database=None):
    """The Azure connection settings, pointed at RDS, in ODBC keywords.

    Azure's string also had the ADO.NET keywords `Persist Security Info=False` (no ODBC meaning) and
    `MultipleActiveResultSets=False`, which ODBC Driver 18 rejects; `MARS_Connection=no` is its equivalent.
    """
    database = database or db_name()
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER=tcp:{os.environ['DB_HOST']},1433;"
        f"DATABASE={database};"
        f"UID={_odbc_value(os.environ['DB_USER'])};"
        f"PWD={_odbc_value(os.environ['DB_PASSWORD'])};"
        "MARS_Connection=no;Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=30;"
    )


def cpu_limit():
    """CPU cores this process may run on (respects docker --cpuset-cpus)."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def cpu_model(cpuinfo_path="/proc/cpuinfo"):
    try:
        with open(cpuinfo_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def extra_fields():
    """RESEARCH_DATA fields that exist only in the AWS version."""
    return {
        "cloud_provider": "aws",
        "region": os.environ.get("AWS_REGION", "unknown"),
        "run_id": os.environ.get("RUN_ID", "unknown"),
        "cpu_limit": cpu_limit(),
        "cpu_model": cpu_model(),
        # Cores the host has (thread pools may size themselves to this rather than cpu_limit).
        "host_cpu_count": os.cpu_count(),
        "omp_num_threads": os.environ.get("OMP_NUM_THREADS", "unset"),
    }
