import ast
import csv
import io
import json

import export_telemetry as et
from helpers import AZURE_DIR


def azure_payload_keys():
    tree = ast.parse((AZURE_DIR / "Docker-Local-Inference" / "translation.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "metrics_payload" for t in node.targets):
            return [key.value for key in node.value.keys]
    raise LookupError("metrics_payload not found")


def line(payload):
    return f"2026-10-16 10:00:00,000 [INFO] 📊 RESEARCH_DATA: {json.dumps(payload)}"


class FakeLogs:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def test_azure_fields_match_the_azure_payload_in_order():
    assert et.AZURE_FIELDS == azure_payload_keys()


def test_parse_research_line():
    assert et.parse_research_line(line({"run_id": "r1", "total_time": 5})) == {"run_id": "r1", "total_time": 5}
    assert et.parse_research_line("[INFO] Translating resx_file_10.es-ES.xlf") is None
    assert et.parse_research_line("RESEARCH_DATA: {not json") is None


def test_collect_follows_pages_and_keeps_only_the_run():
    logs = FakeLogs([
        {"events": [{"message": line({"run_id": "r1", "target_lang": "de-DE"})},
                    {"message": line({"run_id": "other", "target_lang": "fr-FR"})}],
         "nextToken": "t2"},
        {"events": [{"message": line({"run_id": "r1", "target_lang": "es-ES"})}]},
    ])
    rows = et.collect(logs, "/cloud-loc/codebuild", "r1")
    assert [r["target_lang"] for r in rows] == ["de-DE", "es-ES"]
    assert logs.calls[1]["nextToken"] == "t2"
    assert logs.calls[0]["logGroupName"] == "/cloud-loc/codebuild"


def test_write_csv_uses_azure_order_then_aws_fields():
    out = io.StringIO()
    et.write_csv([{"run_id": "r1", "total_time": 5, "unexpected": "dropped"}], out)
    rows = list(csv.reader(io.StringIO(out.getvalue())))
    assert rows[0] == et.AZURE_FIELDS + et.AWS_FIELDS
    record = dict(zip(rows[0], rows[1]))
    assert record["total_time"] == "5"
    assert record["run_id"] == "r1"
    assert record["file_name"] == ""


def test_main_fails_when_the_run_has_no_telemetry(monkeypatch):
    monkeypatch.setattr(et.boto3, "client", lambda *args, **kwargs: FakeLogs([{"events": []}]))
    assert et.main(["--run-id", "missing"]) == 1


def test_collect_keeps_only_the_latest_row_per_file():
    # A retried file (e.g. its raw input could not be deleted) logs a second row; keep the newest.
    logs = FakeLogs([{"events": [
        {"message": line({"run_id": "r1", "file_name": "a.de-DE.xlf", "target_lang": "de-DE", "total_time": 1})},
        {"message": line({"run_id": "r1", "file_name": "a.es-ES.xlf", "target_lang": "es-ES", "total_time": 2})},
        {"message": line({"run_id": "r1", "file_name": "a.de-DE.xlf", "target_lang": "de-DE", "total_time": 3})},
    ]}])
    rows = et.collect(logs, "/cloud-loc/codebuild", "r1")
    assert [(r["file_name"], r["total_time"]) for r in rows] == [("a.de-DE.xlf", 3), ("a.es-ES.xlf", 2)]


def test_collect_reads_every_line_of_a_batched_event():
    # SSM Run Command output can put several output lines into one CloudWatch event.
    message = "\n".join([
        line({"run_id": "r1", "file_name": "a.de-DE.xlf", "target_lang": "de-DE"}),
        "2026-10-16 10:00:01,000 [INFO] Translating a.es-ES.xlf to es-ES...",
        line({"run_id": "r1", "file_name": "a.es-ES.xlf", "target_lang": "es-ES"}),
    ])
    rows = et.collect(FakeLogs([{"events": [{"message": message}]}]), "/cloud-loc/ec2-runner", "r1")
    assert [r["target_lang"] for r in rows] == ["de-DE", "es-ES"]


def test_memory_and_real_token_counters_are_exported():
    for field in ("tm_db_queries", "tm_lookup_ms", "tm_hits_in_run", "tm_twin_hits", "model_strings",
                  "real_input_tokens", "real_output_tokens", "padding_pct"):
        assert field in et.AWS_FIELDS
