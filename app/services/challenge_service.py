"""Random challenge phrase generation and transcript matching (PRD Section 9.7)."""

import re
import uuid

from unidecode import unidecode

# Predefined phrase pools per language — simple, language-appropriate phrases
PHRASE_POOLS: dict[str, list[str]] = {
    "en": [
        "The sun rises over the mountain every morning",
        "Please verify my identity for this service",
        "My voice is my password and it is unique",
        "The market opens early on Wednesday",
        "I confirm this request with my own voice",
    ],
    "sw": [
        "Jua linachomoza juu ya mlima kila asubuhi",
        "Tafadhali thibitisha utambulisho wangu",
        "Sauti yangu ndiyo nenosiri langu",
        "Soko linafunguliwa mapema siku ya Jumatano",
        "Ninathibitisha ombi hili kwa sauti yangu",
    ],
}


def _normalize(text: str) -> str:
    """Normalize text for comparison: unidecode, lowercase, strip punctuation and extra spaces."""
    text = unidecode(text)
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)  # strip punctuation
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text


def _word_similarity(transcript: str, expected: str) -> tuple[float, int, int]:
    """Compute word-level similarity between transcript and expected phrase.

    Uses the expected phrase as the reference. For each expected word, checks
    if it appears in the transcript words (order-independent). Returns the
    ratio of matched words to total expected words.

    Returns:
        (score, matched_count, total_expected_words)
    """
    expected_words = expected.split()
    transcript_words = transcript.split()
    total = len(expected_words)
    if total == 0:
        return (1.0, 0, 0) if len(transcript_words) == 0 else (0.0, 0, 0)

    # Track which transcript words have been consumed to avoid double-counting
    available = list(transcript_words)
    matched = 0
    for word in expected_words:
        if word in available:
            available.remove(word)
            matched += 1

    return (matched / total, matched, total)


class ChallengeService:
    """Generates random challenge phrases and validates transcript matches."""

    def __init__(self, phrase_pools: dict[str, list[str]] | None = None):
        self._pools = phrase_pools or PHRASE_POOLS
        # In-memory store of challenge_id -> normalized phrase
        self._active: dict[str, str] = {}

    def generate_challenge(self, language: str = "en") -> dict:
        """Select a random phrase from the pool for the given language.

        Args:
            language: ISO 639-1 code ("en", "sw").

        Returns:
            {"challenge_id": str, "phrase_text": str}

        Raises:
            ValueError: If language is not in the phrase pool.
        """
        import random

        pool = self._pools.get(language)
        if pool is None:
            raise ValueError(f"No phrase pool for language '{language}'")

        phrase = random.choice(pool)
        challenge_id = str(uuid.uuid4())
        self._active[challenge_id] = _normalize(phrase)

        return {"challenge_id": challenge_id, "phrase_text": phrase}

    def match_transcript(
        self, challenge_id: str, transcript: str, threshold: float = 0.75
    ) -> dict:
        """Compare a transcript against the expected challenge phrase using word-level similarity.

        Args:
            challenge_id: The ID returned by generate_challenge.
            transcript: The Whisper ASR transcription to check.
            threshold: Minimum word-match ratio to pass (0.0–1.0).

        Returns:
            {"match": bool, "score": float, "matched_words": int, "total_words": int}

        Raises:
            KeyError: If challenge_id is not found.
        """
        expected = self._active.get(challenge_id)
        if expected is None:
            raise KeyError(f"Challenge ID '{challenge_id}' not found")

        score, matched, total = _word_similarity(_normalize(transcript), expected)
        return {
            "match": score >= threshold,
            "score": round(score, 4),
            "matched_words": matched,
            "total_words": total,
        }
