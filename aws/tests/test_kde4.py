"""KDE4 pipeline run: the spreadsheet becomes one .resx for Phase 1, and the localized output is scored."""
import xml.etree.ElementTree as ET

import kde4

ITEMS = [("Open %1", {"de": "Öffne %1", "fr": "Ouvrir %1"}),
         ("Save <b>file</b>", {"de": "<b>Datei</b> speichern", "fr": "Enregistrer <b>fichier</b>"})]


def resx_values(path):
    return {d.attrib["name"]: d.find("value").text for d in ET.parse(path).getroot().findall("data")}


def test_spreadsheet_strings_become_numbered_resx_entries(tmp_path):
    path = tmp_path / "kde4.resx"
    kde4.write_resx(ITEMS, path)
    assert resx_values(path) == {"KDE4_0": "Open %1", "KDE4_1": "Save <b>file</b>"}


def test_kde4_columns_map_to_pipeline_locales():
    assert kde4.LOCALES["pa"] == "pa-IN" and kde4.LOCALES["zh"] == "zh-CN" and len(kde4.LOCALES) == 12


def write_output(folder, locale, values):
    (folder / locale).mkdir(parents=True)
    kde4.write_resx([(v, {}) for v in values], folder / locale / "kde4.resx")


def test_output_is_scored_per_language_against_the_spreadsheet(tmp_path, monkeypatch):
    monkeypatch.setattr(kde4, "SPBLEU_TOKENIZE", "char")
    write_output(tmp_path, "de-DE", ["Öffne %1", "<b>Datei</b> speichern"])          # same as the references
    write_output(tmp_path, "fr-FR", ["Ouvrir", "Enregistrer <b>fichier</b>"])       # %1 lost

    report = kde4.score(ITEMS, ["de", "fr"], tmp_path)

    de = report["per_language"]["de"]
    assert {k: de[k] for k in ("strings", "missing", "chrf", "placeholders_kept")} == {"strings": 2, "missing": 0, "chrf": 100.0, "placeholders_kept": 1.0}
    assert report["per_language"]["fr"]["placeholders_kept"] == 0.5
    assert report["per_language"]["fr"]["chrf"] < 100
    assert report["translations"] == 4 and report["placeholders_kept_all"] == 0.75


def test_a_missing_output_file_or_string_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(kde4, "SPBLEU_TOKENIZE", "char")
    write_output(tmp_path, "de-DE", ["Öffne %1"])

    report = kde4.score(ITEMS, ["de", "fr"], tmp_path)

    assert report["per_language"]["de"]["missing"] == 1
    assert report["per_language"]["fr"]["missing"] == 2


def test_versions_add_new_strings_never_seen_in_another_version(tmp_path):
    items = [(f"String {i}", {"de": f"Zeile {i}"}) for i in range(40)]
    manifest = kde4.write_versions(items, tmp_path, base=10, sizes={"v2": 1, "v3": 2, "v4": 3}, repeats=2)

    assert sorted(manifest) == ["v1", "v2-r1", "v2-r2", "v3-r1", "v3-r2", "v4-r1", "v4-r2"]
    v1 = set(manifest["v1"]["keys"])
    assert len(v1) == 10 and manifest["v1"]["new"] == 10
    new_sets = []
    for name in ("v2-r1", "v3-r1", "v4-r1", "v2-r2", "v3-r2", "v4-r2"):
        keys = set(manifest[name]["keys"])
        assert v1 < keys
        new_sets.append(keys - v1)
        assert manifest[name]["new"] == len(keys - v1)
    assert [len(n) for n in new_sets] == [1, 2, 3, 1, 2, 3]
    assert len(set().union(*new_sets)) == 12                      # all new strings are different
    values = resx_values(tmp_path / "kde4tm-v2-r1.resx")
    assert len(values) == 11 and all(values[f"KDE4_{k}"] == f"String {k}" for k in manifest["v2-r1"]["keys"])


def test_a_version_is_scored_on_its_own_strings_only(tmp_path, monkeypatch):
    monkeypatch.setattr(kde4, "SPBLEU_TOKENIZE", "char")
    keys = [1]
    (tmp_path / "de-DE").mkdir()
    kde4.write_resx([ITEMS[1]], tmp_path / "de-DE" / "kde4tm.resx", keys=keys)
    for d in ("de-DE",):
        values = resx_values(tmp_path / d / "kde4tm.resx")
        assert values == {"KDE4_1": "Save <b>file</b>"}

    report = kde4.score(ITEMS, ["de"], tmp_path, keys=keys, resx_name="kde4tm.resx")

    assert report["strings"] == 1 and report["per_language"]["de"]["missing"] == 0


def test_bleu_uses_word_splitting_suited_to_each_language():
    assert kde4.bleu_tokenizer("de") == "13a"
    assert kde4.bleu_tokenizer("zh") == "zh"
    assert kde4.bleu_tokenizer("ja") in ("ja-mecab", "char")      # MeCab when installed, else characters


def test_perfect_output_scores_100_bleu_and_spbleu(tmp_path, monkeypatch):
    monkeypatch.setattr(kde4, "SPBLEU_TOKENIZE", "char")         # tests stay offline; the real run uses flores200
    write_output(tmp_path, "de-DE", ["Öffne %1", "<b>Datei</b> speichern"])

    report = kde4.score(ITEMS, ["de"], tmp_path)

    assert report["per_language"]["de"]["bleu"] == 100.0
    assert report["per_language"]["de"]["spbleu"] == 100.0
    assert report["spbleu_all"] == 100.0


def test_comet_is_scored_on_a_fixed_sample_per_language(tmp_path):
    write_output(tmp_path, "de-DE", ["Öffne %1", "<b>Datei</b> speichern"])
    write_output(tmp_path, "fr-FR", ["Ouvrir", "Enregistrer <b>fichier</b>"])
    seen = []

    def fake_comet(rows):
        seen.extend(rows)
        return [0.9 if r["mt"].startswith("Ö") or r["mt"].startswith("<b>") else 0.5 for r in rows]

    report = kde4.comet_scores(ITEMS, ["de", "fr"], tmp_path, per_language=1, predict=fake_comet)

    assert len(seen) == 2 and {"src", "mt", "ref"} <= set(seen[0])
    assert set(report["per_language"]) == {"de", "fr"} and report["per_language"]["de"]["strings"] == 1
    again = kde4.comet_scores(ITEMS, ["de", "fr"], tmp_path, per_language=1, predict=fake_comet)
    assert again == report                                        # the same sample every time
    assert report["comet_mean"] == round(sum(v["comet"] for v in report["per_language"].values()) / 2, 4)
