"""Export the RESEARCH_DATA lines of one AWS pipeline run to CSV (Azure column order first)."""
import argparse
import csv
import json
import sys

import boto3

MARKER = "RESEARCH_DATA: "

# Same keys and order as metrics_payload in the Azure translation.py (checked by tests).
AZURE_FIELDS = [
    "run_invocation_id", "file_name", "target_lang", "throughput_seg_per_hr",
    "throughput_input_tokens_per_hr", "total_api_call_logically", "actual_api_call",
    "total_segments", "avg_segment_char_length", "text_expansion_ratio",
    "tokens_processed_in_whole_language", "total_input_tokens_source", "total_output_tokens_target",
    "avg_input_tokens_per_batch", "avg_output_tokens_per_batch", "char_per_token_ratio",
    "total_token_input_plus_output_per_batch", "time_per_token", "nmt_calls",
    "transational_memory_hits", "cache_rate", "total_time", "cold_start_init_ms",
    "compute_time_ms", "total_sleep_time_ms", "network_wait_time_ms", "io_bound_pipeline_pct",
    "hf_api_wait_ms", "azure_sql_query_ms", "azure_sql_commit_ms", "xml_parse_ms",
    "xml_disk_write_ms", "blob_download_ms", "blob_upload_ms", "telemetry_overhead_ms",
    "cpu_usage_pct", "memory_mb", "serverless_cost_gb_sec", "api_retries", "api_failures",
    "api_throttled_429",
]
AWS_FIELDS = ["cloud_provider", "region", "run_id", "cpu_limit", "cpu_model"]


def parse_research_line(message):
    if MARKER not in message:
        return None
    try:
        payload = json.loads(message.split(MARKER, 1)[1].strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def collect(logs_client, log_group, run_id):
    # Events arrive oldest first; a file processed twice in one run (a retry) keeps its newest row.
    latest = {}
    kwargs = {"logGroupName": log_group, "filterPattern": f'"RESEARCH_DATA" "{run_id}"'}
    while True:
        resp = logs_client.filter_log_events(**kwargs)
        for event in resp.get("events", []):
            payload = parse_research_line(event["message"])
            if payload and payload.get("run_id") == run_id:
                latest[(payload.get("file_name"), payload.get("target_lang"))] = payload
        token = resp.get("nextToken")
        if not token:
            return list(latest.values())
        kwargs["nextToken"] = token


def write_csv(rows, out):
    writer = csv.DictWriter(out, fieldnames=AZURE_FIELDS + AWS_FIELDS, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="CodePipeline execution ID of the run.")
    parser.add_argument("--log-group", default="/cloud-loc/codebuild")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--out", help="CSV file to write (default: stdout).")
    args = parser.parse_args(argv)

    rows = collect(boto3.client("logs", region_name=args.region), args.log_group, args.run_id)
    if not rows:
        print(f"No RESEARCH_DATA found for run {args.run_id} in {args.log_group}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            write_csv(rows, f)
        print(f"Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    else:
        write_csv(rows, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
