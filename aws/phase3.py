import os
import sys
import json
import xml.etree.ElementTree as ET

from translator.s3_store import RunStore


# Identical to azure/API_based_HFace_AppInsight/phase3.py (enforced by tests/test_parity.py).
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


def main(config_file, store=None):
    with open(config_file, "r") as f:
        config = json.load(f)

    source_path = config.get("SourceRepoPath", "source-folder")
    target_resx_path = config.get("TargetResxPath", "localized")
    if store is None:
        store = RunStore(os.environ["BUCKET"], os.environ["RUN_ID"])

    translated = store.list_translated()
    if not translated:
        print(f"❌ ERROR: no translated XLF files in s3://{store.bucket}/{store.prefix('translated')}")
        return 1

    failures = 0
    for item in translated:
        parts = item.name.split(".")
        target_lang = parts[-2] if len(parts) >= 3 else "unknown"
        base_resx = os.path.join(source_path, parts[0] + ".resx")
        if len(parts) < 3 or not os.path.exists(base_resx):
            print(f"❌ ERROR: cannot map {item.name} to a source .resx ({base_resx})")
            failures += 1
            continue

        local_xlf = item.name
        try:
            store.download_translated(item.name, local_xlf)
            out_file = xlf_to_resx(local_xlf, base_resx, target_lang, target_resx_path)
            store.upload_output(out_file, f"{target_lang}/{os.path.basename(out_file)}")
            print(f"✅ Generated localized resx: {out_file}")
        except Exception as e:
            failures += 1
            print(f"❌ ERROR converting {item.name}: {e}")
        finally:
            if os.path.exists(local_xlf):
                os.remove(local_xlf)

    print(f"🎉 Phase3 complete: {len(translated) - failures} localized files, {failures} failures.")
    return failures


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python phase3.py config.json")
        sys.exit(2)
    sys.exit(1 if main(sys.argv[1]) else 0)
