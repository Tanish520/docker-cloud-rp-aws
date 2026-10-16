"""Placeholder protection: mask %1, {name}, <tag> before translation and put them back after."""
import pytest

import placeholders as ph


@pytest.mark.parametrize("style, marker", [("square", "[0]"), ("section", "§0§"), ("curly", "{0}")])
def test_protect_swaps_each_placeholder_for_a_numbered_marker(style, marker):
    masked, originals = ph.protect("Could not find conduit %1.", style)
    assert masked == f"Could not find conduit {marker}."
    assert originals == ["%1"]


def test_protect_numbers_every_kind_of_placeholder_in_order():
    masked, originals = ph.protect("Copy %1 to <b>{target}</b> as %s", "square")
    assert masked == "Copy [0] to [1][2][3] as [4]"
    assert originals == ["%1", "<b>", "{target}", "</b>", "%s"]


def test_text_without_placeholders_is_left_alone():
    assert ph.protect("Edit Patch", "square") == ("Edit Patch", [])


def test_text_that_already_looks_like_a_marker_is_not_masked():
    # Masking "See [0] and %1" would make the restore ambiguous, so it is sent as it is.
    assert ph.protect("See [0] and %1", "square") == ("See [0] and %1", [])


def test_restore_puts_the_originals_back_and_reports_success():
    assert ph.restore("Leitung [0] nicht gefunden.", ["%1"], "square") == ("Leitung %1 nicht gefunden.", True)


@pytest.mark.parametrize("translated", ["找不到管道 ［0］。", "找不到管道 [ 0 ]。", "[०] नहीं मिला", "[٠] غير موجود"])
def test_restore_accepts_full_width_brackets_spaces_and_native_digits(translated):
    restored, ok = ph.restore(translated, ["%1"], "square")
    assert ok and "%1" in restored and "0" not in restored.replace("%1", "")


def test_restore_reports_a_missing_or_repeated_marker():
    assert ph.restore("Kopieren nach [1]", ["%1", "%2"], "square")[1] is False      # [0] lost
    assert ph.restore("[0] und [0]", ["%1"], "square")[1] is False                    # [0] twice
    assert ph.restore("[0] [7]", ["%1"], "square")[1] is False                        # unknown marker


def test_restore_with_nothing_protected_is_a_no_op():
    assert ph.restore("Bearbeiten", [], "square") == ("Bearbeiten", True)


def test_placeholders_match_compares_the_exact_set():
    assert ph.placeholders_match("Copy %1 to %2", "Kopiere %2 nach %1")
    assert not ph.placeholders_match("Copy %1 to %2", "Kopiere %1")
    assert ph.placeholders_match("Plain", "Schlicht")
