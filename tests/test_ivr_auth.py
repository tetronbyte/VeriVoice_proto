"""Tests for the IVR authentication flow.

Covers:
  POST /twilio/voice/authenticate         — NID gather, citizen lookup, challenge
  POST /twilio/voice/authenticate/callback — recording hand-off, hold audio
  Background: _run_auth_pipeline           — dual-stage voice+transcript auth,
                                             DB writes, retry/hangup logic
"""

import pytest

import twilio_integration.webhook_handler as wh
from app.models.auth_event import AuthEvent
from conftest import TestSessionLocal, parse_twiml


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/authenticate — prompt flow
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthPrompt:
    """Tests for the authenticate endpoint: NID gathering and challenge prompts."""

    def test_no_nid_prompts_gather_en(self, ivr_client):
        """No national_id and no Digits -> should emit a <Gather> for NID entry (English)."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/authenticate?lang=en")
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None, "Expected <Gather> for national ID input"
        assert "authenticate" in gather.attrib["action"]
        assert gather.attrib.get("finishOnKey") == "#"

        # English prompt uses <Say>
        say_elements = gather.findall("Say")
        assert len(say_elements) >= 1
        say_text = " ".join(s.text for s in say_elements if s.text)
        assert "national ID" in say_text.lower() or "national id" in say_text.lower()

    def test_no_nid_prompts_gather_sw(self, ivr_client):
        """No national_id and no Digits -> should emit a <Gather> with Swahili prompt."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/authenticate?lang=sw")
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None, "Expected <Gather> for national ID input"
        assert "authenticate" in gather.attrib["action"]

        # Swahili prompt uses <Play> (gTTS)
        play_elements = gather.findall("Play")
        assert len(play_elements) >= 1, "Swahili prompt should use <Play> for gTTS audio"

    def test_nid_from_digits_form_field(self, enrolled_citizen):
        """When Digits is provided via form data, it should be used as the national_id."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        resp = client.post(
            "/twilio/voice/authenticate?lang=en",
            data={"Digits": national_id},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should NOT show another Gather for NID -- should proceed to challenge
        gathers = root.findall("Gather")
        assert len(gathers) == 0, "Should not re-gather NID when Digits provided"

        # Should have a <Record> for the challenge phrase
        record = root.find("Record")
        assert record is not None, "Expected <Record> for challenge phrase recording"
        assert "challenge_id" in record.attrib.get("action", "")

    def test_unknown_nid_redirects_to_enroll(self, ivr_client):
        """If the national_id is not found in DB, redirect to enrollment."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/authenticate?lang=en&national_id=UNKNOWN-999",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should have a message about not enrolled
        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "not enrolled" in texts or "enroll" in texts

        # Should redirect to enroll
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/enroll" in redirect.text
        assert "UNKNOWN-999" in redirect.text

    def test_known_nid_generates_challenge_and_records(self, enrolled_citizen):
        """Known citizen -> generate challenge phrase, show <Record>."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        resp = client.post(
            f"/twilio/voice/authenticate?lang=en&national_id={national_id}",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should contain the challenge phrase text in a <Say>
        say_elements = root.findall("Say")
        all_text = " ".join(s.text for s in say_elements if s.text)
        # The mocked challenge phrase is "The sun rises over the mountain every morning"
        assert "sun rises" in all_text or "phrase" in all_text.lower()

        # Should have a <Record> element
        record = root.find("Record")
        assert record is not None, "Expected <Record> for voice capture"
        assert record.attrib.get("method") == "POST"

    def test_record_action_includes_challenge_id(self, enrolled_citizen):
        """The <Record> action URL should include challenge_id and national_id."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        resp = client.post(
            f"/twilio/voice/authenticate?lang=en&national_id={national_id}",
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        record = root.find("Record")
        assert record is not None
        action = record.attrib["action"]
        assert "challenge_id=test-chal-001" in action
        assert f"national_id={national_id}" in action
        assert "/twilio/voice/authenticate/callback" in action


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/authenticate/callback
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthCallback:
    """Tests for the authenticate callback: recording hand-off and hold audio."""

    def test_no_recording_retries(self, enrolled_citizen):
        """If no RecordingUrl is provided, redirect back to authenticate (retry)."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        resp = client.post(
            f"/twilio/voice/authenticate/callback?lang=en"
            f"&challenge_id=test-chal-001&national_id={national_id}&attempt=0",
            data={"CallSid": "CA-test-001"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should redirect back to authenticate (no pipeline launched)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/authenticate" in redirect.text
        assert f"national_id={national_id}" in redirect.text

        # Pipeline should NOT have been triggered
        mock_update_call.assert_not_called()

    def test_with_recording_plays_hold_audio(self, enrolled_citizen):
        """When RecordingUrl is provided, should launch pipeline and play hold audio."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        resp = client.post(
            f"/twilio/voice/authenticate/callback?lang=en"
            f"&challenge_id=test-chal-001&national_id={national_id}&attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE123",
                "CallSid": "CA-test-002",
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should contain hold audio (Say or Play elements for "processing")
        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "processing" in texts or "hold" in texts or "wait" in texts

        # Pipeline runs inline (create_task patched), so update_call should be called
        assert mock_update_call.call_count >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Background: _run_auth_pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthPipeline:
    """Tests for the background auth pipeline triggered by authenticate/callback."""

    def _trigger_auth_pipeline(self, client, national_id, attempt=0,
                               call_sid="CA-pipeline-test"):
        """Helper: POST to authenticate/callback with a recording to trigger the pipeline."""
        return client.post(
            f"/twilio/voice/authenticate/callback?lang=en"
            f"&challenge_id=test-chal-001&national_id={national_id}&attempt={attempt}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE999",
                "CallSid": call_sid,
            },
        )

    def test_granted_redirects_to_consent(self, enrolled_citizen):
        """When both voice and transcript match, pipeline should redirect to consent."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen
        # Default mocks: matching.match -> granted=True, challenge.match_transcript -> match=True
        self._trigger_auth_pipeline(client, national_id)

        # Inspect the TwiML sent by _update_call_twiml
        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]  # second positional arg
        root = parse_twiml(twiml_str)

        # Should contain "granted" text
        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "granted" in texts

        # Should redirect to consent
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/consent" in redirect.text
        assert f"citizen_id={citizen_id}" in redirect.text

    def test_denied_attempt0_retries(self, enrolled_citizen):
        """When denied on attempt 0, should redirect to authenticate with attempt=1."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # Override matching to return denied
        wh._matching_service.match.return_value = {"score": 0.30, "granted": False}

        self._trigger_auth_pipeline(client, national_id, attempt=0)

        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]
        root = parse_twiml(twiml_str)

        # Should contain "denied" text
        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "denied" in texts

        # Should redirect to authenticate with attempt=1
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/authenticate" in redirect.text
        assert "attempt=1" in redirect.text

    def test_denied_attempt1_hangup(self, enrolled_citizen):
        """When denied on attempt >= 1, should hang up."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        wh._matching_service.match.return_value = {"score": 0.25, "granted": False}

        self._trigger_auth_pipeline(client, national_id, attempt=1)

        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]
        root = parse_twiml(twiml_str)

        # Should contain "denied again" / "goodbye" text
        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "denied" in texts
        assert "goodbye" in texts.lower() or "later" in texts.lower()

        # Should hang up (no redirect)
        hangup = root.find("Hangup")
        assert hangup is not None, "Expected <Hangup> when denied on second attempt"

    def test_voice_pass_transcript_fail_denied(self, enrolled_citizen):
        """Voice match passes but transcript fails -> overall denied."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # Voice passes
        wh._matching_service.match.return_value = {"score": 0.85, "granted": True}
        # Transcript fails
        wh._challenge_service.match_transcript.return_value = {
            "match": False,
            "score": 0.20,
            "matched_words": 1,
            "total_words": 8,
        }

        self._trigger_auth_pipeline(client, national_id, attempt=0)

        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]
        root = parse_twiml(twiml_str)

        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "denied" in texts

        # attempt=0 -> should still allow retry
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "attempt=1" in redirect.text

    def test_both_pass_granted(self, enrolled_citizen):
        """Both voice and transcript pass -> access granted."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        wh._matching_service.match.return_value = {"score": 0.88, "granted": True}
        wh._challenge_service.match_transcript.return_value = {
            "match": True,
            "score": 0.95,
            "matched_words": 8,
            "total_words": 8,
        }

        self._trigger_auth_pipeline(client, national_id, attempt=0)

        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]
        root = parse_twiml(twiml_str)

        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "granted" in texts

        redirect = root.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/consent" in redirect.text

    def test_creates_auth_event_in_db(self, enrolled_citizen):
        """After running the pipeline, an AUTH_EVENT record should exist in the DB."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # Use defaults (granted)
        wh._matching_service.match.return_value = {"score": 0.92, "granted": True}
        wh._challenge_service.match_transcript.return_value = {
            "match": True, "score": 0.90, "matched_words": 7, "total_words": 8,
        }

        self._trigger_auth_pipeline(client, national_id)

        db = TestSessionLocal()
        try:
            events = db.query(AuthEvent).filter(
                AuthEvent.citizen_id == citizen_id,
            ).all()
            assert len(events) >= 1, "Expected at least one AUTH_EVENT record"
            event = events[-1]
            assert event.result == "granted"
            assert event.voice_match_score == pytest.approx(0.92)
        finally:
            db.close()

    def test_denied_creates_auth_event_in_db(self, enrolled_citizen):
        """Denied pipeline should also write an AUTH_EVENT with result='denied'."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        wh._matching_service.match.return_value = {"score": 0.30, "granted": False}

        self._trigger_auth_pipeline(client, national_id)

        db = TestSessionLocal()
        try:
            events = db.query(AuthEvent).filter(
                AuthEvent.citizen_id == citizen_id,
            ).all()
            assert len(events) >= 1
            event = events[-1]
            assert event.result == "denied"
            assert event.voice_match_score == pytest.approx(0.30)
        finally:
            db.close()

    def test_citizen_not_found_error(self, ivr_client):
        """If citizen is not found during pipeline, should send error TwiML."""
        client, mock_redis, mock_update_call = ivr_client

        resp = client.post(
            "/twilio/voice/authenticate/callback?lang=en"
            "&challenge_id=test-chal-001&national_id=GHOST-999&attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE404",
                "CallSid": "CA-not-found",
            },
        )
        assert resp.status_code == 200

        # Pipeline should have sent error TwiML
        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        twiml_str = call_args[0][1]
        root = parse_twiml(twiml_str)

        say_elements = root.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "not found" in texts or "enroll" in texts


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-step integration flow
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthMultiStepFlow:
    """End-to-end multi-step authentication flow test."""

    def test_full_auth_granted_en(self, enrolled_citizen):
        """Full flow: NID gather -> challenge -> callback -> pipeline -> consent redirect."""
        client, mock_redis, mock_update_call, citizen_id, national_id = enrolled_citizen

        # Ensure mocks return granted
        wh._matching_service.match.return_value = {"score": 0.90, "granted": True}
        wh._challenge_service.match_transcript.return_value = {
            "match": True, "score": 0.92, "matched_words": 7, "total_words": 8,
        }

        # Step 1: Hit authenticate with no NID -> should get Gather
        resp1 = client.post("/twilio/voice/authenticate?lang=en")
        assert resp1.status_code == 200
        root1 = parse_twiml(resp1.text)
        gather = root1.find("Gather")
        assert gather is not None, "Step 1: Expected <Gather> for NID"
        assert "authenticate" in gather.attrib["action"]

        # Step 2: Submit NID via Digits -> should get challenge + Record
        resp2 = client.post(
            "/twilio/voice/authenticate?lang=en",
            data={"Digits": national_id},
        )
        assert resp2.status_code == 200
        root2 = parse_twiml(resp2.text)
        record = root2.find("Record")
        assert record is not None, "Step 2: Expected <Record> for challenge phrase"
        action_url = record.attrib["action"]
        assert "challenge_id" in action_url
        assert f"national_id={national_id}" in action_url

        # Step 3: Submit recording to callback -> triggers pipeline
        resp3 = client.post(
            f"/twilio/voice/authenticate/callback?lang=en"
            f"&challenge_id=test-chal-001&national_id={national_id}&attempt=0",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-FULL",
                "CallSid": "CA-full-flow",
            },
        )
        assert resp3.status_code == 200

        # Step 4: Verify pipeline outcome -- should redirect to consent
        assert mock_update_call.call_count >= 1
        call_args = mock_update_call.call_args_list[-1]
        call_sid_arg = call_args[0][0]
        twiml_str = call_args[0][1]
        assert call_sid_arg == "CA-full-flow"

        root4 = parse_twiml(twiml_str)
        say_elements = root4.findall("Say")
        texts = " ".join(s.text for s in say_elements if s.text).lower()
        assert "granted" in texts

        redirect = root4.find("Redirect")
        assert redirect is not None
        assert "/twilio/voice/consent" in redirect.text
        assert f"citizen_id={citizen_id}" in redirect.text

        # Step 5: Verify DB record
        db = TestSessionLocal()
        try:
            events = db.query(AuthEvent).filter(
                AuthEvent.citizen_id == citizen_id,
            ).all()
            assert len(events) == 1
            assert events[0].result == "granted"
        finally:
            db.close()
