"""Placeholder protection for machine translation.

Software strings carry slots the program fills in later (%1, %s, {name}, <b>). The model treats
them as ordinary text and sometimes drops or changes them, so before translation each one is
swapped for a numbered marker the model tends to copy, and after translation the markers are
swapped back. restore() reports whether every marker came back exactly once.
"""
import re

PLACEHOLDER = re.compile(r"%\d+|%[sdif]|\{[^}]*\}|<[^>]+>")

# marker written for placeholder i, and a pattern that also accepts what the model turns it into:
# full-width brackets (Chinese, Japanese), spaces inside, and native digits (\d matches ०, ٠, ১, ...).
STYLES = {
    "square": ("[{i}]", re.compile(r"[\[［]\s*(\d+)\s*[\]］]")),
    "section": ("§{i}§", re.compile(r"§\s*(\d+)\s*§")),
    "curly": ("{{{i}}}", re.compile(r"[{｛]\s*(\d+)\s*[}｝]")),
}


def protect(text, style="square"):
    """(masked text, original placeholders in order). Text is left alone when it has no
    placeholders, or already contains something that looks like this style's marker."""
    marker, pattern = STYLES[style]
    originals = PLACEHOLDER.findall(text)
    if not originals or pattern.search(text):
        return text, []
    counter = iter(range(len(originals)))
    return PLACEHOLDER.sub(lambda m: marker.format(i=next(counter)), text), originals


def restore(text, originals, style="square"):
    """(text with the originals put back, True when every marker appeared exactly once)."""
    if not originals:
        return text, True
    _, pattern = STYLES[style]
    found = [int(m.group(1)) for m in pattern.finditer(text)]
    ok = sorted(found) == list(range(len(originals)))
    restored = pattern.sub(lambda m: originals[int(m.group(1))] if int(m.group(1)) < len(originals) else m.group(0), text)
    return restored, ok


def placeholders_match(source, output):
    """True when the output holds exactly the source's placeholders (order may differ)."""
    return sorted(PLACEHOLDER.findall(source)) == sorted(PLACEHOLDER.findall(output))
