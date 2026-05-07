"""Tests for the IVR enrollment flow: NID gathering, phrase recording, callback
advancing, background enrollment pipeline, and full multi-step integration.

Covers:
  POST /twilio/voice/enroll           (steps 0-6)
  POST /twilio/voice/enroll/callback  (recording storage, step advancing, pipeline)
  _run_enrollment_pipeline            (DB writes, MOSIP linkage, error paths)
"""

import json

import pytest

from app.models.citizen import Citizen
from app.models.voice_template import VoiceTemplate
from conftest import TestSessionLocal, parse_twiml


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/enroll  — Step 0 (National ID gathering)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollStep0:
    """Step 0: gather national ID via DTMF before recording begins."""

    def test_no_nid_prompts_gather_en(self, ivr_client):
        """English: step=0 with no national_id → Gather for NID input."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/enroll?lang=en&step=0")
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None, "Expected <Gather> for NID input"

        # Prompt should mention national ID
        say = gather.find("Say")
        assert say is not None
        assert "national id" in say.text.lower()

        # Should NOT contain <Record> or <Hangup> at this step
        assert root.find("Record") is None
        assert root.find("Hangup") is None

    def test_no_nid_prompts_gather_sw(self, ivr_client):
        """Swahili: step=0 with no national_id → Gather with <Play> (gTTS)."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/enroll?lang=sw&step=0")
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None

        # Swahili uses <Play> not <Say>
        play = gather.find("Play")
        assert play is not None, "Swahili prompt should use <Play> (gTTS)"
        assert gather.find("Say") is None, "Swahili should not use <Say>"

    def test_prefilled_nid_skips_to_step1(self, ivr_client):
        """step=0 with national_id already provided → Redirect to step=1."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=0&national_id=KE-PREFILL-001"
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None, "Should redirect to step=1"
        assert "step=1" in redirect.text
        assert "national_id=KE-PREFILL-001" in redirect.text

        # Should NOT contain a Gather (NID already known)
        assert root.find("Gather") is None

    def test_gather_finishes_on_pound(self, ivr_client):
        """The NID Gather should use finish_on_key='#'."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/enroll?lang=en&step=0")
        root = parse_twiml(resp.text)

        gather = root.find("Gather")
        assert gather is not None
        assert gather.attrib.get("finishOnKey") == "#"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/enroll  — Steps 1-5 (Recording prompts)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollRecordingSteps:
    """Steps 1-5: play a phrase and record the caller's voice."""

    def test_step1_plays_phrase_and_records(self, ivr_client):
        """Step 1 with national_id → Say phrase prompt + Record verb."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=1&national_id=KE-REC-001"
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should contain at least one <Say> with the phrase prompt
        say_elements = root.findall("Say")
        assert len(say_elements) >= 1
        phrase_text = " ".join(s.text for s in say_elements if s.text)
        assert "please say" in phrase_text.lower() or "pound" in phrase_text.lower()

        # Should contain a <Record> verb
        record = root.find("Record")
        assert record is not None

    def test_record_has_max_length_10(self, ivr_client):
        """Record verb should have maxLength=10."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=2&national_id=KE-REC-002"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        assert record is not None
        assert record.attrib.get("maxLength") == "10"

    def test_record_action_points_to_callback(self, ivr_client):
        """Record action should point to /twilio/voice/enroll/callback with
        correct step, national_id, and session_id."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=3&national_id=KE-REC-003"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        assert record is not None
        action = record.attrib["action"]
        assert "/twilio/voice/enroll/callback" in action
        assert "step=3" in action
        assert "national_id=KE-REC-003" in action
        assert "session_id=" in action

    def test_sw_uses_play_for_prompt(self, ivr_client):
        """Swahili recording steps should use <Play> instead of <Say>."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=sw&step=1&national_id=KE-SW-001"
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        play_elements = root.findall("Play")
        assert len(play_elements) >= 1, "Swahili should use <Play> for phrase prompt"

        # Should still contain <Record>
        record = root.find("Record")
        assert record is not None

    def test_step_beyond_5_says_complete_and_hangup(self, ivr_client):
        """Step > ENROLLMENT_PHRASES (5) → 'Enrollment complete' + Hangup."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=6&national_id=KE-DONE-001"
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        say = root.find("Say")
        assert say is not None
        assert "complete" in say.text.lower()

        hangup = root.find("Hangup")
        assert hangup is not None

    def test_step_beyond_5_swahili(self, ivr_client):
        """Swahili step > 5 → uses <Play> for completion message + Hangup."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=sw&step=6&national_id=KE-DONE-SW"
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Swahili uses Play
        play_elements = root.findall("Play")
        assert len(play_elements) >= 1

        hangup = root.find("Hangup")
        assert hangup is not None

    def test_session_id_auto_created(self, ivr_client):
        """When session_id is empty, a new UUID should be generated and
        appear in the Record action URL."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=1&national_id=KE-SESS-001"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        action = record.attrib["action"]
        # session_id should be a non-empty UUID in the callback URL
        assert "session_id=" in action
        # Extract the session_id value
        parts = action.split("session_id=")
        session_id = parts[1].split("&")[0]
        assert len(session_id) > 0, "session_id should be auto-generated"
        assert session_id != ""

    def test_session_id_preserved_across_steps(self, ivr_client):
        """When session_id is provided, it should be preserved in the callback URL."""
        client, mock_redis, mock_update_call = ivr_client
        sid = "test-session-12345"
        resp = client.post(
            f"/twilio/voice/enroll?lang=en&step=2&national_id=KE-KEEP-001&session_id={sid}"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        action = record.attrib["action"]
        assert f"session_id={sid}" in action

    def test_record_uses_trim_silence(self, ivr_client):
        """Record verb should have trim='trim-silence'."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=1&national_id=KE-TRIM-001"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        assert record.attrib.get("trim") == "trim-silence"

    def test_record_uses_finish_on_key_pound(self, ivr_client):
        """Record verb should have finishOnKey='#'."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=1&national_id=KE-FINISH-001"
        )
        root = parse_twiml(resp.text)

        record = root.find("Record")
        assert record.attrib.get("finishOnKey") == "#"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/enroll/callback  — Recording storage + step advancing
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollCallback:
    """Callback: store recording URL, advance to next step or trigger pipeline."""

    def test_callback_stores_url_in_redis(self, ivr_client):
        """Callback should store the RecordingUrl in Redis under the session key."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-001"
        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=1&national_id=KE-CB-001&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CB-001",
                "CallSid": "CA-CB-001",
            },
        )
        assert resp.status_code == 200

        # Check Redis for stored recording
        raw = mock_redis.get(f"ivr:enroll_recordings:{session_id}")
        assert raw is not None
        recordings = json.loads(raw)
        assert "https://api.twilio.com/recordings/RE-CB-001" in recordings

    def test_callback_step1_redirects_to_step2(self, ivr_client):
        """Callback with step=1 → next_step=2 ≤ 5 → Redirect to step=2."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-002"
        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=1&national_id=KE-CB-002&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CB-002",
                "CallSid": "CA-CB-002",
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "step=2" in redirect.text
        assert "national_id=KE-CB-002" in redirect.text
        assert f"session_id={session_id}" in redirect.text

    def test_callback_step4_redirects_to_step5(self, ivr_client):
        """Callback with step=4 → next_step=5 ≤ 5 → Redirect to step=5."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-004"
        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=4&national_id=KE-CB-004&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CB-004",
                "CallSid": "CA-CB-004",
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "step=5" in redirect.text

    def test_callback_final_step_triggers_pipeline_and_hold(self, ivr_client):
        """Callback with step=5 → next_step=6 > 5 → pops recordings, runs
        pipeline, and plays hold audio."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-final"
        call_sid = "CA-FINAL-001"

        # Pre-populate Redis with 4 recordings (callback will add the 5th)
        existing_recs = [
            f"https://api.twilio.com/recordings/RE-FINAL-{i}" for i in range(1, 5)
        ]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}",
            json.dumps(existing_recs),
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id=KE-FINAL-001&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-FINAL-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should NOT redirect (pipeline fired instead)
        redirect = root.find("Redirect")
        assert redirect is None, "Final step should not redirect — pipeline runs instead"

        # Should contain hold audio (Say or Play for processing message)
        say_elements = root.findall("Say")
        say_texts = [s.text for s in say_elements if s.text]
        all_text = " ".join(say_texts).lower()
        assert "processing" in all_text or "wait" in all_text or "hold" in all_text, \
            f"Expected hold/processing message, got: {say_texts}"

        # Recordings should be popped from Redis (consumed by pipeline)
        raw = mock_redis.get(f"ivr:enroll_recordings:{session_id}")
        assert raw is None, "Recordings should be popped from Redis after pipeline fires"

    def test_callback_insufficient_recordings_errors(self, ivr_client):
        """When fewer than 5 recordings are stored and final step fires,
        an error message should be played and the call hung up."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-insufficient"
        call_sid = "CA-INSUFF-001"

        # Only 2 existing recordings (callback adds 1 = 3 total, but need 5)
        # Actually, we need to pre-populate with fewer recs. When step=5
        # and callback adds 1, we get total in the list. But the pipeline
        # checks len(all_recordings) < 5. Let's pre-pop with 2 so total = 3.
        # Wait -- the flow stores them via _get/_set. Let me trace:
        # recs = _get (gets 2), append RecordingUrl (now 3), _set (3 stored).
        # Then next_step = 6 > 5, so we pop. Pop returns 3. 3 < 5 = error.
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}",
            json.dumps(["https://rec1.com", "https://rec2.com"]),
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id=KE-INSUFF&session_id={session_id}",
            data={
                "RecordingUrl": "https://rec3.com",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        # Should contain an error message
        say_elements = root.findall("Say")
        say_texts = [s.text for s in say_elements if s.text]
        all_text = " ".join(say_texts).lower()
        assert "error" in all_text or "not enough" in all_text, \
            f"Expected error about insufficient recordings, got: {say_texts}"

        # Should hang up
        hangup = root.find("Hangup")
        assert hangup is not None

    def test_callback_step3_redirects_to_step4(self, ivr_client):
        """Callback with step=3 → next_step=4 ≤ 5 → Redirect to step=4."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "cb-session-003"
        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=3&national_id=KE-CB-003&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-CB-003",
                "CallSid": "CA-CB-003",
            },
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "step=4" in redirect.text

    def test_callback_preserves_lang_in_redirect(self, ivr_client):
        """Redirect URL should preserve the lang parameter."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/enroll/callback?lang=sw&step=2&national_id=KE-LANG&session_id=lang-sess",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-LANG",
                "CallSid": "CA-LANG",
            },
        )
        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "lang=sw" in redirect.text


