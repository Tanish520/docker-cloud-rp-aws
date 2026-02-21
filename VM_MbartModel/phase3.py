import os
import sys
import json
import xml.etree.ElementTree as ET
from azure.storage.blob import BlobServiceClient

def xlf_to_resx(xlf_path, resx_path, target_lang, target_resx_path):
    tree = ET.parse(xlf_path)
    root = tree.getroot()

    orig_resx = ET.parse(resx_path)
    resx_root = orig_resx.getroot()

    translations = {}
    for tu in root.findall(".//trans-unit"):
        source = tu.find("source").text if tu.find("source") is not None else None
        target = tu.find("target").text if tu.find("target") is not None else None
        if source and target:
            translations[source] = target

    for data in resx_root.findall("data"):
        val_elem = data.find("value")
        if val_elem is not None and val_elem.text in translations:
            val_elem.text = translations[val_elem.text]

    lang_dir = os.path.join(target_resx_path, target_lang)
    os.makedirs(lang_dir, exist_ok=True)

    out_file = os.path.join(lang_dir, os.path.basename(resx_path))
    orig_resx.write(out_file, encoding="utf-8", xml_declaration=True)
    return out_file

def main(config_file):
    with open(config_file, "r") as f:
        config = json.load(f)

    storage_conn_str = config.get("blob_connection_string")
    translated_container = config.get("translated_container", "trans-files")
    source_path = config.get("SourceRepoPath", "source-folder")
    target_resx_path = config.get("TargetResxPath", "target-folder")

    if not storage_conn_str:
        print("❌ ERROR: Missing blob_connection_string in config.json")
        sys.exit(1)

    blob_service = BlobServiceClient.from_connection_string(storage_conn_str)
    trans_client = blob_service.get_container_client(translated_container)
    try:
        trans_client.create_container()
    except Exception:
        pass

    blobs = list(trans_client.list_blobs())
    if not blobs:
        print("ℹ️ No translated XLF files found.")
        return

    for blob in blobs:
        local_xlf = blob.name
        with open(local_xlf, "wb") as f:
            f.write(trans_client.download_blob(blob.name).readall())

        parts = blob.name.split(".")
        target_lang = parts[-2] if len(parts) >= 3 else "unknown"
        base_resx = os.path.join(source_path, parts[0] + ".resx")

        if not os.path.exists(base_resx):
            print(f"⚠️ WARNING: Base resx {base_resx} not found, skipping {blob.name}")
            os.remove(local_xlf)
            continue

        try:
            out_file = xlf_to_resx(local_xlf, base_resx, target_lang, target_resx_path)
            print(f"✅ Generated localized resx: {out_file}")
        except Exception as e:
            print(f"❌ ERROR converting {blob.name}: {e}")

        os.remove(local_xlf)

    print("🎉 Phase3 complete: Localized RESX files generated.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase3.py config.json")
        sys.exit(1)
    main(sys.argv[1])
