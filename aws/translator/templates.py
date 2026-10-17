"""Near-twin reuse for the translation memory.

Two strings are twins when they differ only in numbers, placeholders (%1, {name}, <b>) or trailing
punctuation: "20 Cards" / "8 Cards", "Copy %1 to %2" / "Copy %3 to %4", "Border:" / "Border".
adapt() turns the saved translation of one twin into a translation of the other, or returns None when
that cannot be done cleanly, in which case the string goes to the model as usual.
"""
import re

SLOT = re.compile(r"%\d+|%[sdif]|\{[^}]*\}|<[^>]+>|\d+")
SOURCE_TAIL = re.compile(r"[\s.:!?…]+$")
TRANSLATION_TAIL = re.compile(r"[\s.:!?…。：！？؟।]+$")
# Trailing punctuation the KDE4 references use most per language (counted over every row ending in it);
# anything not listed is kept as written. "..." is kept everywhere.
TAIL_STYLE = {
    "zh": {":": "：", "?": "？", "!": "！", ".": "。"},
    "ja": {"?": "？", "!": "！", ".": "。"},
    "ar": {"?": "؟"},
    "hi": {".": "।"}, "bn": {".": "।"}, "pa": {".": "।"},
}


def template_of(text):
    """What twins share: the text with every slot blanked and trailing punctuation removed."""
    return SOURCE_TAIL.sub("", SLOT.sub("¤", text))


def _tail(text):
    match = SOURCE_TAIL.search(text)
    return match.group(0).strip() if match else ""


def _styled(tail, target_lang):
    """Trailing punctuation as the target language writes it."""
    style = TAIL_STYLE.get(target_lang.split("-")[0], {})
    return style.get(tail, tail)


def _value_pattern(value):
    pattern = re.escape(value)
    if value[0].isdigit():
        pattern = r"(?<!\d)" + pattern
    if value[-1].isdigit():
        pattern += r"(?!\d)"
    return pattern


def _swap_slots(old_values, new_values, translation):
    if old_values == new_values:
        return translation
    if len(set(old_values)) != len(old_values):
        return None                       # "1 of 1": cannot tell which value maps where
    patterns = {value: _value_pattern(value) for value in old_values}
    if any(len(re.findall(pattern, translation)) != 1 for pattern in patterns.values()):
        return None                       # value dropped, repeated or written differently (e.g. other digits)
    mapping = dict(zip(old_values, new_values))
    combined = re.compile("|".join(f"(?:{patterns[v]})" for v in sorted(old_values, key=len, reverse=True)))
    return combined.sub(lambda m: mapping[m.group(0)], translation)


def adapt(saved_source, saved_translation, new_source, target_lang):
    if template_of(saved_source) != template_of(new_source):
        return None
    translation = _swap_slots(SLOT.findall(saved_source), SLOT.findall(new_source), saved_translation)
    if translation is None:
        return None
    old_tail, new_tail = _tail(saved_source), _tail(new_source)
    if old_tail == new_tail:
        return translation
    if old_tail:
        translation = TRANSLATION_TAIL.sub("", translation)
    return translation + _styled(new_tail, target_lang)
