import os
import sys
import json
import xml.etree.ElementTree as ET
from azure.storage.blob import BlobServiceClient

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

def main(config_file):
    with open(config_file, "r") as f:
        config = json.load(f)

    storage_conn_str = config.get("blob_connection_string")
    source_path = config.get("SourceRepoPath", "source-folder")
    target_langs = config.get("TargetLanguages", [])
    raw_container = config.get("raw_container", "raw-files")

    if not storage_conn_str:
        print("❌ ERROR: Missing blob_connection_string in config.json")
        sys.exit(1)

    blob_service = BlobServiceClient.from_connection_string(storage_conn_str)
    container_client = blob_service.get_container_client(raw_container)
    try:
        container_client.create_container()
    except Exception:
        pass

    print(f"📂 Processing .resx files in {source_path}...")

    for root, _, files in os.walk(source_path):
        for file in files:
            if not file.endswith(".resx"):
                continue

            resx_file = os.path.join(root, file)
            for lang in target_langs:
                try:
                    xlf_tree = resx_to_xlf(resx_file, lang)
                    xlf_filename = f"{os.path.splitext(file)[0]}.{lang}.xlf"

                    xlf_tree.write(xlf_filename, encoding="utf-8", xml_declaration=True)

                    blob_client = container_client.get_blob_client(xlf_filename)
                    with open(xlf_filename, "rb") as data:
                        blob_client.upload_blob(data, overwrite=True)

                    os.remove(xlf_filename)
                    print(f"✅ Uploaded {xlf_filename} → {raw_container}")
                except Exception as e:
                    print(f"⚠️ ERROR processing {file} for {lang}: {e}")

    print("🎉 Phase1 complete: Extracted and uploaded XLIFF files.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase1.py config.json")
        sys.exit(1)
    main(sys.argv[1])
