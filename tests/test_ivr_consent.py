"""Tests for the IVR consent flow: prompt, callback, and background pipeline.

Covers:
  POST /twilio/voice/consent           -- consent prompt (en/sw, clarification)
  POST /twilio/voice/consent/callback  -- recording handoff to background pipeline
  Background: _run_consent_pipeline    -- yes/no/unclear classification, Ed25519 signing
"""

import uuid
from unittest.mock import patch

import pytest

from app.models.consent_token import ConsentToken
from conftest import TestSessionLocal, parse_twiml


# ============================================================================
# POST /twilio/voice/consent
# ============================================================================


class TestConsentPrompt:
    """Tests for the consent prompt endpoint."""

    def test_valid_citizen_plays_consent_text_en(self, enrolled_citizen):
        """English consent prompt should contain <Say> with consent text."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}",
        )
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]

        root = parse_twiml(resp.text)

        # English uses <Say> elements
        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "consent" in all_text.lower(), (
            f"Expected consent text in English prompt, got: {all_text}"
        )
        assert "Yes" in all_text or "yes" in all_text.lower()

    def test_valid_citizen_plays_consent_text_sw(self, enrolled_citizen):
        """Swahili consent prompt should use <Play> (gTTS), not <Say>."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent?lang=sw&citizen_id={citizen_id}",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        # Swahili uses <Play> elements for gTTS audio
        play_elements = root.findall("Play")
        assert len(play_elements) >= 1, (
            "Swahili consent prompt should use <Play> for gTTS audio"
        )
        # Verify Play URL comes from the mocked TTS service
        play_url = play_elements[0].text
        assert play_url is not None and play_url.startswith("http")

        # Should NOT have <Say> for the consent text (Swahili uses Play)
        say_elements = root.findall("Say")
        consent_says = [
            s for s in say_elements
            if s.text and "consent" in s.text.lower()
        ]
        assert len(consent_says) == 0, (
            "Swahili consent should use <Play>, not <Say>"
        )

    def test_invalid_citizen_hangup(self, ivr_client):
        """Bogus citizen_id should produce 'Session not found' and <Hangup>."""
        client, mock_redis, mock_update_call = ivr_client
        bogus_id = str(uuid.uuid4())

        resp = client.post(
            f"/twilio/voice/consent?lang=en&citizen_id={bogus_id}",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "session not found" in all_text.lower() or "goodbye" in all_text.lower(), (
            f"Expected 'Session not found' message, got: {all_text}"
        )

        hangup = root.find("Hangup")
        assert hangup is not None, "Expected <Hangup> for invalid citizen"

    def test_unclear_attempt_shows_clarification(self, enrolled_citizen):
        """When unclear_attempt > 0, a clarification message should appear."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}&unclear_attempt=1",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "didn't catch" in all_text.lower() or "yes or no" in all_text.lower(), (
            f"Expected clarification message, got: {all_text}"
        )

    def test_record_action_points_to_callback(self, enrolled_citizen):
        """The <Record> action should point to /twilio/voice/consent/callback."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        record = root.find("Record")
        assert record is not None, "Expected a <Record> element"

        action = record.attrib.get("action", "")
        assert "/twilio/voice/consent/callback" in action
        assert f"citizen_id={citizen_id}" in action
        assert "lang=en" in action


# ============================================================================
# POST /twilio/voice/consent/callback
# ============================================================================


class TestConsentCallback:
    """Tests for the consent callback (recording handoff)."""

    def test_no_recording_redirects_back(self, enrolled_citizen):
        """When no RecordingUrl is provided, redirect back to consent prompt."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={"CallSid": "CA-test-001"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None, "Expected <Redirect> when no recording"
        assert "/twilio/voice/consent" in redirect.text
        assert f"citizen_id={citizen_id}" in redirect.text

    def test_with_recording_plays_hold_audio(self, enrolled_citizen):
        """When RecordingUrl is provided, response should contain hold audio."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE001",
                "CallSid": "CA-test-001",
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        # Should contain hold/processing messages
        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "hold" in all_text.lower() or "processing" in all_text.lower() or "wait" in all_text.lower(), (
            f"Expected hold/processing audio, got: {all_text}"
        )

        # Should NOT contain a <Redirect> (pipeline runs in background)
        redirect = root.find("Redirect")
        assert redirect is None, "Hold response should not redirect (background task handles it)"


# ============================================================================
# Background: _run_consent_pipeline
# ============================================================================


class TestConsentPipeline:
    """Tests for the consent background pipeline (runs inline via conftest patch)."""

    def test_yes_signs_and_persists_token(self, enrolled_citizen):
        """classify_yes_no returns 'yes' (default) -- CONSENT_TOKEN should appear in DB."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # Trigger the pipeline (classify_yes_no defaults to "yes" via conftest)
        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE001",
                "CallSid": "CA-test-001",
            },
        )
        assert resp.status_code == 200

        # Check DB for the consent token
        db = TestSessionLocal()
        try:
            tokens = (
                db.query(ConsentToken)
                .filter(ConsentToken.citizen_id == citizen_id)
                .all()
            )
            assert len(tokens) >= 1, "Expected at least one CONSENT_TOKEN in DB after 'yes'"
            token = tokens[0]
            assert token.digital_signature is not None
            assert len(token.digital_signature) > 0
        finally:
            db.close()

    def test_yes_redirects_to_service_menu(self, enrolled_citizen):
        """After 'yes', the background pipeline should redirect to /voice/service/menu."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE001",
                "CallSid": "CA-test-001",
            },
        )
        assert resp.status_code == 200

        # The background pipeline should have called _update_call_twiml
        assert mock_update_call.call_count >= 1, "Expected _update_call_twiml to be called"

        # Get the TwiML sent to the live call
        last_twiml = mock_update_call.call_args_list[-1][0][1]
        root = parse_twiml(last_twiml)

        redirect = root.find("Redirect")
        assert redirect is not None, "Expected <Redirect> to service menu in pipeline TwiML"
        assert "/voice/service/menu" in redirect.text

    def test_no_declines_and_hangup(self, enrolled_citizen):
        """When classify_yes_no returns 'no', pipeline should decline and hangup."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="no"):
            resp = client.post(
                f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE002",
                    "CallSid": "CA-test-002",
                },
            )
        assert resp.status_code == 200

        assert mock_update_call.call_count >= 1
        last_twiml = mock_update_call.call_args_list[-1][0][1]
        root = parse_twiml(last_twiml)

        # Should contain "declined" or "goodbye"
        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "declined" in all_text.lower() or "goodbye" in all_text.lower(), (
            f"Expected decline message, got: {all_text}"
        )

        hangup = root.find("Hangup")
        assert hangup is not None, "Expected <Hangup> on consent decline"

        # No consent token should have been created
        db = TestSessionLocal()
        try:
            tokens = (
                db.query(ConsentToken)
                .filter(ConsentToken.citizen_id == citizen_id)
                .all()
            )
            assert len(tokens) == 0, "No CONSENT_TOKEN should exist after decline"
        finally:
            db.close()

    def test_unclear_first_attempt_retries(self, enrolled_citizen):
        """When classify_yes_no returns 'unclear' on attempt 0, redirect with unclear_attempt=1."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="unclear"):
            resp = client.post(
                f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE003",
                    "CallSid": "CA-test-003",
                },
            )
        assert resp.status_code == 200

        assert mock_update_call.call_count >= 1
        last_twiml = mock_update_call.call_args_list[-1][0][1]
        root = parse_twiml(last_twiml)

        redirect = root.find("Redirect")
        assert redirect is not None, "Expected <Redirect> for unclear retry"
        assert "/voice/consent" in redirect.text
        assert "unclear_attempt=1" in redirect.text

        # Should NOT hangup
        hangup = root.find("Hangup")
        assert hangup is None, "Should not hangup on first unclear attempt"

    def test_unclear_second_attempt_gives_up(self, enrolled_citizen):
        """When classify_yes_no returns 'unclear' on attempt >= 1, hangup."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="unclear"):
            resp = client.post(
                f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=1",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE004",
                    "CallSid": "CA-test-004",
                },
            )
        assert resp.status_code == 200

        assert mock_update_call.call_count >= 1
        last_twiml = mock_update_call.call_args_list[-1][0][1]
        root = parse_twiml(last_twiml)

        # Should contain a "could not understand" or "goodbye" message
        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        assert "could not understand" in all_text.lower() or "goodbye" in all_text.lower(), (
            f"Expected give-up message, got: {all_text}"
        )

        hangup = root.find("Hangup")
        assert hangup is not None, "Expected <Hangup> after second unclear attempt"

    def test_token_has_correct_ministry_and_scope(self, enrolled_citizen):
        """Consent token in DB should use _CONSENT_MINISTRY_CODE and _CONSENT_DATA_SCOPE."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # classify_yes_no defaults to "yes" via conftest
        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE005",
                "CallSid": "CA-test-005",
            },
        )
        assert resp.status_code == 200

        db = TestSessionLocal()
        try:
            tokens = (
                db.query(ConsentToken)
                .filter(ConsentToken.citizen_id == citizen_id)
                .all()
            )
            assert len(tokens) >= 1
            token = tokens[0]
            assert token.ministry_code == "GOV", (
                f"Expected ministry_code='GOV', got '{token.ministry_code}'"
            )
            assert token.data_scope == "service_access", (
                f"Expected data_scope='service_access', got '{token.data_scope}'"
            )
        finally:
            db.close()
