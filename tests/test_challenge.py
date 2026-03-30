"""Phase 8 validation: ChallengeService — phrase generation and transcript matching."""

import pytest

from app.services.challenge_service import ChallengeService, _normalize


@pytest.fixture()
def service() -> ChallengeService:
    return ChallengeService()


# ── Generation ───────────────────────────────────────────────────────────────

class TestGenerateChallenge:
    def test_english_returns_valid_dict(self, service: ChallengeService):
        result = service.generate_challenge(language="en")
        assert "challenge_id" in result
        assert "phrase_text" in result
        assert len(result["challenge_id"]) > 0
        assert len(result["phrase_text"]) > 0

    def test_swahili_returns_valid_dict(self, service: ChallengeService):
        result = service.generate_challenge(language="sw")
        assert "challenge_id" in result
        assert "phrase_text" in result
        assert len(result["phrase_text"]) > 0

    def test_unknown_language_raises(self, service: ChallengeService):
        with pytest.raises(ValueError, match="No phrase pool"):
            service.generate_challenge(language="xx")

    def test_unique_challenge_ids(self, service: ChallengeService):
        ids = {service.generate_challenge("en")["challenge_id"] for _ in range(10)}
        assert len(ids) == 10


# ── Transcript Matching ─────────────────────────────────────────────────────

class TestMatchTranscript:
    def test_exact_match(self, service: ChallengeService):
        challenge = service.generate_challenge("en")
        assert service.match_transcript(challenge["challenge_id"], challenge["phrase_text"]) is True

    def test_case_insensitive(self, service: ChallengeService):
        svc = ChallengeService(phrase_pools={"en": ["The quick brown fox"]})
        challenge = svc.generate_challenge("en")
        assert svc.match_transcript(challenge["challenge_id"], "THE QUICK BROWN FOX") is True

    def test_punctuation_stripped(self, service: ChallengeService):
        svc = ChallengeService(phrase_pools={"en": ["The quick brown fox"]})
        challenge = svc.generate_challenge("en")
        assert svc.match_transcript(challenge["challenge_id"], "The Quick, Brown Fox!") is True

    def test_mismatch(self, service: ChallengeService):
        svc = ChallengeService(phrase_pools={"en": ["Hello world"]})
        challenge = svc.generate_challenge("en")
        assert svc.match_transcript(challenge["challenge_id"], "Goodbye world") is False

    def test_unknown_challenge_id_raises(self, service: ChallengeService):
        with pytest.raises(KeyError):
            service.match_transcript("nonexistent-id", "some text")

    def test_extra_whitespace_handled(self, service: ChallengeService):
        svc = ChallengeService(phrase_pools={"en": ["The quick brown fox"]})
        challenge = svc.generate_challenge("en")
        assert svc.match_transcript(challenge["challenge_id"], "  the   quick  brown   fox  ") is True


# ── Swahili Normalization ────────────────────────────────────────────────────

class TestSwahiliNormalization:
    def test_swahili_exact_match(self, service: ChallengeService):
        challenge = service.generate_challenge("sw")
        assert service.match_transcript(challenge["challenge_id"], challenge["phrase_text"]) is True

    def test_swahili_ngombe_apostrophe(self):
        """Swahili ng' (velar nasal) with apostrophe should normalize cleanly."""
        # ng' is common in Swahili — the apostrophe should be stripped
        assert _normalize("ng'ombe") == _normalize("ng'ombe")
        assert _normalize("ng'ombe") == "ngombe"

    def test_swahili_with_accents(self):
        """Unidecode should handle any accented characters in Swahili."""
        assert _normalize("Tafadhali") == "tafadhali"
        # Accented variant (unlikely but possible in ASR output)
        assert _normalize("Tafádhalì") == "tafadhali"

    def test_swahili_mixed_case_punctuation(self):
        svc = ChallengeService(phrase_pools={"sw": ["Jua linachomoza juu ya mlima kila asubuhi"]})
        challenge = svc.generate_challenge("sw")
        assert svc.match_transcript(
            challenge["challenge_id"],
            "JUA LINACHOMOZA JUU YA MLIMA KILA ASUBUHI!"
        ) is True