# ═══════════════════════════════════════════════════════════════════════════════
# _run_enrollment_pipeline  — Background processing
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollmentPipeline:
    """Background pipeline: creates Citizen + VoiceTemplate, handles errors."""

    def test_pipeline_creates_citizen_and_template(self, ivr_client):
        """Successful pipeline creates a CITIZEN and VOICE_TEMPLATE in the DB."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-001"
        call_sid = "CA-PIPE-001"
        nid = "KE-PIPE-CREATE-001"

        # Pre-populate 4 recordings; the 5th comes from the callback
        recs = [f"https://api.twilio.com/recordings/RE-PIPE-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-PIPE-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        # Verify DB records via TestSessionLocal
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None, "Citizen should be created by pipeline"
            assert citizen.preferred_language == "en"

            template = db.query(VoiceTemplate).filter(
                VoiceTemplate.citizen_id == citizen.citizen_id
            ).first()
            assert template is not None, "VoiceTemplate should be created by pipeline"
            assert template.is_active is True
            assert len(template.he_ciphertext) > 0
        finally:
            db.close()

    def test_pipeline_duplicate_nid_sends_error_twiml(self, ivr_client):
        """Pipeline with a duplicate national ID should send an error TwiML
        to the live call via _update_call_twiml."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-dup"
        call_sid = "CA-PIPE-DUP"
        nid = "KE-PIPE-DUP-001"

        # First: create a citizen with this NID so the pipeline finds a duplicate
        db = TestSessionLocal()
        try:
            from app.db.crud import create_citizen
            create_citizen(db, national_id_number=nid, preferred_language="en",
                           phone_number="+254700000000")
        finally:
            db.close()

        # Pre-populate 4 recordings
        recs = [f"https://api.twilio.com/recordings/RE-DUP-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-DUP-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        # The pipeline should have called _update_call_twiml with an error
        assert mock_update_call.call_count >= 1, \
            "Pipeline should call _update_call_twiml for duplicate NID"

        # Check the TwiML sent contains the error message
        twiml_sent = mock_update_call.call_args_list[-1][0][1]  # second positional arg
        assert "already enrolled" in twiml_sent.lower() or "tayari" in twiml_sent.lower(), \
            f"Expected 'already enrolled' error in TwiML, got: {twiml_sent}"

    def test_pipeline_links_verified_identity(self, ivr_client):
        """When ivr:verified_identity:{CallSid} exists in Redis with matching
        national_id, the pipeline should link mosip_individual_id and set
        identity_verified=True on the new citizen."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-verify"
        call_sid = "CA-PIPE-VERIFY"
        nid = "KE-PIPE-VERIFY-001"
        mosip_id = "MOSIP-VERIFIED-999"

        # Set up verified identity in Redis
        mock_redis.set(
            f"ivr:verified_identity:{call_sid}",
            json.dumps({
                "national_id": nid,
                "mosip_individual_id": mosip_id,
            }),
        )

        # Pre-populate 4 recordings
        recs = [f"https://api.twilio.com/recordings/RE-VER-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-VER-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        # Verify the citizen was created with MOSIP linkage
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None
            assert citizen.mosip_individual_id == mosip_id
            assert citizen.identity_verified is True
        finally:
            db.close()

    def test_pipeline_success_sends_complete_twiml(self, ivr_client):
        """Successful pipeline should call _update_call_twiml with a
        completion message."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-success"
        call_sid = "CA-PIPE-SUCCESS"
        nid = "KE-PIPE-SUCCESS-001"

        # Pre-populate 4 recordings
        recs = [f"https://api.twilio.com/recordings/RE-SUC-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-SUC-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        # Pipeline should have sent completion TwiML
        assert mock_update_call.call_count >= 1, \
            "Pipeline should call _update_call_twiml on success"
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "complete" in twiml_sent.lower() or "asante" in twiml_sent.lower(), \
            f"Expected completion message in TwiML, got: {twiml_sent}"

    def test_pipeline_without_verified_identity_sets_defaults(self, ivr_client):
        """When no verified identity is in Redis, the citizen should have
        mosip_individual_id=None and identity_verified=False."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-noverify"
        call_sid = "CA-PIPE-NOVERIFY"
        nid = "KE-PIPE-NOVERIFY-001"

        # No ivr:verified_identity key set — just recordings
        recs = [f"https://api.twilio.com/recordings/RE-NOV-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=en&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-NOV-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None
            assert citizen.mosip_individual_id is None
            assert citizen.identity_verified is False
        finally:
            db.close()

    def test_pipeline_swahili_sends_swahili_twiml(self, ivr_client):
        """Pipeline running in Swahili should send Swahili completion message."""
        client, mock_redis, mock_update_call = ivr_client
        session_id = "pipe-session-sw"
        call_sid = "CA-PIPE-SW"
        nid = "KE-PIPE-SW-001"

        recs = [f"https://api.twilio.com/recordings/RE-SW-{i}" for i in range(1, 5)]
        mock_redis.set(
            f"ivr:enroll_recordings:{session_id}", json.dumps(recs)
        )

        resp = client.post(
            f"/twilio/voice/enroll/callback?lang=sw&step=5&national_id={nid}&session_id={session_id}",
            data={
                "RecordingUrl": "https://api.twilio.com/recordings/RE-SW-5",
                "CallSid": call_sid,
            },
        )
        assert resp.status_code == 200

        # Pipeline should have sent TwiML — Swahili uses <Play> so the TwiML
        # will contain a Play element rather than Say
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # Swahili TwiML should contain <Play> (gTTS URL)
        assert "Play" in twiml_sent or "Asante" in twiml_sent or "http" in twiml_sent


# ═══════════════════════════════════════════════════════════════════════════════
# Full multi-step enrollment flow
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollMultiStepFlow:
    """Integration test: step through the entire 5-step enrollment from
    NID gathering through pipeline completion."""

    def test_full_5step_enrollment_en(self, ivr_client):
        """Walk through the complete English enrollment flow:
        step=0 (NID) → step=1..5 (record) → callbacks → pipeline → DB check."""
        client, mock_redis, mock_update_call = ivr_client
        nid = "KE-FULL-E2E-001"
        call_sid = "CA-FULL-E2E"

        # ── Step 0: NID Gather ───────────────────────────────────────────
        resp = client.post("/twilio/voice/enroll?lang=en&step=0")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None, "Step 0 should show Gather for NID"
        gather_action = gather.attrib["action"]
        assert "step=1" in gather_action

        # Simulate user entering NID digits (Twilio posts to the Gather action)
        # The Gather action is /twilio/voice/enroll?lang=en&step=1
        # When Twilio posts to this, it sends Digits= in the form body
        resp = client.post(
            "/twilio/voice/enroll?lang=en&step=1",
            data={"Digits": nid},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = root.find("Record")
        assert record is not None, "Step 1 should have a <Record> verb"

        # Extract session_id from the record action URL
        action_url = record.attrib["action"]
        session_id = action_url.split("session_id=")[1].split("&")[0]
        assert len(session_id) > 0

        # ── Steps 1-5: Record phrases + callbacks ────────────────────────
        for step in range(1, 6):
            if step > 1:
                # Steps 2-5: hit the enroll endpoint directly (simulating redirect)
                resp = client.post(
                    f"/twilio/voice/enroll?lang=en&step={step}&national_id={nid}&session_id={session_id}"
                )
                assert resp.status_code == 200
                root = parse_twiml(resp.text)
                record = root.find("Record")
                assert record is not None, f"Step {step} should have <Record>"

            # Simulate callback with recording
            rec_url = f"https://api.twilio.com/recordings/RE-FULL-{step}"
            resp = client.post(
                f"/twilio/voice/enroll/callback?lang=en&step={step}&national_id={nid}&session_id={session_id}",
                data={
                    "RecordingUrl": rec_url,
                    "CallSid": call_sid,
                },
            )
            assert resp.status_code == 200
            root = parse_twiml(resp.text)

            if step < 5:
                # Should redirect to next step
                redirect = root.find("Redirect")
                assert redirect is not None, f"Callback step={step} should redirect to step={step + 1}"
                assert f"step={step + 1}" in redirect.text
            else:
                # Step 5 callback (next_step=6 > 5): pipeline fires, hold audio plays
                redirect = root.find("Redirect")
                assert redirect is None, "Step 5 callback should not redirect — pipeline runs"

                # Verify hold audio
                say_elements = root.findall("Say")
                say_texts = [s.text for s in say_elements if s.text]
                all_text = " ".join(say_texts).lower()
                assert "processing" in all_text or "wait" in all_text or "hold" in all_text

        # ── Verify pipeline ran successfully ─────────────────────────────
        assert mock_update_call.call_count >= 1, \
            "Pipeline should have called _update_call_twiml"

        # Check the completion TwiML
        last_twiml = mock_update_call.call_args_list[-1][0][1]
        assert "complete" in last_twiml.lower() or "Asante" in last_twiml

        # The first arg should be the call_sid
        sent_call_sid = mock_update_call.call_args_list[-1][0][0]
        assert sent_call_sid == call_sid

        # ── Verify DB records ────────────────────────────────────────────
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None, "Citizen should exist after full enrollment"
            assert citizen.preferred_language == "en"
            assert citizen.identity_verified is False
            assert citizen.mosip_individual_id is None

            template = db.query(VoiceTemplate).filter(
                VoiceTemplate.citizen_id == citizen.citizen_id
            ).first()
            assert template is not None, "VoiceTemplate should exist"
            assert template.is_active is True
        finally:
            db.close()

    def test_full_5step_enrollment_sw(self, ivr_client):
        """Swahili variant of the full flow — verifies Play is used throughout."""
        client, mock_redis, mock_update_call = ivr_client
        nid = "KE-FULL-SW-001"
        call_sid = "CA-FULL-SW"

        # Step 0: Swahili NID gather
        resp = client.post("/twilio/voice/enroll?lang=sw&step=0")
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None
        # Swahili should use Play inside Gather
        play = gather.find("Play")
        assert play is not None, "Swahili NID prompt should use <Play>"

        # Submit NID and go through steps 1-5
        session_id = None
        for step in range(1, 6):
            params = f"lang=sw&step={step}&national_id={nid}"
            if session_id:
                params += f"&session_id={session_id}"
            resp = client.post(f"/twilio/voice/enroll?{params}")
            assert resp.status_code == 200

            root = parse_twiml(resp.text)
            record = root.find("Record")
            assert record is not None, f"Step {step} (sw) should have <Record>"

            # Extract session_id on first step
            if session_id is None:
                action_url = record.attrib["action"]
                session_id = action_url.split("session_id=")[1].split("&")[0]

            # Swahili uses Play for prompts
            play_elements = root.findall("Play")
            assert len(play_elements) >= 1, f"Step {step} (sw) should use <Play>"

            # Callback
            resp = client.post(
                f"/twilio/voice/enroll/callback?lang=sw&step={step}&national_id={nid}&session_id={session_id}",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-SW-FULL-{step}",
                    "CallSid": call_sid,
                },
            )
            assert resp.status_code == 200

        # Verify citizen created
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None
            assert citizen.preferred_language == "sw"
        finally:
            db.close()

    def test_full_flow_with_prefilled_nid(self, ivr_client):
        """Enrollment flow when national_id is pre-filled (e.g., from verify/otp).
        Step 0 should redirect directly to step 1 without Gather."""
        client, mock_redis, mock_update_call = ivr_client
        nid = "KE-PREFILL-FULL-001"
        call_sid = "CA-PREFILL-FULL"

        # Step 0 with pre-filled NID → redirect to step 1
        resp = client.post(
            f"/twilio/voice/enroll?lang=en&step=0&national_id={nid}"
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "step=1" in redirect.text
        assert f"national_id={nid}" in redirect.text

        # Follow the redirect to step 1
        resp = client.post(
            f"/twilio/voice/enroll?lang=en&step=1&national_id={nid}"
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        record = root.find("Record")
        assert record is not None

        # Extract session_id
        action_url = record.attrib["action"]
        session_id = action_url.split("session_id=")[1].split("&")[0]

        # Run through remaining steps 1-5 via callbacks
        for step in range(1, 6):
            if step > 1:
                resp = client.post(
                    f"/twilio/voice/enroll?lang=en&step={step}&national_id={nid}&session_id={session_id}"
                )
                assert resp.status_code == 200

            resp = client.post(
                f"/twilio/voice/enroll/callback?lang=en&step={step}&national_id={nid}&session_id={session_id}",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-PRE-{step}",
                    "CallSid": call_sid,
                },
            )
            assert resp.status_code == 200

        # Verify citizen created
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None
        finally:
            db.close()

    def test_full_flow_with_verified_identity(self, ivr_client):
        """Full enrollment with a pre-existing verified identity in Redis.
        The resulting citizen should have identity_verified=True and
        the correct mosip_individual_id."""
        client, mock_redis, mock_update_call = ivr_client
        nid = "KE-VER-FULL-001"
        call_sid = "CA-VER-FULL"
        mosip_id = "MOSIP-VER-FULL-999"

        # Set up verified identity
        mock_redis.set(
            f"ivr:verified_identity:{call_sid}",
            json.dumps({
                "national_id": nid,
                "mosip_individual_id": mosip_id,
            }),
        )

        session_id = "ver-full-session"
        # Run through steps 1-5
        for step in range(1, 6):
            resp = client.post(
                f"/twilio/voice/enroll?lang=en&step={step}&national_id={nid}&session_id={session_id}"
            )
            assert resp.status_code == 200

            resp = client.post(
                f"/twilio/voice/enroll/callback?lang=en&step={step}&national_id={nid}&session_id={session_id}",
                data={
                    "RecordingUrl": f"https://api.twilio.com/recordings/RE-VERFULL-{step}",
                    "CallSid": call_sid,
                },
            )
            assert resp.status_code == 200

        # Verify citizen with MOSIP linkage
        db = TestSessionLocal()
        try:
            citizen = db.query(Citizen).filter(
                Citizen.national_id_number == nid
            ).first()
            assert citizen is not None
            assert citizen.mosip_individual_id == mosip_id
            assert citizen.identity_verified is True
        finally:
            db.close()
