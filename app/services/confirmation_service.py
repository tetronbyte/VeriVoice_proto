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
