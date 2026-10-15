"""S3 layout for one pipeline run: runs/<run_id>/{raw,translated,output}/."""
from collections import namedtuple

import boto3

RAW = "raw"
TRANSLATED = "translated"
OUTPUT = "output"

StoredFile = namedtuple("StoredFile", ["name", "key"])


class RunStore:
    def __init__(self, bucket, run_id, client=None):
        if not bucket:
            raise ValueError("bucket is required")
        if not run_id or "/" in run_id:
            raise ValueError(f"invalid run_id: {run_id!r}")
        self.bucket = bucket
        self.run_id = run_id
        self.client = client or boto3.client("s3")

    def prefix(self, stage):
        return f"runs/{self.run_id}/{stage}/"

    @property
    def raw_prefix(self):
        return self.prefix(RAW)

    def key(self, stage, name):
        return self.prefix(stage) + name

    def _list(self, stage):
        prefix = self.prefix(stage)
        found = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if name and "/" not in name:
                    found.append(StoredFile(name, obj["Key"]))
        return sorted(found)

    def _upload(self, stage, local_path, name):
        key = self.key(stage, name)
        self.client.upload_file(local_path, self.bucket, key)
        return key

    def upload_raw(self, local_path, name):
        return self._upload(RAW, local_path, name)

    def list_raw(self):
        return self._list(RAW)

    def download_raw(self, name, local_path):
        self.client.download_file(self.bucket, self.key(RAW, name), local_path)

    def delete_raw(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self.key(RAW, name))

    def upload_translated(self, local_path, name):
        return self._upload(TRANSLATED, local_path, name)

    def list_translated(self):
        return self._list(TRANSLATED)

    def download_translated(self, name, local_path):
        self.client.download_file(self.bucket, self.key(TRANSLATED, name), local_path)

    def upload_output(self, local_path, relative_name):
        return self._upload(OUTPUT, local_path, relative_name)
