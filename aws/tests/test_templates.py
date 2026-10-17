"""Near-twin reuse: a saved translation adapted to a string that differs only in numbers, placeholders
or trailing punctuation."""
import pytest

import templates as tp


def test_twins_share_a_template():
    assert tp.template_of("20 Cards") == tp.template_of("8 Cards")
    assert tp.template_of("Copy %1 to %2") == tp.template_of("Copy %3 to %4")
    assert tp.template_of("Column width:") == tp.template_of("Column width") == tp.template_of("Column width...")
    assert tp.template_of("Cards") != tp.template_of("20 Cards")
    assert tp.template_of("Border") != tp.template_of("border")          # case is not ignored


def test_numbers_are_swapped_in():
    assert tp.adapt("20 Cards", "20 Karten", "8 Cards", "de-DE") == "8 Karten"
    assert tp.adapt("CadetBlue1", "CadetBlue1", "CadetBlue4", "fr-FR") == "CadetBlue4"


def test_placeholders_are_swapped_even_when_the_translation_reorders_them():
    assert tp.adapt("Copy %1 to %2", "Nach %2 kopieren: %1", "Copy %3 to %4", "de-DE") == "Nach %4 kopieren: %3"


def test_a_number_inside_a_longer_number_is_not_touched():
    assert tp.adapt("2 Cards", "2 Karten (2024)", "5 Cards", "de-DE") == "5 Karten (2024)"


@pytest.mark.parametrize("saved_source, saved_translation, new_source", [
    ("20 Cards", "٢٠ بطاقة", "8 Cards"),                # the model wrote the number in Arabic digits
    ("1 of 1", "1 von 1", "2 of 2"),                     # repeated value: which one maps where is unknown
    ("20 Cards", "Karten", "8 Cards"),                   # the number was dropped from the translation
    ("Open file", "Datei öffnen", "Close file"),         # not twins
])
def test_reuse_that_cannot_be_done_cleanly_is_refused(saved_source, saved_translation, new_source):
    assert tp.adapt(saved_source, saved_translation, new_source, "de-DE") is None


def test_trailing_punctuation_is_removed_or_added():
    assert tp.adapt("Border:", "Rahmen:", "Border", "de-DE") == "Rahmen"
    assert tp.adapt("Border", "Rahmen", "Border:", "de-DE") == "Rahmen:"
    assert tp.adapt("Save...", "Speichern...", "Save!", "de-DE") == "Speichern!"


def test_trailing_punctuation_follows_the_target_language_style():
    assert tp.adapt("Border", "边框", "Border:", "zh-CN") == "边框："
    assert tp.adapt("Done!", "完了！", "Done", "ja-JP") == "完了"
    assert tp.adapt("Done", "完成", "Done?", "zh-CN") == "完成？"
    assert tp.adapt("Exit", "بيرون", "Exit?", "ar-SA") == "بيرون؟"


def test_an_identical_string_is_returned_unchanged():
    assert tp.adapt("Open", "Öffnen", "Open", "de-DE") == "Öffnen"
