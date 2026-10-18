"""Keep the converted CTranslate2 model in S3 so later Phase 2 runs skip conversion.

A cached model counts as complete only when its _COMPLETE marker exists. The
marker is written last, so an interrupted upload is never mistaken for a
usable model.
"""
import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

MARKER = "_COMPLETE"
EXIT_MISSING = 3
# Written into a local model folder once that model has loaded; a reused runner then skips the download.
LOCAL_MARK = ".model-key"


def cache_prefix(model_key):
    if not model_key or "/" in model_key:
        raise ValueError(f"invalid model key: {model_key!r}")
    return f"models/{model_key}/"


def is_complete(client, bucket, model_key):
    try:
        client.head_object(Bucket=bucket, Key=cache_prefix(model_key) + MARKER)
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def fetch(client, bucket, model_key, dest):
    if not is_complete(client, bucket, model_key):
        return False
    prefix = cache_prefix(model_key)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            relative = obj["Key"][len(prefix):]
            if not relative or relative == MARKER:
                continue
            local_path = os.path.join(dest, *relative.split("/"))
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            client.download_file(bucket, obj["Key"], local_path)
    return True


def present(dest, model_key):
    """True when dest holds this model and it already loaded once on this machine (mark)."""
    try:
        with open(os.path.join(dest, LOCAL_MARK), encoding="utf-8") as f:
            return f.read().strip() == model_key
    except OSError:
        return False


def mark(dest, model_key):
    with open(os.path.join(dest, LOCAL_MARK), "w", encoding="utf-8") as f:
        f.write(model_key)


def invalidate(client, bucket, model_key):
    client.delete_object(Bucket=bucket, Key=cache_prefix(model_key) + MARKER)


def publish(client, bucket, model_key, src):
    files = []
    for root, _, names in os.walk(src):
        for name in names:
            if name != LOCAL_MARK:
                files.append(os.path.join(root, name))
    if not files:
        raise ValueError(f"no model files to publish in {src}")
    prefix = cache_prefix(model_key)
    invalidate(client, bucket, model_key)
    for path in sorted(files):
        relative = os.path.relpath(path, src).replace(os.sep, "/")
        client.upload_file(path, bucket, prefix + relative)
    client.put_object(Bucket=bucket, Key=prefix + MARKER, Body=b"")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch, publish or invalidate the cached model in S3; "
                                                 "check (present) or record (mark) the model on this machine.")
    parser.add_argument("command", choices=["fetch", "publish", "invalidate", "present", "mark"])
    parser.add_argument("--dir", help="Local model directory (fetch destination or publish source).")
    parser.add_argument("--bucket", default=os.environ.get("BUCKET"))
    parser.add_argument("--model-key", default=os.environ.get("MODEL_KEY"))
    args = parser.parse_args(argv)
    if not args.bucket or not args.model_key:
        parser.error("--bucket (or BUCKET) and --model-key (or MODEL_KEY) are required")
    if args.command in ("fetch", "publish", "present", "mark") and not args.dir:
        parser.error(f"{args.command} needs --dir")
    if args.command == "present":
        if present(args.dir, args.model_key):
            print(f"✅ Model {args.model_key} is already on this machine")
            return 0
        return EXIT_MISSING
    if args.command == "mark":
        mark(args.dir, args.model_key)
        return 0

    client = boto3.client("s3")
    if args.command == "fetch":
        if fetch(client, args.bucket, args.model_key, args.dir):
            print(f"✅ Restored cached model {args.model_key} into {args.dir}")
            return 0
        print(f"ℹ️ No complete cached model for {args.model_key}")
        return EXIT_MISSING
    if args.command == "publish":
        publish(client, args.bucket, args.model_key, args.dir)
        print(f"✅ Published model to s3://{args.bucket}/{cache_prefix(args.model_key)}")
        return 0
    invalidate(client, args.bucket, args.model_key)
    print(f"🧹 Invalidated cached model {args.model_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
