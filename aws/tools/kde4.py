"""KDE4 pipeline runs: turn the KDE4 spreadsheet into .resx files for Phase 1, and score the localized output.

    python tools/kde4.py resx     Dataset-Translated.xlsx kde4.resx
    python tools/kde4.py versions Dataset-Translated.xlsx <folder>      # app versions for the memory experiment
    python tools/kde4.py score    Dataset-Translated.xlsx <downloaded runs/<id>/output/> --out scores.json
    python tools/kde4.py score    Dataset-Translated.xlsx <output/> --version <folder>/kde4tm-v2-r1.resx

All steps use the same cleaned rows (batching_bench.clean_kde4), so entry KDE4_<i> is row i.
"""
import argparse
import json
import os
import random
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "translator"))
from batching_bench import chrf, clean_kde4, placeholder_kept_fraction, read_kde4  # noqa: E402

# KDE4 spreadsheet columns -> pipeline target languages (config.kde4.json).
LOCALES = {"hi": "hi-IN", "ar": "ar-SA", "bn": "bn-IN", "de": "de-DE", "es": "es-ES", "fr": "fr-FR",
           "ja": "ja-JP", "mr": "mr-IN", "pa": "pa-IN", "ta": "ta-IN", "te": "te-IN", "zh": "zh-CN"}
RESX_NAME = "kde4.resx"


def write_resx(items, path, keys=None):
    """items as KDE4_<key> entries; keys are the rows' positions in the cleaned spreadsheet (default 0, 1, ...)."""
    root = ET.Element("root")
    for i, (english, _) in zip(keys if keys is not None else range(len(items)), items):
        data = ET.SubElement(root, "data", {"name": f"KDE4_{i}", "xml:space": "preserve"})
        ET.SubElement(data, "value").text = english
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def read_output(path):
    if not os.path.exists(path):
        return {}
    return {d.attrib.get("name"): d.find("value").text or "" for d in ET.parse(path).getroot().findall("data")}


def write_versions(items, out_dir, base=2000, sizes=None, repeats=3, seed=2026):
    """App versions for the memory experiment: v1 is `base` random rows; each of v2/v3/v4 (repeated `repeats`
    times) is v1 plus its own new rows, never used by any other version, so a kept memory has never seen them.
    Writes kde4tm-<name>.resx and manifest.json; returns {name: {"keys": [...], "new": n}}."""
    sizes = sizes or {"v2": 20, "v3": 100, "v4": 200}
    order = random.Random(seed).sample(range(len(items)), base + sum(sizes.values()) * repeats)
    v1, pool = order[:base], iter(order[base:])
    manifest = {"v1": {"keys": sorted(v1), "new": base}}
    for r in range(1, repeats + 1):
        for name, size in sizes.items():
            new = [next(pool) for _ in range(size)]
            manifest[f"{name}-r{r}"] = {"keys": sorted(v1 + new), "new": size}
    os.makedirs(out_dir, exist_ok=True)
    for name, version in manifest.items():
        write_resx([items[k] for k in version["keys"]], os.path.join(out_dir, f"kde4tm-{name}.resx"), keys=version["keys"])
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return manifest


def score(items, langs, output_dir, keys=None, resx_name=RESX_NAME):
    """chrF against the spreadsheet and placeholders kept, per language and over all languages.
    keys: score only these rows (a version of the app); default every row."""
    keys = list(keys) if keys is not None else list(range(len(items)))
    sources = [items[k][0] for k in keys]
    per_language, all_out, all_refs, all_src = {}, [], [], []
    for lang in langs:
        found = read_output(os.path.join(output_dir, LOCALES[lang], resx_name))
        outputs = [found.get(f"KDE4_{k}") or "" for k in keys]
        refs = [items[k][1][lang] for k in keys]
        per_language[lang] = {"strings": len(keys), "missing": sum(not o for o in outputs),
                              "chrf": chrf(outputs, refs), "placeholders_kept": placeholder_kept_fraction(sources, outputs)}
        all_out += outputs
        all_refs += refs
        all_src += sources
    return {"strings": len(keys), "languages": langs, "translations": len(all_out),
            "missing": sum(p["missing"] for p in per_language.values()),
            "chrf_all": chrf(all_out, all_refs), "placeholders_kept_all": placeholder_kept_fraction(all_src, all_out),
            "per_language": per_language}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["resx", "versions", "score"])
    parser.add_argument("xlsx")
    parser.add_argument("target", help="resx: the .resx to write; versions: the folder to write; score: the downloaded output folder")
    parser.add_argument("--version", help="score: only the rows in this version file (kde4tm-<name>.resx)")
    parser.add_argument("--out", help="score: also write the report here as JSON")
    args = parser.parse_args(argv)

    langs, rows = read_kde4(args.xlsx)
    items = clean_kde4(rows, langs)
    if args.step == "resx":
        write_resx(items, args.target)
        print(f"Wrote {len(items)} strings ({len(rows)} rows in the spreadsheet) to {args.target}")
        return 0
    if args.step == "versions":
        manifest = write_versions(items, args.target)
        print(f"Wrote {len(manifest)} versions to {args.target}: " + ", ".join(f"{n} ({len(v['keys'])} strings, {v['new']} new)" for n, v in manifest.items()))
        return 0
    if args.version:
        keys = [int(name.split("_")[1]) for name in read_output(args.version)]
        report = score(items, langs, args.target, keys=keys, resx_name="kde4tm.resx")
    else:
        report = score(items, langs, args.target)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
