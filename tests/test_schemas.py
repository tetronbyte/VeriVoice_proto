"""Phase 10 validation: Pydantic schema parsing and validation."""

import base64
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.enrollment import EnrollmentRequest, EnrollmentResponse
from app.schemas.authentication import AuthenticationResponse, AuthResult, ChallengeResponse
from app.schemas.consent import ConsentResponse, ServiceAccessResponse


# ── EnrollmentRequest ────────────────────────────────────────────────────────

class TestEnrollmentRequest:
    def test_valid(self):
        req = EnrollmentRequest(
            national_id_number="KE-123456",
            preferred_language="en",
            phone_number="+254700000000",
        )
        assert req.national_id_number == "KE-123456"
        assert req.preferred_language == "en"

    def test_missing_national_id(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(phone_number="+254700000000")

    def test_empty_national_id(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(national_id_number="", phone_number="+254700000000")

    def test_missing_phone_number(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(national_id_number="KE-123456")

    def test_invalid_phone_no_plus(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(national_id_number="KE-123456", phone_number="254700000000")

    def test_invalid_phone_too_short(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(national_id_number="KE-123456", phone_number="+123")

    def test_invalid_language_format(self):
        with pytest.raises(ValidationError):
            EnrollmentRequest(national_id_number="KE-123456", phone_number="+254700000000", preferred_language="eng")

    def test_default_language(self):
        req = EnrollmentRequest(national_id_number="KE-123456", phone_number="+254700000000")
        assert req.preferred_language == "en"


# ── EnrollmentResponse ───────────────────────────────────────────────────────

class TestEnrollmentResponse:
    def test_valid(self):
        resp = EnrollmentResponse(
            citizen_id="550e8400-e29b-41d4-a716-446655440000",
            enrolled_at=datetime.now(timezone.utc),
            template_id="660e8400-e29b-41d4-a716-446655440000",
            status="enrolled",
        )
        assert resp.status == "enrolled"
        assert resp.citizen_id == "550e8400-e29b-41d4-a716-446655440000"


# ── AuthenticationResponse ───────────────────────────────────────────────────

class TestAuthenticationResponse:
    def test_valid_granted(self):
        resp = AuthenticationResponse(
            event_id="770e8400-e29b-41d4-a716-446655440000",
            voice_match_score=0.87,
            transcript_match=True,
            result=AuthResult.GRANTED,
            event_timestamp=datetime.now(timezone.utc),
        )
        assert resp.result == AuthResult.GRANTED
        assert resp.voice_match_score == 0.87

    def test_valid_denied(self):
        resp = AuthenticationResponse(
            event_id="770e8400-e29b-41d4-a716-446655440000",
            voice_match_score=0.30,
            transcript_match=False,
            result=AuthResult.DENIED,
            event_timestamp=datetime.now(timezone.utc),
        )
        assert resp.result == AuthResult.DENIED

    def test_score_out_of_range(self):
        with pytest.raises(ValidationError):
            AuthenticationResponse(
                event_id="x",
                voice_match_score=1.5,
                transcript_match=True,
                result="granted",
                event_timestamp=datetime.now(timezone.utc),
            )

    def test_invalid_result_value(self):
        with pytest.raises(ValidationError):
            AuthenticationResponse(
                event_id="x",
                voice_match_score=0.5,
                transcript_match=True,
                result="maybe",
                event_timestamp=datetime.now(timezone.utc),
            )


# ── ChallengeResponse ───────────────────────────────────────────────────────

class TestChallengeResponse:
    def test_valid(self):
        resp = ChallengeResponse(
            challenge_id="880e8400-e29b-41d4-a716-446655440000",
            phrase_text="The sun rises over the mountain",
            audio_url="/static/challenges/abc.wav",
        )
        assert resp.phrase_text == "The sun rises over the mountain"

    def test_audio_url_optional(self):
        resp = ChallengeResponse(
            challenge_id="880e8400-e29b-41d4-a716-446655440000",
            phrase_text="Hello",
        )
        assert resp.audio_url is None


# ── ConsentResponse ──────────────────────────────────────────────────────────

class TestConsentResponse:
    def test_valid_with_bytes(self):
        sig = b"\x01\x02\x03" * 20
        resp = ConsentResponse(
            token_id="990e8400-e29b-41d4-a716-446655440000",
            ministry_code="MOH",
            data_scope="health_records",
            issued_at=datetime.now(timezone.utc),
            digital_signature=sig,
        )
        assert resp.digital_signature == sig

    def test_base64_serialization(self):
        sig = b"\xde\xad\xbe\xef"
        resp = ConsentResponse(
            token_id="x",
            ministry_code="MOH",
            data_scope="health_records",
            issued_at=datetime.now(timezone.utc),
            digital_signature=sig,
        )
        json_data = resp.model_dump(mode="json")
        assert json_data["digital_signature"] == base64.b64encode(sig).decode("ascii")

    def test_base64_deserialization(self):
        sig_bytes = b"\xca\xfe\xba\xbe"
        b64_str = base64.b64encode(sig_bytes).decode("ascii")
        resp = ConsentResponse(
            token_id="x",
            ministry_code="MOH",
            data_scope="health_records",
            issued_at=datetime.now(timezone.utc),
            digital_signature=b64_str,
        )
        assert resp.digital_signature == sig_bytes


# ── ServiceAccessResponse ────────────────────────────────────────────────────

class TestServiceAccessResponse:
    def test_valid(self):
        resp = ServiceAccessResponse(
            form_id="aa0e8400-e29b-41d4-a716-446655440000",
            question="What is your full name?",
            transcribed_answer="John Doe",
            status="completed",
        )
        assert resp.transcribed_answer == "John Doe"

    def test_default_status(self):
        resp = ServiceAccessResponse(
            form_id="x",
            question="Q",
            transcribed_answer="A",
        )
        assert resp.status == "completed"
