"""Translation memory stored as files in S3 instead of a database.

    memory/<engine key>/engine.txt                      the full engine version this folder belongs to
    memory/<engine key>/<lang>/base-<n>.jsonl.gz        all entries merged so far
    memory/<engine key>/<lang>/runs/<run>-<k>.jsonl.gz  entries added by one commit of one run

The pipeline loads a language once per run (load) and keeps it in a dictionary, so a file per language
is enough. Each run only ever writes its own files, so two runs at the same time cannot overwrite each
other. When a language has COMPACT_AFTER unmerged run files, the next load merges them into a new base;
S3 creates that base only if it does not exist yet (IfNoneMatch), so of two runs merging at once one
wins and the other carries on unchanged.

    python memory_store.py reset     empty the memory (start a run at a 0% cache rate)
"""
import gzip
import hashlib
import json
import os
import re
import sys
import threading
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError

COMPACT_AFTER = 8           # unmerged run files that trigger a merge (each is one more request per load)
DOWNLOAD_THREADS = 8        # a language's files are fetched in parallel: one S3 round trip instead of one per file
Entry = namedtuple("Entry", "key lang source translation model")
BASE = re.compile(r"base-(\d{6})\.jsonl\.gz$")


def engine_key(engine_version):
    return hashlib.sha256(engine_version.encode("utf-8")).hexdigest()[:16]


def encode_lines(records):
    return gzip.compress("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8"))


def decode_lines(body):
    return [json.loads(line) for line in gzip.decompress(body).decode("utf-8").splitlines() if line]


def _list(client, bucket, prefix):
    keys = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [obj["Key"] for obj in page.get("Contents", [])]
    return keys


def _record(entry):
    return {"hash": entry.key.hex(), "source": entry.source, "translation": entry.translation, "model": entry.model}


class S3Memory:
    def __init__(self, client, bucket, run_id, engine_version, prefix="memory"):
        self.client, self.bucket, self.run_id = client, bucket, run_id
        self.engine_version = engine_version
        self.root = f"{prefix}/{engine_key(engine_version)}"
        self.pending = {}            # lang -> [Entry] saved since the last commit
        self.commits = {}            # lang -> files written by this run
        self.reads = 0               # S3 objects read
        self._engine_named = False
        self._lock = threading.Lock()
        self._prefetched = {}        # lang -> Future of a background load (prefetch)
        self._loader = None
        self._uploader = ThreadPoolExecutor(max_workers=4)
        self._uploads = []           # (key, Future) of background uploads not yet confirmed by close()

    def _get(self, key):
        with self._lock:
            self.reads += 1
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def prefetch(self, langs):
        """Start loading these languages in the background, all at once, so that each is ready when the run
        reaches it: one S3 round trip for the whole run instead of one per language."""
        langs = [lang for lang in langs if lang not in self._prefetched]
        if not langs:
            return
        if self._loader is None:
            self._loader = ThreadPoolExecutor(max_workers=min(16, len(langs)))
        for lang in langs:
            self._prefetched[lang] = self._loader.submit(self._load_now, lang)

    def wait_for_prefetch(self):
        for future in list(self._prefetched.values()):
            future.exception()

    def load(self, lang):
        """[(SourceHash bytes, source, translation, model)] for one language of this engine version."""
        future = self._prefetched.pop(lang, None)
        if future is not None:
            try:
                return future.result()
            except Exception as e:
                print(f"⚠️ Prefetch of the {lang} translation memory failed ({e}); loading it now.")
        return self._load_now(lang)

    def _load_now(self, lang):
        folder = f"{self.root}/{lang}/"
        keys = _list(self.client, self.bucket, folder)
        bases = sorted((int(m.group(1)), k) for k in keys if (m := BASE.search(k)))
        runs = sorted(k for k in keys if k.startswith(folder + "runs/"))

        # The base names the run files it already contains; run files are read only if they are not in it.
        # The base and every run file are fetched together, then applied in order (later files win).
        base_key = bases[-1][1] if bases else None
        base_number = bases[-1][0] if bases else 0
        with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
            bodies = dict(zip([base_key, *runs] if base_key else runs,
                              pool.map(self._get, [base_key, *runs] if base_key else runs)))

        entries, merged = {}, set()
        if base_key:
            header, *records = decode_lines(bodies[base_key])
            merged = set(header.get("merged_runs", []))
            for r in records:
                entries[r["hash"]] = r
        unmerged = [k for k in runs if k.rsplit("/", 1)[1] not in merged]
        for key in unmerged:
            for r in decode_lines(bodies[key]):
                entries[r["hash"]] = r

        if len(unmerged) >= COMPACT_AFTER:
            self._compact(folder, base_number, merged, unmerged, entries, runs, [k for _, k in bases[:-1]])
        return [(bytes.fromhex(h), r["source"], r["translation"], r["model"]) for h, r in entries.items()]

    def _compact(self, folder, base_number, merged, unmerged, entries, runs, older_bases):
        header = {"merged_runs": sorted(merged | {k.rsplit("/", 1)[1] for k in unmerged})}
        new_base = f"{folder}base-{base_number + 1:06d}.jsonl.gz"
        try:
            self.client.put_object(Bucket=self.bucket, Key=new_base, IfNoneMatch="*",
                                   Body=encode_lines([header, *entries.values()]))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
                return               # another run created this base first; its contents are a superset
            raise
        # Run files already in the previous base are in the new one too; readers of that base no longer need them.
        stale = [k for k in runs if k.rsplit("/", 1)[1] in merged] + older_bases
        for i in range(0, len(stale), 1000):
            self.client.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": [{"Key": k} for k in stale[i:i + 1000]], "Quiet": True})

    def save(self, entries):
        if not self._engine_named:
            self.client.put_object(Bucket=self.bucket, Key=f"{self.root}/engine.txt", Body=self.engine_version.encode("utf-8"))
            self._engine_named = True
        for entry in entries:
            self.pending.setdefault(entry.lang, []).append(entry)

    def commit(self):
        """Upload what was saved since the last commit, in the background; close() confirms the uploads."""
        for lang, entries in self.pending.items():
            if not entries:
                continue
            number = self.commits.get(lang, 0) + 1
            self.commits[lang] = number
            key = f"{self.root}/{lang}/runs/{self.run_id}-{number:04d}.jsonl.gz"
            body = encode_lines([_record(e) for e in entries])
            self._uploads.append((key, self._uploader.submit(self.client.put_object, Bucket=self.bucket, Key=key, Body=body)))
        self.pending = {}

    def close(self):
        """Commit, wait for every background upload, and return how many failed (each is printed)."""
        self.commit()
        failed = 0
        for key, future in self._uploads:
            try:
                future.result()
            except Exception as e:
                failed += 1
                print(f"❌ Could not save translation memory file s3://{self.bucket}/{key}: {e}")
        self._uploads = []
        return failed


def reset(client, bucket, prefix="memory"):
    keys = _list(client, bucket, prefix + "/")
    for i in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
    return len(keys)


def main(argv):
    if len(argv) != 2 or argv[1] != "reset":
        print("Usage: python memory_store.py reset")
        return 2
    import boto3
    removed = reset(boto3.client("s3"), os.environ["BUCKET"])
    print(f"🧹 Emptied the translation memory in s3://{os.environ['BUCKET']}/memory/ ({removed} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
