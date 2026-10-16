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


def test_output_is_scored_per_language_against_the_spreadsheet(tmp_path):
    write_output(tmp_path, "de-DE", ["Öffne %1", "<b>Datei</b> speichern"])          # same as the references
    write_output(tmp_path, "fr-FR", ["Ouvrir", "Enregistrer <b>fichier</b>"])       # %1 lost

    report = kde4.score(ITEMS, ["de", "fr"], tmp_path)

    assert report["per_language"]["de"] == {"strings": 2, "missing": 0, "chrf": 100.0, "placeholders_kept": 1.0}
    assert report["per_language"]["fr"]["placeholders_kept"] == 0.5
    assert report["per_language"]["fr"]["chrf"] < 100
    assert report["translations"] == 4 and report["placeholders_kept_all"] == 0.75


def test_a_missing_output_file_or_string_is_counted(tmp_path):
    write_output(tmp_path, "de-DE", ["Öffne %1"])

    report = kde4.score(ITEMS, ["de", "fr"], tmp_path)

    assert report["per_language"]["de"]["missing"] == 1
    assert report["per_language"]["fr"]["missing"] == 2
