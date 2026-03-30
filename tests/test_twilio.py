"""Phase 15 validation: Twilio IVR webhook endpoints return valid TwiML XML."""

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _parse_twiml(content: str) -> ET.Element:
    """Parse TwiML XML and return the root <Response> element."""
    root = ET.fromstring(content)
    assert root.tag == "Response"
    return root


class TestWelcome:
    def test_returns_valid_twiml(self):
        resp = client.post("/twilio/voice/welcome")
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]
        root = _parse_twiml(resp.text)
        # Should contain a <Gather> element
        gather = root.find("Gather")
        assert gather is not None

    def test_gather_has_action(self):
        resp = client.post("/twilio/voice/welcome")
        root = _parse_twiml(resp.text)
        gather = root.find("Gather")
        assert "action" in gather.attrib
        assert "/twilio/voice/welcome/language" in gather.attrib["action"]


class TestLanguageSelection:
    def test_english_selected(self):
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "1"})
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None
        assert "lang=en" in gather.attrib["action"]

    def test_swahili_selected(self):
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "2"})
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        gather = root.find("Gather")
        assert "lang=sw" in gather.attrib["action"]


class TestEnrollment:
    def test_first_step_prompts_national_id(self):
        resp = client.post("/twilio/voice/enroll?lang=en&step=0")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None
        # Should ask for national ID
        say = gather.find("Say")
        assert say is not None
        assert "national id" in say.text.lower() or "kitambulisho" in say.text.lower()

    def test_recording_step_has_record_verb(self):
        """Step 1 with a national_id should prompt and record."""
        resp = client.post("/twilio/voice/enroll?lang=en&step=1&national_id=KE-123")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        # Should contain <Record>
        record = root.find("Record")
        assert record is not None
        assert record.attrib.get("maxLength") == "10"
        assert "trim-silence" in record.attrib.get("trim", "")

    def test_record_action_points_to_callback(self):
        resp = client.post("/twilio/voice/enroll?lang=en&step=2&national_id=KE-123")
        root = _parse_twiml(resp.text)
        record = root.find("Record")
        assert "/twilio/voice/enroll/callback" in record.attrib["action"]
        assert "step=2" in record.attrib["action"]

    def test_enrollment_complete_at_step_5(self):
        resp = client.post(f"/twilio/voice/enroll?lang=en&step=5&national_id=KE-123")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        say = root.find("Say")
        assert say is not None
        assert "complete" in say.text.lower()
        hangup = root.find("Hangup")
        assert hangup is not None

    def test_callback_advances_step(self):
        resp = client.post(
            "/twilio/voice/enroll/callback?lang=en&step=1&national_id=KE-123",
            data={"RecordingUrl": "https://api.twilio.com/recordings/RE123"},
        )
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "step=2" in redirect.text

    def test_swahili_enrollment_prompt(self):
        resp = client.post("/twilio/voice/enroll?lang=sw&step=0&national_id=SW-456")
        root = _parse_twiml(resp.text)
        # step=0 with national_id present should show recording prompt (step treated as 0)
        # Actually step=0 with national_id should go to first recording
        say = root.find("Say")
        assert say is not None
        assert "Tafadhali" in say.text


class TestAuthenticate:
    def test_challenge_has_record(self):
        resp = client.post("/twilio/voice/authenticate?lang=en")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        say = root.find("Say")
        assert say is not None
        assert "please say" in say.text.lower() or "phrase" in say.text.lower()
        record = root.find("Record")
        assert record is not None
        assert record.attrib.get("maxLength") == "10"

    def test_callback_returns_twiml(self):
        resp = client.post(
            "/twilio/voice/authenticate/callback?lang=en&challenge_id=test-123",
            data={"RecordingUrl": "https://api.twilio.com/recordings/RE456"},
        )
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        assert root.find("Hangup") is not None


class TestConsent:
    def test_consent_has_record(self):
        resp = client.post("/twilio/voice/consent?lang=en")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        say = root.find("Say")
        assert "consent" in say.text.lower()
        record = root.find("Record")
        assert record is not None
        assert record.attrib.get("maxLength") == "10"


class TestService:
    def test_first_question(self):
        resp = client.post("/twilio/voice/service?lang=en&question_index=0")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        say = root.find("Say")
        assert "name" in say.text.lower()
        record = root.find("Record")
        assert record is not None

    def test_complete_after_all_questions(self):
        resp = client.post("/twilio/voice/service?lang=en&question_index=3")
        assert resp.status_code == 200
        root = _parse_twiml(resp.text)
        hangup = root.find("Hangup")
        assert hangup is not None
