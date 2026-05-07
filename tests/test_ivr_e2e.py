"""End-to-end IVR flow integration tests.

Exercises the full Twilio webhook chain through mocked services:
  Welcome -> Language -> Enroll -> Auth -> Consent -> Service -> Done

Uses the ``ivr_client`` fixture from conftest.py which provides a
``(TestClient, MockRedis, mock_update_call)`` tuple with all heavy ML
services, Redis, Twilio validation, and background tasks patched to
run inline.
"""

import json
import urllib.parse
import xml.etree.ElementTree as ET

import pytest

from app.db.crud import get_citizen_by_national_id
from app.models.auth_event import AuthEvent
from app.models.citizen import Citizen
from app.models.consent_token import ConsentToken
from app.models.service_form import ServiceForm
from app.models.voice_template import VoiceTemplate
from conftest import TestSessionLocal, parse_twiml


# ── Helpers ─────────────────────────────────────────────────────────────────


def _extract_query_param(url: str, key: str) -> str:
    """Extract a single query-parameter value from a URL string."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    values = params.get(key, [])
    return values[0] if values else ""


def _find_element(root: ET.Element, tag: str) -> ET.Element | None:
    """Recursively find the first element with the given tag name."""
    el = root.find(f".//{tag}")
    return el


def _enroll_citizen(client, mock_redis, mock_update_call, nid, call_sid, lang="en"):
    """Run the full enrollment sub-flow and return (citizen_id, session_id).

    Steps:
      1. Enroll step=0 — NID gather
      2. Enter NID -> step=1 (first Record)
      3. Callbacks for steps 1-5
      4. Pipeline runs inline -> citizen + template created
    """
    # Step 0: gather NID
    resp = client.post(f"/twilio/voice/enroll?lang={lang}&step=0")
    assert resp.status_code == 200
    root = parse_twiml(resp.text)
    gather = _find_element(root, "Gather")
    assert gather is not None

    # Enter NID -> step 1 (first phrase + Record)
    resp = client.post(f"/twilio/voice/enroll?lang={lang}&step=1", data={"Digits": nid})
    assert resp.status_code == 200
    root = parse_twiml(resp.text)
    record = _find_element(root, "Record")
    assert record is not None, "Expected <Record> for first enrollment phrase"
    action = record.attrib["action"]
    session_id = _extract_query_param(action, "session_id")
    assert session_id, "session_id must be present in Record action URL"

    # Callbacks for steps 1..5
    for step in range(1, 6):
        resp = client.post(
            f"/twilio/voice/enroll/callback?lang={lang}&step={step}"
            f"&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": f"https://api.twilio.com/recordings/RE-ENROLL-{step}",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        if step < 5:
            redirect = _find_element(root, "Redirect")
            assert redirect is not None
            assert f"step={step + 1}" in redirect.text

    # After step 5, pipeline ran inline -> _update_call_twiml called
    assert mock_update_call.called, "Enrollment pipeline should have called _update_call_twiml"

    # Verify citizen was created
    db = TestSessionLocal()
    citizen = get_citizen_by_national_id(db, nid)
    assert citizen is not None, f"Citizen with NID {nid} should exist after enrollment"
    citizen_id = str(citizen.citizen_id)
    db.close()

    return citizen_id, session_id


def _authenticate_citizen(client, mock_redis, mock_update_call, nid, call_sid, lang="en", attempt=0):
    """Run the auth sub-flow (NID entry + callback) and return the TwiML sent via _update_call_twiml."""
    # Enter NID -> get challenge + Record
    resp = client.post(
        f"/twilio/voice/authenticate?lang={lang}&attempt={attempt}",
        data={"Digits": nid},
    )
    assert resp.status_code == 200
    root = parse_twiml(resp.text)
    record = _find_element(root, "Record")
    assert record is not None, "Expected <Record> for auth challenge"
    action = record.attrib["action"]
    challenge_id = _extract_query_param(action, "challenge_id")
    assert challenge_id

    # Auth callback
    resp = client.post(
        f"/twilio/voice/authenticate/callback?lang={lang}"
        f"&challenge_id={challenge_id}&national_id={nid}&attempt={attempt}",
        data={
            "RecordingUrl": "https://api.twilio.com/recordings/RE-AUTH",
            "CallSid": call_sid,
        },
    )
    assert resp.status_code == 200
    assert mock_update_call.called, "Auth pipeline should have called _update_call_twiml"
    twiml_sent = mock_update_call.call_args_list[-1][0][1]
    return twiml_sent


# ═══════════════════════════════════════════════════════════════════════════
# TestFullIVRFlowEN
# ═══════════════════════════════════════════════════════════════════════════


class TestFullIVRFlowEN:
    """Complete English IVR flow: Welcome -> Enroll -> Auth -> Consent -> Service -> Done."""

    def test_welcome_to_service_complete_en(self, ivr_client):
        """Simulate a complete IVR call in English from first ring to service submission."""
        client, mock_redis, mock_update_call = ivr_client
        CALL_SID = "CA-E2E-EN-001"
        NID = "KE-E2E-001"

        # ── 1. Welcome ──────────────────────────────────────────────────────
        resp = client.post("/twilio/voice/welcome")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert "welcome/language" in gather.attrib["action"]

        # ── 2. Select English (digit 1) ─────────────────────────────────────
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "1"})
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert "lang=en" in gather.attrib["action"]

        # ── 3. Select Enroll (digit 1) ──────────────────────────────────────
        resp = client.post("/twilio/voice/welcome/action?lang=en", data={"Digits": "1"})
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "enroll" in redirect.text

        # ── 4-10. Full enrollment (NID + 5 recordings) ─────────────────────
        citizen_id, _ = _enroll_citizen(client, mock_redis, mock_update_call, NID, CALL_SID)
        mock_update_call.reset_mock()

        # ── 11-12. Authenticate ─────────────────────────────────────────────
        twiml_sent = _authenticate_citizen(client, mock_redis, mock_update_call, NID, CALL_SID)

        # Auth should grant (mock matching returns granted=True) and redirect to consent
        assert "consent" in twiml_sent.lower(), "Granted auth should redirect to consent"
        assert citizen_id in twiml_sent, "Consent redirect must include citizen_id"
        mock_update_call.reset_mock()

        # ── 13. Consent prompt ──────────────────────────────────────────────
        resp = client.post(f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = _find_element(root, "Record")
        assert record is not None, "Consent prompt should have a <Record>"

        # ── 14. Consent callback (classify_yes_no returns "yes") ────────────
        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CONSENT",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "service/menu" in twiml_sent, "Consent 'yes' should redirect to service menu"

        # Get consent_token_id from DB
        db = TestSessionLocal()
        token = db.query(ConsentToken).filter(ConsentToken.citizen_id == citizen_id).first()
        assert token is not None, "ConsentToken should have been created"
        consent_token_id = str(token.token_id)
        db.close()

        mock_update_call.reset_mock()

        # ── 15. Service menu — select pension (digit 1) ─────────────────────
        resp = client.post(
            f"/twilio/voice/service/menu?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}",
            data={"Digits": "1", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "pension" in redirect.text

        # ── 16-18. Answer 3 questions (service callbacks for Q0, Q1, Q2) ────
        for qi in range(3):
            # First, hit the service prompt to get the question + Record
            resp = client.post(
                f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=pension"
                f"&question_index={qi}&correction_attempts=0"
                f"&correcting_index=-1&call_sid={CALL_SID}",
            )
            assert resp.status_code == 200
            root = parse_twiml(resp.text)
            record = _find_element(root, "Record")
            assert record is not None, f"Service Q{qi} should have a <Record>"

            # Submit the recording callback
            resp = client.post(
                f"/twilio/voice/service/callback?lang=en&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=pension"
                f"&question_index={qi}&correction_attempts=0&correcting_index=-1",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-Q{qi}",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200

        # Verify answers stored in Redis
        raw = mock_redis.get(f"ivr:service_answers:{CALL_SID}")
        assert raw is not None, "Service answers should be stored in Redis"
        answers = json.loads(raw)
        assert "q0" in answers and "q1" in answers and "q2" in answers

        mock_update_call.reset_mock()

        # ── 19. Readback prompt (question_index=3 triggers readback) ────────
        resp = client.post(
            f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&question_index=3&correction_attempts=0&unclear_attempt=0"
            f"&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = _find_element(root, "Record")
        assert record is not None, "Readback should have a <Record> for yes/no"

        # ── 20. Service confirm — "yes" (classify_yes_no returns "yes") ─────
        resp = client.post(
            f"/twilio/voice/service/confirm?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&correction_attempts=0&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CONFIRM",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # Confirm TwiML should mention reference ID
        assert "reference" in twiml_sent.lower() or "Reference" in twiml_sent

        # ── 21. Verify all DB records exist ─────────────────────────────────
        db = TestSessionLocal()
        assert db.query(Citizen).count() == 1
        assert db.query(VoiceTemplate).count() == 1
        assert db.query(AuthEvent).count() == 1
        assert db.query(ConsentToken).count() == 1
        assert db.query(ServiceForm).count() == 1

        form = db.query(ServiceForm).first()
        assert form.service_code == "pension"
        assert form.ministry_code == "MLSP"
        assert form.consent_token_id == consent_token_id
        assert form.citizen_id == citizen_id

        # Verify answers_json is valid JSON with the expected keys
        saved_answers = json.loads(form.answers_json)
        assert "payment_count" in saved_answers
        assert "withdrawal_type" in saved_answers
        assert "delivery_method" in saved_answers
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestFullIVRFlowSW
# ═══════════════════════════════════════════════════════════════════════════


class TestFullIVRFlowSW:
    """Complete Swahili IVR flow — verifies <Play> tags instead of <Say>."""

    def test_welcome_to_service_complete_sw(self, ivr_client):
        """Simulate a complete IVR call in Swahili with <Play> TTS verification."""
        client, mock_redis, mock_update_call = ivr_client
        CALL_SID = "CA-E2E-SW-001"
        NID = "KE-E2E-SW-001"

        # ── 1. Welcome ──────────────────────────────────────────────────────
        resp = client.post("/twilio/voice/welcome")
        assert resp.status_code == 200

        # ── 2. Select Swahili (digit 2) ─────────────────────────────────────
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "2"})
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert "lang=sw" in gather.attrib["action"]
        # Swahili prompts use <Play> not <Say>
        play = _find_element(gather, "Play")
        assert play is not None, "Swahili prompt should use <Play> (gTTS)"

        # ── 3. Select Enroll (digit 1) ──────────────────────────────────────
        resp = client.post("/twilio/voice/welcome/action?lang=sw", data={"Digits": "1"})
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "enroll" in redirect.text
        assert "lang=sw" in redirect.text

        # ── 4-10. Full enrollment in Swahili ────────────────────────────────
        citizen_id, _ = _enroll_citizen(
            client, mock_redis, mock_update_call, NID, CALL_SID, lang="sw"
        )
        mock_update_call.reset_mock()

        # ── 11-12. Authenticate in Swahili ──────────────────────────────────
        twiml_sent = _authenticate_citizen(
            client, mock_redis, mock_update_call, NID, CALL_SID, lang="sw"
        )
        assert "consent" in twiml_sent.lower()
        mock_update_call.reset_mock()

        # ── 13. Consent prompt — Swahili uses <Play> ────────────────────────
        resp = client.post(f"/twilio/voice/consent?lang=sw&citizen_id={citizen_id}")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        # Swahili consent should contain <Play> elements for prompts
        plays = root.findall(".//Play")
        assert len(plays) >= 1, "Swahili consent should use <Play> for TTS"
        record = _find_element(root, "Record")
        assert record is not None

        # ── 14. Consent callback ────────────────────────────────────────────
        resp = client.post(
            f"/twilio/voice/consent/callback?lang=sw&citizen_id={citizen_id}&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CONSENT-SW",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "service/menu" in twiml_sent

        # Get consent token
        db = TestSessionLocal()
        token = db.query(ConsentToken).filter(ConsentToken.citizen_id == citizen_id).first()
        consent_token_id = str(token.token_id)
        db.close()

        mock_update_call.reset_mock()

        # ── 15. Service menu — select telemedicine (digit 5) ────────────────
        resp = client.post(
            f"/twilio/voice/service/menu?lang=sw&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}",
            data={"Digits": "5", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "telemedicine" in redirect.text

        # ── 16-18. Answer 3 questions ───────────────────────────────────────
        for qi in range(3):
            resp = client.post(
                f"/twilio/voice/service?lang=sw&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=telemedicine"
                f"&question_index={qi}&correction_attempts=0"
                f"&correcting_index=-1&call_sid={CALL_SID}",
            )
            assert resp.status_code == 200
            root = parse_twiml(resp.text)
            # Swahili questions should use <Play>
            plays = root.findall(".//Play")
            assert len(plays) >= 1, f"Swahili Q{qi} should use <Play>"

            resp = client.post(
                f"/twilio/voice/service/callback?lang=sw&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=telemedicine"
                f"&question_index={qi}&correction_attempts=0&correcting_index=-1",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-SW-Q{qi}",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200

        mock_update_call.reset_mock()

        # ── 19-20. Readback + confirm ───────────────────────────────────────
        resp = client.post(
            f"/twilio/voice/service?lang=sw&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=telemedicine"
            f"&question_index=3&correction_attempts=0&unclear_attempt=0"
            f"&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/twilio/voice/service/confirm?lang=sw&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=telemedicine"
            f"&correction_attempts=0&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-SW-CONFIRM",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called

        # Verify DB
        db = TestSessionLocal()
        assert db.query(ServiceForm).count() == 1
        form = db.query(ServiceForm).first()
        assert form.service_code == "telemedicine"
        assert form.ministry_code == "MOH"
        citizen = db.query(Citizen).first()
        assert citizen.preferred_language == "sw"
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestVerifyThenEnrollFlow
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifyThenEnrollFlow:
    """Verify identity via eSignet OTP, then enroll -- citizen gets mosip_individual_id."""

    def test_verify_then_enroll_links_mosip(self, ivr_client):
        """Full verify -> enroll flow: citizen ends up with identity_verified=True."""
        client, mock_redis, mock_update_call = ivr_client
        CALL_SID = "CA-VERIFY-001"
        NID = "KE-VERIFY-001"

        # ── 1. Verify start — prompt for NID ────────────────────────────────
        resp = client.post(
            "/twilio/voice/verify/start?lang=en",
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert "verify/nid" in gather.attrib["action"]

        # ── 2. Enter NID — triggers eSignet OTP ────────────────────────────
        resp = client.post(
            "/twilio/voice/verify/nid?lang=en",
            data={"Digits": NID, "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert "verify/otp" in gather.attrib["action"]

        # Verify session stored in Redis
        session_raw = mock_redis.get(f"ivr:verify_session:{CALL_SID}")
        assert session_raw is not None, "Verify session should be stored in Redis"
        session_data = json.loads(session_raw)
        assert session_data["national_id"] == NID

        # ── 3. Enter OTP — verifies with eSignet, redirects to enroll ──────
        resp = client.post(
            "/twilio/voice/verify/otp?lang=en",
            data={"Digits": "123456", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "enroll" in redirect.text
        assert f"national_id={NID}" in redirect.text

        # Verified identity stored in Redis
        verified_raw = mock_redis.get(f"ivr:verified_identity:{CALL_SID}")
        assert verified_raw is not None
        verified_data = json.loads(verified_raw)
        assert verified_data["national_id"] == NID
        assert verified_data["mosip_individual_id"] == "MOSIP-IND-001"

        # Verify session was cleaned up
        assert mock_redis.get(f"ivr:verify_session:{CALL_SID}") is None

        # ── 4. Complete enrollment (5 recordings) ───────────────────────────
        # The redirect from verify/otp points to enroll with national_id pre-filled
        # so step=0 with national_id will skip NID gather and jump to step=1

        # Hit enroll with national_id pre-filled (step=0 auto-redirects to step=1)
        resp = client.post(
            f"/twilio/voice/enroll?lang=en&step=0&national_id={NID}",
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "step=1" in redirect.text

        # Follow the redirect to step=1
        resp = client.post(
            f"/twilio/voice/enroll?lang=en&step=1&national_id={NID}",
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = _find_element(root, "Record")
        assert record is not None
        action = record.attrib["action"]
        session_id = _extract_query_param(action, "session_id")

        # Enroll callbacks for steps 1-5
        for step in range(1, 6):
            resp = client.post(
                f"/twilio/voice/enroll/callback?lang=en&step={step}"
                f"&national_id={NID}&session_id={session_id}",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-V-{step}",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200

        assert mock_update_call.called

        # ── 5. Check DB: citizen has identity_verified + mosip_individual_id ─
        db = TestSessionLocal()
        citizen = get_citizen_by_national_id(db, NID)
        assert citizen is not None
        assert citizen.identity_verified is True
        assert citizen.mosip_individual_id == "MOSIP-IND-001"

        template = db.query(VoiceTemplate).filter(
            VoiceTemplate.citizen_id == citizen.citizen_id
        ).first()
        assert template is not None
        assert template.is_active is True
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestAuthDeniedRetryFlow
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthDeniedRetryFlow:
    """Auth denied twice -> hangup, 2 AuthEvents with result='denied' in DB."""

    def test_auth_denied_retry_denied_hangup(self, ivr_client):
        """Two failed auth attempts result in hangup and 2 denied AuthEvents."""
        client, mock_redis, mock_update_call = ivr_client
        CALL_SID = "CA-DENY-001"
        NID = "KE-DENY-001"

        # First, enroll a citizen so auth has someone to match against
        citizen_id, _ = _enroll_citizen(client, mock_redis, mock_update_call, NID, CALL_SID)
        mock_update_call.reset_mock()

        # Patch matching to return denied
        from unittest.mock import patch
        with patch(
            "twilio_integration.webhook_handler._matching_service"
        ) as mock_matching:
            mock_matching.match.return_value = {"score": 0.30, "granted": False}

            # ── Attempt 0: denied, should retry ─────────────────────────────
            twiml_sent = _authenticate_citizen(
                client, mock_redis, mock_update_call, NID, CALL_SID,
                attempt=0,
            )
            # Should contain retry redirect (attempt=1)
            assert "authenticate" in twiml_sent.lower()
            assert "attempt=1" in twiml_sent
            assert "denied" in twiml_sent.lower() or "Denied" in twiml_sent or "0.30" in twiml_sent

            mock_update_call.reset_mock()

            # ── Attempt 1: denied again, should hangup ──────────────────────
            twiml_sent = _authenticate_citizen(
                client, mock_redis, mock_update_call, NID, CALL_SID,
                attempt=1,
            )
            # Should hangup
            root = parse_twiml(twiml_sent)
            hangup = _find_element(root, "Hangup")
            assert hangup is not None, "Second denial should result in <Hangup>"

        # ── Verify DB: 2 AuthEvents with result='denied' ───────────────────
        db = TestSessionLocal()
        events = db.query(AuthEvent).filter(AuthEvent.citizen_id == citizen_id).all()
        assert len(events) == 2, f"Expected 2 AuthEvents, got {len(events)}"
        assert all(e.result == "denied" for e in events)
        assert all(e.voice_match_score == pytest.approx(0.30) for e in events)
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestConsentDeclinedFlow
# ═══════════════════════════════════════════════════════════════════════════


class TestConsentDeclinedFlow:
    """Consent 'no' results in hangup, no ConsentToken in DB."""

    def test_consent_declined_hangup(self, enrolled_citizen):
        """Declining consent should produce <Hangup> and no token persisted."""
        client, mock_redis, mock_update_call, citizen_id, nid = enrolled_citizen
        CALL_SID = "CA-CONSENT-NO-001"

        # Consent prompt
        resp = client.post(f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}")
        assert resp.status_code == 200

        # Override classify_yes_no to return "no" for this callback
        from unittest.mock import patch
        with patch(
            "twilio_integration.webhook_handler.classify_yes_no",
            return_value="no",
        ):
            resp = client.post(
                f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE-CONSENT-NO",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200
            assert mock_update_call.called
            twiml_sent = mock_update_call.call_args_list[-1][0][1]
            root = parse_twiml(twiml_sent)
            hangup = _find_element(root, "Hangup")
            assert hangup is not None, "Declining consent should <Hangup>"
            assert "declined" in twiml_sent.lower() or "Declined" in twiml_sent

        # No consent token should exist
        db = TestSessionLocal()
        assert db.query(ConsentToken).count() == 0
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestConsentUnclearRetryFlow
# ═══════════════════════════════════════════════════════════════════════════


class TestConsentUnclearRetryFlow:
    """Consent unclear once -> retry, then 'yes' -> token persisted."""

    def test_consent_unclear_then_yes(self, enrolled_citizen):
        """Unclear first attempt retries; second attempt 'yes' persists token."""
        client, mock_redis, mock_update_call, citizen_id, nid = enrolled_citizen
        CALL_SID = "CA-CONSENT-UNCLEAR-001"

        # First attempt: unclear
        from unittest.mock import patch
        with patch(
            "twilio_integration.webhook_handler.classify_yes_no",
            return_value="unclear",
        ):
            resp = client.post(
                f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=0",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE-UNCLEAR",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200
            assert mock_update_call.called
            twiml_sent = mock_update_call.call_args_list[-1][0][1]
            # Should redirect to consent with unclear_attempt=1
            assert "consent" in twiml_sent.lower()
            assert "unclear_attempt=1" in twiml_sent

        mock_update_call.reset_mock()

        # Second attempt with unclear_attempt=1 prompt shows clarification
        resp = client.post(
            f"/twilio/voice/consent?lang=en&citizen_id={citizen_id}&unclear_attempt=1"
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        # Should contain clarification text
        says = root.findall(".//Say")
        say_texts = [s.text for s in says if s.text]
        combined = " ".join(say_texts)
        assert "didn't catch" in combined.lower() or "yes or no" in combined.lower()

        # Now classify_yes_no returns "yes" (default in conftest)
        resp = client.post(
            f"/twilio/voice/consent/callback?lang=en&citizen_id={citizen_id}&unclear_attempt=1",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-YES",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "service/menu" in twiml_sent

        db = TestSessionLocal()
        assert db.query(ConsentToken).count() == 1
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestServiceCorrectionFlow
# ═══════════════════════════════════════════════════════════════════════════


class TestServiceCorrectionFlow:
    """Answer 3 Qs, readback 'no', correct Q1, readback 'yes' -> form persisted."""

    def test_correction_flow(self, consented_citizen):
        """Full correction cycle: answer -> reject readback -> correct Q1 -> confirm."""
        client, mock_redis, mock_update_call, citizen_id, nid, consent_token_id = consented_citizen
        CALL_SID = "CA-CORRECT-001"

        # ── 1. Answer 3 questions for pension service ───────────────────────
        for qi in range(3):
            resp = client.post(
                f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=pension"
                f"&question_index={qi}&correction_attempts=0"
                f"&correcting_index=-1&call_sid={CALL_SID}",
            )
            assert resp.status_code == 200

            resp = client.post(
                f"/twilio/voice/service/callback?lang=en&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=pension"
                f"&question_index={qi}&correction_attempts=0&correcting_index=-1",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-C-Q{qi}",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200

        # Verify all 3 answers in Redis
        raw = mock_redis.get(f"ivr:service_answers:{CALL_SID}")
        answers = json.loads(raw)
        assert len(answers) == 3

        mock_update_call.reset_mock()

        # ── 2. Readback — say "no" ──────────────────────────────────────────
        # Get the readback prompt
        resp = client.post(
            f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&question_index=3&correction_attempts=0&unclear_attempt=0"
            f"&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200

        # Submit "no" to the confirm endpoint
        from unittest.mock import patch
        with patch(
            "twilio_integration.webhook_handler.classify_yes_no",
            return_value="no",
        ):
            resp = client.post(
                f"/twilio/voice/service/confirm?lang=en&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}&service_code=pension"
                f"&correction_attempts=0&unclear_attempt=0",
                data={
                    "RecordingUrl": "https://api.twilio.com/recordings/RE-C-NO",
                    "CallSid": CALL_SID,
                },
            )
            assert resp.status_code == 200
            assert mock_update_call.called
            twiml_sent = mock_update_call.call_args_list[-1][0][1]
            # Should redirect to /voice/service/correct with correction_attempts=1
            assert "service/correct" in twiml_sent
            assert "correction_attempts=1" in twiml_sent

        mock_update_call.reset_mock()

        # ── 3. Correction — "which question?" -> Q1 (index 0) ──────────────
        # parse_question_number is patched to return 0 by default in conftest
        resp = client.post(
            f"/twilio/voice/service/correct?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&correction_attempts=1&unclear_attempt=0&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = _find_element(root, "Record")
        assert record is not None, "Correct prompt should have <Record>"

        # Submit "one" recording
        resp = client.post(
            f"/twilio/voice/service/correct/callback?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&correction_attempts=1&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-WHICH",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # Should redirect to re-ask question 0 with correcting_index=0
        assert "question_index=0" in twiml_sent
        assert "correcting_index=0" in twiml_sent

        mock_update_call.reset_mock()

        # ── 4. Re-answer Q0 ────────────────────────────────────────────────
        resp = client.post(
            f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&question_index=0&correction_attempts=1"
            f"&correcting_index=0&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/twilio/voice/service/callback?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&question_index=0&correction_attempts=1&correcting_index=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-C-Q0-FIXED",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # After correcting, should jump to readback (question_index=3)
        assert "question_index=3" in twiml_sent
        # correcting_index should be reset to -1
        assert "correcting_index=-1" in twiml_sent

        mock_update_call.reset_mock()

        # ── 5. Second readback — say "yes" (default classify_yes_no) ────────
        resp = client.post(
            f"/twilio/voice/service?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&question_index=3&correction_attempts=1&unclear_attempt=0"
            f"&call_sid={CALL_SID}",
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/twilio/voice/service/confirm?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}&service_code=pension"
            f"&correction_attempts=1&unclear_attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-C-YES",
                "CallSid": CALL_SID,
            },
        )
        assert resp.status_code == 200
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "reference" in twiml_sent.lower() or "Reference" in twiml_sent

        # ── 6. Verify DB — SERVICE_FORM persisted ──────────────────────────
        db = TestSessionLocal()
        assert db.query(ServiceForm).count() == 1
        form = db.query(ServiceForm).first()
        assert form.service_code == "pension"
        assert form.ministry_code == "MLSP"
        assert form.citizen_id == citizen_id
        assert form.consent_token_id == consent_token_id
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestServiceMenuValidation
# ═══════════════════════════════════════════════════════════════════════════


class TestServiceMenuValidation:
    """Service menu edge cases: invalid consent, invalid digit, expired token."""

    def test_invalid_consent_token_rejected(self, enrolled_citizen):
        """Service menu with bogus consent_token_id should hangup."""
        client, mock_redis, mock_update_call, citizen_id, nid = enrolled_citizen
        resp = client.post(
            f"/twilio/voice/service/menu?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id=bogus-token-id",
            data={"Digits": "1", "CallSid": "CA-BOGUS"},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        hangup = _find_element(root, "Hangup")
        assert hangup is not None, "Invalid consent token should <Hangup>"

    def test_invalid_digit_re_prompts_menu(self, consented_citizen):
        """Pressing an invalid digit (9) should re-show the menu."""
        client, mock_redis, mock_update_call, citizen_id, nid, consent_token_id = consented_citizen
        resp = client.post(
            f"/twilio/voice/service/menu?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}",
            data={"Digits": "9", "CallSid": "CA-BAD-DIGIT"},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None, "Invalid digit should re-prompt with <Gather>"

    def test_no_digit_shows_menu(self, consented_citizen):
        """No digit pressed shows the service menu."""
        client, mock_redis, mock_update_call, citizen_id, nid, consent_token_id = consented_citizen
        resp = client.post(
            f"/twilio/voice/service/menu?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}",
            data={"CallSid": "CA-NO-DIGIT"},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None


# ═══════════════════════════════════════════════════════════════════════════
# TestAllFiveServices
# ═══════════════════════════════════════════════════════════════════════════


class TestAllFiveServices:
    """Quick smoke test: each of the 5 services can be selected and Q0 prompted."""

    @pytest.mark.parametrize("digit,expected_code", [
        ("1", "pension"),
        ("2", "mpesa_transfer"),
        ("3", "aid_verification"),
        ("4", "sim_swap"),
        ("5", "telemedicine"),
    ])
    def test_service_selection(self, consented_citizen, digit, expected_code):
        """Pressing digit N routes to the correct service_code."""
        client, mock_redis, mock_update_call, citizen_id, nid, consent_token_id = consented_citizen
        resp = client.post(
            f"/twilio/voice/service/menu?lang=en&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}",
            data={"Digits": digit, "CallSid": "CA-SVC-SEL"},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert expected_code in redirect.text


# ═══════════════════════════════════════════════════════════════════════════
# TestEnrollmentEdgeCases
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrollmentEdgeCases:
    """Enrollment guard rails: duplicate NID, insufficient recordings."""

    def test_duplicate_nid_rejected(self, ivr_client):
        """Enrolling the same NID twice is rejected by the pipeline."""
        client, mock_redis, mock_update_call = ivr_client
        CALL_SID = "CA-DUP-001"
        NID = "KE-DUP-001"

        # First enrollment succeeds
        _enroll_citizen(client, mock_redis, mock_update_call, NID, CALL_SID)
        mock_update_call.reset_mock()

        # Second enrollment with same NID should fail (pipeline detects existing)
        CALL_SID2 = "CA-DUP-002"
        _enroll_citizen(client, mock_redis, mock_update_call, NID, CALL_SID2)

        # The pipeline should call _update_call_twiml with an error message
        assert mock_update_call.called
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "already enrolled" in twiml_sent.lower()

        # Only 1 citizen should exist
        db = TestSessionLocal()
        assert db.query(Citizen).count() == 1
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TestAuthNotEnrolled
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthNotEnrolled:
    """Attempting to authenticate a non-enrolled NID redirects to enrollment."""

    def test_auth_unenrolled_redirects_to_enroll(self, ivr_client):
        """Auth with unknown NID should redirect caller to enrollment."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/authenticate?lang=en",
            data={"Digits": "UNKNOWN-NID"},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "enroll" in redirect.text
        assert "UNKNOWN-NID" in redirect.text
