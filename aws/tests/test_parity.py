"""The AWS pipeline must keep the Azure logic so results stay comparable."""
from astutil import (assigned_value_dump, function_dump, import_dumps, main_timeline,
                     shared_top_level, statement_slice)
from helpers import AWS_DIR, AZURE_DIR

AZ_PHASES = AZURE_DIR / "API_based_HFace_AppInsight"
AZ_DOCKER = AZURE_DIR / "Docker-Local-Inference"
AWS_TRANSLATOR = AWS_DIR / "translator"


def test_resx_to_xlf_is_identical_to_azure():
    assert function_dump(AWS_DIR / "phase1.py", "resx_to_xlf") == function_dump(AZ_PHASES / "phase1.py", "resx_to_xlf")


def test_xlf_to_resx_is_identical_to_azure():
    assert function_dump(AWS_DIR / "phase3.py", "xlf_to_resx") == function_dump(AZ_PHASES / "phase3.py", "xlf_to_resx")


AZ_TRANSLATION = AZ_DOCKER / "translation.py"
AWS_TRANSLATION = AWS_TRANSLATOR / "translation.py"
AZURE_ONLY_IMPORTS = {"from azure.storage.blob import BlobServiceClient"}
AWS_ONLY_IMPORTS = {"import aws_runtime", "from s3_store import RunStore",
                    "from placeholders import protect, restore",
                    "from batching import decoding_cap, plan_token_batches"}


def _dumps(statements):
    import ast
    return {ast.dump(ast.parse(s).body[0]) for s in statements}


def test_translation_shared_code_is_identical():
    # AWS changes only the translation engine: batching and beam (BATCHING_RESULTS.md sections 8-9),
    # placeholder protection (section 10) and a Punjabi entry.
    exclude = ("main", "translate_local", "process_xlf_file", "translator", "BATCH_SIZE", "TOKEN_LIMIT",
               "CHUNK_SIZE", "BEAM_SIZE", "ENGINE_WORKERS", "TM_KEY_LIMIT", "LANG_TOKEN_MULTIPLIERS")
    assert shared_top_level(AWS_TRANSLATION, exclude) == shared_top_level(AZ_TRANSLATION, exclude)


def test_translation_imports_differ_only_by_storage_and_runtime():
    azure, aws = import_dumps(AZ_TRANSLATION), import_dumps(AWS_TRANSLATION)
    assert azure - aws == _dumps(AZURE_ONLY_IMPORTS)
    assert aws - azure == _dumps(AWS_ONLY_IMPORTS)


def test_metrics_payload_and_its_inputs_match_azure():
    assert assigned_value_dump(AWS_TRANSLATION, "main", "metrics_payload") == \
        assigned_value_dump(AZ_TRANSLATION, "main", "metrics_payload")
    assert statement_slice(AWS_TRANSLATION, "main", "file_duration", "metrics_payload") == \
        statement_slice(AZ_TRANSLATION, "main", "file_duration", "metrics_payload")


def _requirement_lines(path):
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def test_requirements_are_azure_plus_boto3():
    # boto3 is the only addition: s3_store.py needs it inside the container.
    assert _requirement_lines(AWS_TRANSLATOR / "requirements.txt") == \
        _requirement_lines(AZ_DOCKER / "requirements.txt") | {"boto3==1.35.36"}


def test_dockerfile_differs_only_in_base_image():
    azure = (AZ_DOCKER / "Dockerfile").read_text(encoding="utf-8").splitlines()
    aws = (AWS_TRANSLATOR / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert len(azure) == len(aws)
    differing = [(a, b) for a, b in zip(azure, aws) if a != b]
    # Pinned to bookworm: the Dockerfile installs msodbcsql18 from Microsoft's Debian 12 repo.
    assert differing == [("FROM python:3.10-slim", "FROM public.ecr.aws/docker/library/python:3.10-slim-bookworm")]


def test_main_timing_and_storage_timeline_matches_azure():
    # Every timer, sleep and storage call in main(), in source order: these feed the headline metrics.
    assert main_timeline(AWS_TRANSLATION) == main_timeline(AZ_TRANSLATION)


def test_timeline_detects_changed_sleep_and_moved_timer(tmp_path):
    source = AWS_TRANSLATION.read_text(encoding="utf-8")
    original = main_timeline(AWS_TRANSLATION)

    shorter_sleep = tmp_path / "sleep.py"
    shorter_sleep.write_text(source.replace("await asyncio.sleep(2)", "await asyncio.sleep(0)"), encoding="utf-8")
    assert main_timeline(shorter_sleep) != original

    upload = "                    store.upload_translated(local_safe_name, blob.name)\n"
    timer = '                    profiler.start_timer("blob_upload")\n'
    assert timer + upload in source
    moved_timer = tmp_path / "moved.py"
    moved_timer.write_text(source.replace(timer + upload, upload + timer), encoding="utf-8")
    assert main_timeline(moved_timer) != original
