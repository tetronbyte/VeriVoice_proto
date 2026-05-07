"""Tests for IVR identity verification (eSignet OTP) flow.

Covers the three verify endpoints:
  - POST /twilio/voice/verify/start   (prompt for national ID)
  - POST /twilio/voice/verify/nid     (send OTP via eSignet)
  - POST /twilio/voice/verify/otp     (verify OTP, redirect to enroll)
"""

import json
from unittest.mock import patch

import pytest

from conftest import parse_twiml

CALL_SID = "CALL-001"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _post(client, path, data=None, **params):
    """POST to a Twilio voice endpoint with query params and form data."""
    qs = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    url = f"/twilio/voice/{path}" + (f"?{qs}" if qs else "")
    return client.post(url, data=data or {})


def _find_elements(root, tag):
    """Recursively find all elements with the given tag."""
    found = []
    if root.tag == tag:
        found.append(root)
    for child in root:
        found.extend(_find_elements(child, tag))
    return found


def _find_element(root, tag):
    """Find the first element with the given tag (recursive)."""
    results = _find_elements(root, tag)
    return results[0] if results else None


def _all_text(root):
    """Collect all text content from element and descendants."""
    parts = []
    if root.text:
        parts.append(root.text)
    for child in root:
        parts.extend([_all_text(child)])
    if root.tail:
        parts.append(root.tail)
    return " ".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# TestVerifyStart
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyStart:
    """POST /twilio/voice/verify/start — prompt for national ID."""

    def test_prompts_for_nid_en(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/start", data={"CallSid": CALL_SID}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        # English prompt uses <Say>
        say = _find_element(gather, "Say")
        assert say is not None
        assert "national" in say.text.lower() or "identity" in say.text.lower()

    def test_prompts_for_nid_sw(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/start", data={"CallSid": CALL_SID}, lang="sw")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        # Swahili prompt uses <Play> (gTTS)
        play = _find_element(gather, "Play")
        assert play is not None

    def test_gather_finishes_on_pound(self, ivr_client):
        client, _, _ = ivr_client
        resp = _post(client, "verify/start", data={"CallSid": CALL_SID}, lang="en")
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert gather.get("finishOnKey") == "#"

    def test_gather_action_points_to_nid(self, ivr_client):
        client, _, _ = ivr_client
        resp = _post(client, "verify/start", data={"CallSid": CALL_SID}, lang="en")
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        action = gather.get("action")
        assert "/twilio/voice/verify/nid" in action
        assert "lang=en" in action

    def test_timeout_redirects_to_start(self, ivr_client):
        client, _, _ = ivr_client
        resp = _post(client, "verify/start", data={"CallSid": CALL_SID}, lang="en")
        root = parse_twiml(resp.text)
        # After the Gather, there should be a Redirect back to verify/start
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/verify/start" in redirect.text


# ═════════════════════════════════════════════════════════════════════════════
# TestVerifyNID
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyNID:
    """POST /twilio/voice/verify/nid — send OTP via eSignet."""

    def test_empty_digits_redirects_to_start(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/nid", data={"CallSid": CALL_SID, "Digits": ""}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        # Should say "No number entered" and redirect
        say = _find_element(root, "Say")
        assert say is not None
        assert "no number" in say.text.lower()
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/verify/start" in redirect.text

    def test_valid_nid_calls_start_otp_auth(self, ivr_client):
        client, mock_redis, _ = ivr_client
        import twilio_integration.webhook_handler as wh
        wh._mosip_service.start_otp_auth.reset_mock()

        resp = _post(client, "verify/nid",
                     data={"CallSid": CALL_SID, "Digits": "12345"}, lang="en")
        assert resp.status_code == 200
        wh._mosip_service.start_otp_auth.assert_awaited_once_with("12345")

    def test_valid_nid_stores_session_in_redis(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/nid",
                     data={"CallSid": CALL_SID, "Digits": "12345"}, lang="en")
        assert resp.status_code == 200
        key = f"ivr:verify_session:{CALL_SID}"
        assert key in mock_redis._store
        stored = json.loads(mock_redis._store[key])
        assert stored["national_id"] == "12345"
        assert "session" in stored
        assert stored["session"]["transaction_id"] == "txn-test-001"

    def test_valid_nid_prompts_for_6_digit_otp(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/nid",
                     data={"CallSid": CALL_SID, "Digits": "12345"}, lang="en")
        root = parse_twiml(resp.text)
        gather = _find_element(root, "Gather")
        assert gather is not None
        assert gather.get("numDigits") == "6"
        assert gather.get("finishOnKey") == "#"
        action = gather.get("action")
        assert "/twilio/voice/verify/otp" in action

    def test_esignet_failure_redirects_to_start(self, ivr_client):
        client, mock_redis, _ = ivr_client
        import twilio_integration.webhook_handler as wh
        wh._mosip_service.start_otp_auth.side_effect = Exception("eSignet error")
        try:
            resp = _post(client, "verify/nid",
                         data={"CallSid": CALL_SID, "Digits": "12345"}, lang="en")
            assert resp.status_code == 200
            root = parse_twiml(resp.text)
            # Should mention identity could not be verified
            say = _find_element(root, "Say")
            assert say is not None
            assert "could not be verified" in say.text.lower()
            redirect = _find_element(root, "Redirect")
            assert redirect is not None
            assert "/twilio/voice/verify/start" in redirect.text
        finally:
            wh._mosip_service.start_otp_auth.side_effect = None

    def test_otp_timeout_hangs_up(self, ivr_client):
        client, mock_redis, _ = ivr_client
        resp = _post(client, "verify/nid",
                     data={"CallSid": CALL_SID, "Digits": "12345"}, lang="en")
        root = parse_twiml(resp.text)
        # After the Gather there should be a timeout fallback with Hangup
        hangup = _find_element(root, "Hangup")
        assert hangup is not None
        # And a message about no OTP entered
        all_says = _find_elements(root, "Say")
        timeout_texts = [s.text for s in all_says if s.text and "no otp" in s.text.lower()]
        assert len(timeout_texts) > 0


# ═════════════════════════════════════════════════════════════════════════════
# TestVerifyOTP
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyOTP:
    """POST /twilio/voice/verify/otp — verify OTP with eSignet."""

    def _seed_session(self, mock_redis, call_sid=CALL_SID, national_id="12345"):
        """Pre-populate Redis with a verify session."""
        mock_redis._store[f"ivr:verify_session:{call_sid}"] = json.dumps({
            "national_id": national_id,
            "session": {
                "transaction_id": "txn-001",
                "code_verifier": "v",
                "nonce": "n",
                "state": "s",
                "cookies": {},
                "oauth_details_key": "k",
                "oauth_details_hash": "h",
            },
        })

    def test_valid_otp_redirects_to_enroll(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/enroll" in redirect.text

    def test_valid_otp_stores_verified_identity_in_redis(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        assert resp.status_code == 200
        key = f"ivr:verified_identity:{CALL_SID}"
        assert key in mock_redis._store
        stored = json.loads(mock_redis._store[key])
        assert stored["national_id"] == "12345"
        assert stored["mosip_individual_id"] == "MOSIP-IND-001"

    def test_valid_otp_cleans_up_session(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)
        session_key = f"ivr:verify_session:{CALL_SID}"
        assert session_key in mock_redis._store

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        assert resp.status_code == 200
        # Session should be deleted after successful verification
        assert session_key not in mock_redis._store

    def test_redirect_includes_national_id(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis, national_id="98765")

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "national_id=98765" in redirect.text

    def test_redirect_includes_step_0(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert "step=0" in redirect.text

    def test_invalid_otp_redirects_to_start(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)
        import twilio_integration.webhook_handler as wh
        wh._mosip_service.verify_otp_and_get_identity.side_effect = Exception("OTP invalid")
        try:
            resp = _post(client, "verify/otp",
                         data={"CallSid": CALL_SID, "Digits": "000000"}, lang="en")
            assert resp.status_code == 200
            root = parse_twiml(resp.text)
            say = _find_element(root, "Say")
            assert say is not None
            assert "incorrect" in say.text.lower() or "otp" in say.text.lower()
            redirect = _find_element(root, "Redirect")
            assert redirect is not None
            assert "/twilio/voice/verify/start" in redirect.text
        finally:
            wh._mosip_service.verify_otp_and_get_identity.side_effect = None

    def test_empty_otp_redirects_to_start(self, ivr_client):
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": ""}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        say = _find_element(root, "Say")
        assert say is not None
        assert "no otp" in say.text.lower()
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/verify/start" in redirect.text

    def test_expired_session_redirects(self, ivr_client):
        client, mock_redis, _ = ivr_client
        # Do NOT seed the session — simulate expiration
        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="en")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        say = _find_element(root, "Say")
        assert say is not None
        assert "expired" in say.text.lower() or "session" in say.text.lower()
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/verify/start" in redirect.text

    def test_sw_messages_use_play(self, ivr_client):
        """Swahili messages should use <Play> (gTTS) instead of <Say>."""
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="sw")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        # Swahili prompts should produce <Play> elements
        plays = _find_elements(root, "Play")
        assert len(plays) > 0, "Expected at least one <Play> element for Swahili"

    def test_sw_verified_identity_redirect_includes_lang(self, ivr_client):
        """Swahili flow should redirect to enroll with lang=sw."""
        client, mock_redis, _ = ivr_client
        self._seed_session(mock_redis)

        resp = _post(client, "verify/otp",
                     data={"CallSid": CALL_SID, "Digits": "123456"}, lang="sw")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = _find_element(root, "Redirect")
        assert redirect is not None
        assert "lang=sw" in redirect.text


# ═════════════════════════════════════════════════════════════════════════════
# TestVerifyFullFlow
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyFullFlow:
    """End-to-end: start -> nid -> otp -> enroll redirect with NID."""

    def test_full_verify_to_enroll_redirect(self, ivr_client):
        client, mock_redis, _ = ivr_client

        # Step 1: verify/start — should prompt for NID
        resp1 = _post(client, "verify/start",
                      data={"CallSid": CALL_SID}, lang="en")
        assert resp1.status_code == 200
        root1 = parse_twiml(resp1.text)
        gather1 = _find_element(root1, "Gather")
        assert gather1 is not None
        assert "/twilio/voice/verify/nid" in gather1.get("action")

        # Step 2: verify/nid — enter national ID, triggers OTP
        resp2 = _post(client, "verify/nid",
                      data={"CallSid": CALL_SID, "Digits": "54321"}, lang="en")
        assert resp2.status_code == 200
        root2 = parse_twiml(resp2.text)
        gather2 = _find_element(root2, "Gather")
        assert gather2 is not None
        assert gather2.get("numDigits") == "6"

        # Verify Redis session was created
        session_key = f"ivr:verify_session:{CALL_SID}"
        assert session_key in mock_redis._store
        session_data = json.loads(mock_redis._store[session_key])
        assert session_data["national_id"] == "54321"

        # Step 3: verify/otp — enter OTP, verify identity
        resp3 = _post(client, "verify/otp",
                      data={"CallSid": CALL_SID, "Digits": "111111"}, lang="en")
        assert resp3.status_code == 200
        root3 = parse_twiml(resp3.text)

        # Should say identity verified
        say = _find_element(root3, "Say")
        assert say is not None
        assert "verified" in say.text.lower()

        # Should redirect to enrollment with national_id
        redirect = _find_element(root3, "Redirect")
        assert redirect is not None
        assert "/twilio/voice/enroll" in redirect.text
        assert "national_id=54321" in redirect.text
        assert "step=0" in redirect.text

        # Verify Redis: session cleaned up, verified identity stored
        assert session_key not in mock_redis._store
        identity_key = f"ivr:verified_identity:{CALL_SID}"
        assert identity_key in mock_redis._store
        identity_data = json.loads(mock_redis._store[identity_key])
        assert identity_data["national_id"] == "54321"
        assert identity_data["mosip_individual_id"] == "MOSIP-IND-001"
