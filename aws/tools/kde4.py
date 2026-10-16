"""KDE4 pipeline run: turn the KDE4 spreadsheet into one .resx for Phase 1, and score the localized output.

    python tools/kde4.py resx  Dataset-Translated.xlsx kde4.resx
    python tools/kde4.py score Dataset-Translated.xlsx <downloaded runs/<id>/output/> --out scores.json

Both steps use the same cleaned rows (batching_bench.clean_kde4), so entry KDE4_<i> is row i.
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "translator"))
from batching_bench import chrf, clean_kde4, placeholder_kept_fraction, read_kde4  # noqa: E402

# KDE4 spreadsheet columns -> pipeline target languages (config.kde4.json).
LOCALES = {"hi": "hi-IN", "ar": "ar-SA", "bn": "bn-IN", "de": "de-DE", "es": "es-ES", "fr": "fr-FR",
           "ja": "ja-JP", "mr": "mr-IN", "pa": "pa-IN", "ta": "ta-IN", "te": "te-IN", "zh": "zh-CN"}
RESX_NAME = "kde4.resx"


def write_resx(items, path):
    root = ET.Element("root")
    for i, (english, _) in enumerate(items):
        data = ET.SubElement(root, "data", {"name": f"KDE4_{i}", "xml:space": "preserve"})
        ET.SubElement(data, "value").text = english
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def read_output(path):
    if not os.path.exists(path):
        return {}
    return {d.attrib.get("name"): d.find("value").text or "" for d in ET.parse(path).getroot().findall("data")}


def score(items, langs, output_dir):
    """chrF against the spreadsheet and placeholders kept, per language and over all languages."""
    sources = [english for english, _ in items]
    per_language, all_out, all_refs, all_src = {}, [], [], []
    for lang in langs:
        found = read_output(os.path.join(output_dir, LOCALES[lang], RESX_NAME))
        outputs = [found.get(f"KDE4_{i}") or "" for i in range(len(items))]
        refs = [r[lang] for _, r in items]
        per_language[lang] = {"strings": len(items), "missing": sum(not o for o in outputs),
                              "chrf": chrf(outputs, refs), "placeholders_kept": placeholder_kept_fraction(sources, outputs)}
        all_out += outputs
        all_refs += refs
        all_src += sources
    return {"strings": len(items), "languages": langs, "translations": len(all_out),
            "missing": sum(p["missing"] for p in per_language.values()),
            "chrf_all": chrf(all_out, all_refs), "placeholders_kept_all": placeholder_kept_fraction(all_src, all_out),
            "per_language": per_language}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["resx", "score"])
    parser.add_argument("xlsx")
    parser.add_argument("target", help="resx: the .resx to write; score: the downloaded output folder")
    parser.add_argument("--out", help="score: also write the report here as JSON")
    args = parser.parse_args(argv)

    langs, rows = read_kde4(args.xlsx)
    items = clean_kde4(rows, langs)
    if args.step == "resx":
        write_resx(items, args.target)
        print(f"Wrote {len(items)} strings ({len(rows)} rows in the spreadsheet) to {args.target}")
        return 0
    report = score(items, langs, args.target)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
