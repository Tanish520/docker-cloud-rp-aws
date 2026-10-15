import json
import xml.etree.ElementTree as ET

import pytest

import phase1
import phase3
from helpers import BUCKET, write_resx


def output_keys(s3):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="runs/run-1/output/")
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


def values(resx_path):
    return [data.find("value").text for data in ET.parse(resx_path).getroot().findall("data")]


@pytest.fixture
def workspace(s3, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("RUN_ID", "run-1")
    source = tmp_path / "source"
    source.mkdir()
    write_resx(source / "resx_file_10.resx", ["Hello", "Bye"])
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "SourceRepoPath": str(source),
        "TargetResxPath": str(tmp_path / "localized"),
    }), encoding="utf-8")
    return tmp_path, source, str(config)


def upload_translated(s3, tmp_path, source, name, lang, translations):
    tree = phase1.resx_to_xlf(str(source / "resx_file_10.resx"), lang)
    for unit in tree.getroot().iter("trans-unit"):
        text = unit.find("source").text
        if text in translations:
            unit.find("target").text = translations[text]
    staging = tmp_path / "staging"  # kept out of the working folder that the cleanup check scans
    staging.mkdir(exist_ok=True)
    local = staging / name
    tree.write(str(local), encoding="utf-8", xml_declaration=True)
    s3.upload_file(str(local), BUCKET, f"runs/run-1/translated/{name}")


def test_merges_translations_and_uploads_output(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.es-ES.xlf", "es-ES", {"Hello": "Hola", "Bye": "Adiós"})
    assert phase3.main(config) == 0
    out = tmp_path / "localized" / "es-ES" / "resx_file_10.resx"
    assert values(out) == ["Hola", "Adiós"]
    assert output_keys(s3) == ["runs/run-1/output/es-ES/resx_file_10.resx"]
    assert list(tmp_path.glob("*.xlf")) == []


def test_untranslated_strings_keep_the_source_text(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.de-DE.xlf", "de-DE", {"Hello": "Hallo"})
    assert phase3.main(config) == 0
    assert values(tmp_path / "localized" / "de-DE" / "resx_file_10.resx") == ["Hallo", "Bye"]


def test_no_translated_files_fails(s3, workspace):
    _, _, config = workspace
    assert phase3.main(config) == 1


def test_file_without_matching_source_fails_but_others_merge(s3, workspace):
    tmp_path, source, config = workspace
    upload_translated(s3, tmp_path, source, "resx_file_10.fr-FR.xlf", "fr-FR", {"Hello": "Bonjour"})
    upload_translated(s3, tmp_path, source, "orphan.fr-FR.xlf", "fr-FR", {})
    assert phase3.main(config) == 1
    assert output_keys(s3) == ["runs/run-1/output/fr-FR/resx_file_10.resx"]
