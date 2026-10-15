import os
import sys
import json
import xml.etree.ElementTree as ET

from translator.s3_store import RAW, RunStore


# Identical to azure/API_based_HFace_AppInsight/phase1.py (enforced by tests/test_parity.py).
def resx_to_xlf(resx_path, lang_code):
    tree = ET.parse(resx_path)
    root = tree.getroot()

    xliff = ET.Element("xliff", version="1.2")
    file_elem = ET.SubElement(xliff, "file", {
        "source-language": "en",
        "target-language": lang_code,
        "datatype": "plaintext",
        "original": os.path.basename(resx_path)
    })
    body = ET.SubElement(file_elem, "body")

    for data in root.findall("data"):
        source_text = data.find("value").text if data.find("value") is not None else ""
        if not source_text:
            continue
        trans_unit = ET.SubElement(body, "trans-unit", id=data.attrib.get("name", ""))
        ET.SubElement(trans_unit, "source").text = source_text
        ET.SubElement(trans_unit, "target").text = ""

    return ET.ElementTree(xliff)


def select_input_files(source_path, input_files):
    if input_files:
        return [os.path.join(source_path, name) for name in input_files]
    found = []
    for root, _, files in os.walk(source_path):
        for file in files:
            if file.endswith(".resx"):
                found.append(os.path.join(root, file))
    return sorted(found)


def main(config_file, store=None):
    with open(config_file, "r") as f:
        config = json.load(f)

    source_path = config.get("SourceRepoPath", "source-folder")
    target_langs = config.get("TargetLanguages", [])
    if not target_langs:
        print("❌ ERROR: TargetLanguages is empty in config.json")
        return 1
    if store is None:
        store = RunStore(os.environ["BUCKET"], os.environ["RUN_ID"])

    failures = 0
    uploaded = 0
    for resx_file in select_input_files(source_path, config.get("InputFiles", [])):
        if not os.path.isfile(resx_file):
            print(f"❌ ERROR: input file not found: {resx_file}")
            failures += 1
            continue
        base = os.path.splitext(os.path.basename(resx_file))[0]
        for lang in target_langs:
            xlf_filename = f"{base}.{lang}.xlf"
            try:
                resx_to_xlf(resx_file, lang).write(xlf_filename, encoding="utf-8", xml_declaration=True)
                store.upload_raw(xlf_filename, xlf_filename)
                uploaded += 1
                print(f"✅ Uploaded {xlf_filename} → s3://{store.bucket}/{store.key(RAW, xlf_filename)}")
            except Exception as e:
                failures += 1
                print(f"⚠️ ERROR processing {resx_file} for {lang}: {e}")
            finally:
                if os.path.exists(xlf_filename):
                    os.remove(xlf_filename)

    if uploaded == 0:
        print("❌ ERROR: no XLIFF files were uploaded")
        return max(failures, 1)
    print(f"🎉 Phase1 complete: {uploaded} XLIFF files uploaded, {failures} failures.")
    return failures


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase1.py config.json")
        sys.exit(2)
    sys.exit(1 if main(sys.argv[1]) else 0)
