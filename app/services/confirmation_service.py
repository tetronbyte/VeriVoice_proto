"""Yes/No intent classification for IVR confirmations (consent, form readback).

Used when the caller speaks a short affirmative/negative response into Twilio's
<Record> and the transcript comes back from Whisper / w2v-BERT. We don't need
phrase matching (that's challenge_service) — we just need to detect intent
from a small vocabulary of yes/no words in English and Swahili.
"""

from app.services.challenge_service import _normalize

# Word lists per language. Keep these small and high-confidence —
# add more only if real-world callers use them.
_AFFIRMATIVE: dict[str, set[str]] = {
    "en": {"yes", "yeah", "yep", "yup", "correct", "right", "ok", "okay",
           "affirmative", "confirm", "confirmed", "sure", "true"},
    "sw": {"ndiyo", "ndio", "sawa", "sahihi", "kubali", "nakubali", "kweli"},
}

_NEGATIVE: dict[str, set[str]] = {
    "en": {"no", "nope", "nah", "wrong", "incorrect", "negative", "false",
           "deny", "decline", "cancel"},
    "sw": {"hapana", "la", "si", "sikubali", "sio", "kosa"},
}


_QUESTION_NUMBER_WORDS: dict[str, dict[str, int]] = {
    "en": {
        "one": 0, "1": 0, "first": 0, "number one": 0,
        "two": 1, "2": 1, "second": 1, "number two": 1,
        "three": 2, "3": 2, "third": 2, "number three": 2,
    },
    "sw": {
        "moja": 0, "1": 0, "kwanza": 0, "ya kwanza": 0,
        "mbili": 1, "2": 1, "pili": 1, "ya pili": 1,
        "tatu": 2, "3": 2, "tatu": 2, "ya tatu": 2,
    },
}


def parse_question_number(transcript: str, lang: str = "en") -> int | None:
    """Classify a transcript as a question index 0/1/2 (for "one/two/three").

    Recognizes digits ("1", "2", "3"), English ordinals ("first", "second",
    "third"), and Swahili equivalents ("moja", "mbili", "tatu", "kwanza"
    etc.). Returns None if nothing parseable is found.
    """
    if not transcript or not transcript.strip():
        return None

    norm = _normalize(transcript)
    tokens = norm.split()
    if not tokens:
        return None

    words = _QUESTION_NUMBER_WORDS.get(lang, _QUESTION_NUMBER_WORDS["en"])

    # Check each individual token first (most common case: "two" or "2")
    for tok in tokens:
        if tok in words:
            return words[tok]

    # Check multi-word phrases ("number one", "ya kwanza")
    for phrase, idx in words.items():
        if " " in phrase and phrase in norm:
            return idx

    return None


def classify_yes_no(transcript: str, lang: str = "en") -> str:
    """Classify a transcript as "yes", "no", or "unclear".

    Args:
        transcript: Raw ASR output (may contain extra words like "yes please").
        lang: ISO 639-1 code ("en" or "sw"). Falls back to English word lists
              for unknown languages.

    Returns:
        "yes" if any affirmative word is present,
        "no" if any negative word is present,
        "unclear" if neither matches (or both — rare, treated as unclear).
    """
    if not transcript or not transcript.strip():
        return "unclear"

    words = set(_normalize(transcript).split())
    if not words:
        return "unclear"

    yes_set = _AFFIRMATIVE.get(lang, _AFFIRMATIVE["en"])
    no_set = _NEGATIVE.get(lang, _NEGATIVE["en"])

    has_yes = bool(words & yes_set)
    has_no = bool(words & no_set)

    if has_yes and not has_no:
        return "yes"
    if has_no and not has_yes:
        return "no"
    return "unclear"
