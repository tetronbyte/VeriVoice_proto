"""Comprehensive tests for the IVR service catalog flow.

Covers:
  - /twilio/voice/service/menu         — consent validation, DTMF service selection
  - /twilio/voice/service              — question prompts, readback, unknown service
  - /twilio/voice/service/callback     — recording handling, hold audio
  - /twilio/voice/service/confirm      — yes/no/unclear confirmation pipeline
  - /twilio/voice/service/correct      — per-question correction prompt
  - /twilio/voice/service/correct/callback — correction classification pipeline
  - All 5 services parametrized
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.service_form import ServiceForm
from conftest import TestSessionLocal, parse_twiml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CALL_SID = "CA_test_service_001"
BASE = "http://localhost:8000"


def _menu_url(lang="en", citizen_id="", consent_token_id=""):
    return (
        f"/twilio/voice/service/menu?lang={lang}"
        f"&citizen_id={citizen_id}&consent_token_id={consent_token_id}"
    )


def _service_url(
    lang="en", citizen_id="", consent_token_id="", service_code="pension",
    question_index=0, correction_attempts=0, unclear_attempt=0,
    correcting_index=-1, call_sid=CALL_SID,
):
    return (
        f"/twilio/voice/service?lang={lang}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}&service_code={service_code}"
        f"&question_index={question_index}"
        f"&correction_attempts={correction_attempts}"
        f"&unclear_attempt={unclear_attempt}"
        f"&correcting_index={correcting_index}"
        f"&call_sid={call_sid}"
    )


def _callback_url(
    lang="en", citizen_id="", consent_token_id="", service_code="pension",
    question_index=0, correction_attempts=0, correcting_index=-1,
):
    return (
        f"/twilio/voice/service/callback?lang={lang}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}&service_code={service_code}"
        f"&question_index={question_index}"
        f"&correction_attempts={correction_attempts}"
        f"&correcting_index={correcting_index}"
    )


def _confirm_url(
    lang="en", citizen_id="", consent_token_id="", service_code="pension",
    correction_attempts=0, unclear_attempt=0,
):
    return (
        f"/twilio/voice/service/confirm?lang={lang}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}&service_code={service_code}"
        f"&correction_attempts={correction_attempts}"
        f"&unclear_attempt={unclear_attempt}"
    )


def _correct_url(
    lang="en", citizen_id="", consent_token_id="", service_code="pension",
    correction_attempts=0, unclear_attempt=0, call_sid=CALL_SID,
):
    return (
        f"/twilio/voice/service/correct?lang={lang}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}&service_code={service_code}"
        f"&correction_attempts={correction_attempts}"
        f"&unclear_attempt={unclear_attempt}"
        f"&call_sid={call_sid}"
    )


def _correct_callback_url(
    lang="en", citizen_id="", consent_token_id="", service_code="pension",
    correction_attempts=0, unclear_attempt=0,
):
    return (
        f"/twilio/voice/service/correct/callback?lang={lang}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}&service_code={service_code}"
        f"&correction_attempts={correction_attempts}"
        f"&unclear_attempt={unclear_attempt}"
    )


def _twiml_text(root: ET.Element) -> str:
    """Collect all text content from a TwiML tree for assertion convenience."""
    parts = []
    for elem in root.iter():
        if elem.text:
            parts.append(elem.text.strip())
    return " ".join(parts)


def _find_redirect(root: ET.Element) -> str | None:
    """Return the text of the first <Redirect> element or None."""
    redir = root.find(".//Redirect")
    return redir.text if redir is not None else None


def _has_hangup(root: ET.Element) -> bool:
    return root.find(".//Hangup") is not None


def _has_gather(root: ET.Element) -> bool:
    return root.find(".//Gather") is not None


def _has_record(root: ET.Element) -> bool:
    return root.find(".//Record") is not None


def _seed_answers(mock_redis, call_sid=CALL_SID):
    """Pre-populate Redis with 3 answers for the given call."""
    answers = {"q0": "three times", "q1": "full", "q2": "M-Pesa wallet"}
    mock_redis.set(
        f"ivr:service_answers:{call_sid}",
        json.dumps(answers),
    )
    return answers


# ===========================================================================
# TestServiceMenu
# ===========================================================================


class TestServiceMenu:
    """POST /twilio/voice/service/menu — consent gate + DTMF service selection."""

    def test_valid_consent_shows_menu_en(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        assert _has_gather(root)
        text = _twiml_text(root)
        assert "select a service" in text.lower() or "Press 1" in text

    def test_valid_consent_shows_menu_sw(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="sw", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        # Swahili uses gTTS <Play>, so we expect a Gather with Play inside
        assert _has_gather(root)

    def test_digit_1_selects_pension(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "1", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redir = _find_redirect(root)
        assert redir is not None
        assert "service_code=pension" in redir

    def test_digit_5_selects_telemedicine(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "5", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redir = _find_redirect(root)
        assert redir is not None
        assert "service_code=telemedicine" in redir

    def test_invalid_digit_reprompts(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "9", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        assert "invalid" in text.lower() or "Invalid" in text
        # Should still show Gather for re-prompt
        assert _has_gather(root)

    def test_invalid_consent_token_hangup(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id="bogus-token-id"),
            data={"Digits": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        assert _has_hangup(root)

    def test_expired_consent_token_hangup(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        # Expire the token
        db = TestSessionLocal()
        try:
            from app.models.consent_token import ConsentToken
            token = db.query(ConsentToken).filter(ConsentToken.token_id == tid).first()
            token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()
        finally:
            db.close()

        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        assert _has_hangup(root)

    def test_revoked_consent_token_hangup(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        # Revoke the token
        db = TestSessionLocal()
        try:
            from app.models.consent_token import ConsentToken
            token = db.query(ConsentToken).filter(ConsentToken.token_id == tid).first()
            token.is_revoked = True
            db.commit()
        finally:
            db.close()

        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        assert _has_hangup(root)


# ===========================================================================
# TestServiceQuestions
# ===========================================================================


class TestServiceQuestions:
    """POST /twilio/voice/service — question prompts and readback."""

    def test_question_0_plays_first_question(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _service_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=0),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        # First pension question mentions "Inua Jamii"
        assert "Inua Jamii" in text
        assert _has_record(root)

    def test_question_2_plays_third_question(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _service_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=2),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        # Third pension question mentions "M-Pesa" or "funds"
        assert "funds" in text.lower() or "M-Pesa" in text
        assert _has_record(root)

    def test_question_3_plays_readback(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        resp = client.post(
            _service_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=3),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        # Readback should include the answers
        assert "three times" in text or "full" in text or "M-Pesa wallet" in text
        # Should record for yes/no confirmation
        assert _has_record(root)
        # Record action should point to /confirm
        record = root.find(".//Record")
        assert "confirm" in record.attrib.get("action", "")

    def test_unknown_service_code_hangup(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _service_url(citizen_id=cid, consent_token_id=tid, service_code="nonexistent", question_index=0),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        assert _has_hangup(root)


# ===========================================================================
# TestServiceCallback
# ===========================================================================


class TestServiceCallback:
    """POST /twilio/voice/service/callback — recording receipt and hold audio."""

    def test_no_recording_advances_to_next_question(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=0),
            data={"RecordingUrl": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redir = _find_redirect(root)
        assert redir is not None
        assert "question_index=1" in redir

    def test_no_recording_correcting_jumps_to_readback(self, consented_citizen):
        """When correcting Q1 and no recording, should advance to readback (index=3)."""
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _callback_url(
                citizen_id=cid, consent_token_id=tid, service_code="pension",
                question_index=1, correcting_index=1,
            ),
            data={"RecordingUrl": "", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redir = _find_redirect(root)
        assert redir is not None
        assert "question_index=3" in redir

    def test_with_recording_plays_hold(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=0),
            data={"RecordingUrl": "https://api.twilio.com/rec/RE123", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        # Should contain hold message
        assert "processing" in text.lower() or "hold" in text.lower() or "Processing" in text


# ===========================================================================
# TestServiceTranscriptionPipeline
# ===========================================================================


class TestServiceTranscriptionPipeline:
    """Background pipeline _run_service_transcription via service/callback."""

    def test_transcribes_and_stores_in_redis(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=0),
            data={"RecordingUrl": "https://api.twilio.com/rec/RE123", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        # Pipeline runs inline; check Redis for the stored answer
        raw = mock_redis.get(f"ivr:service_answers:{CALL_SID}")
        assert raw is not None
        answers = json.loads(raw)
        assert "q0" in answers
        # Default mock transcription returns "yes I agree"
        assert answers["q0"] == "yes I agree"

    def test_advances_to_next_question(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension", question_index=1),
            data={"RecordingUrl": "https://api.twilio.com/rec/RE456", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        # Check mock_update_call was invoked with redirect to question_index=2
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "question_index=2" in twiml_sent

    def test_correcting_index_jumps_to_readback(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        resp = client.post(
            _callback_url(
                citizen_id=cid, consent_token_id=tid, service_code="pension",
                question_index=1, correcting_index=1,
            ),
            data={"RecordingUrl": "https://api.twilio.com/rec/RE789", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        # Pipeline should redirect to question_index=3 (readback)
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "question_index=3" in twiml_sent
        # correcting_index should be reset to -1
        assert "correcting_index=-1" in twiml_sent


# ===========================================================================
# TestServiceConfirmPipeline
# ===========================================================================


class TestServiceConfirmPipeline:
    """Background pipeline _run_service_confirm_pipeline via service/confirm."""

    def test_yes_persists_service_form(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        # classify_yes_no is patched to "yes" by default in conftest
        resp = client.post(
            _confirm_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
            data={"RecordingUrl": "https://api.twilio.com/rec/RECONF", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        # Check DB for SERVICE_FORM
        db = TestSessionLocal()
        try:
            form = db.query(ServiceForm).filter(ServiceForm.citizen_id == cid).first()
            assert form is not None
            assert form.service_code == "pension"
            assert form.ministry_code == "MLSP"
            assert form.consent_token_id == tid
            parsed = json.loads(form.answers_json)
            assert parsed["payment_count"] == "three times"
            assert parsed["withdrawal_type"] == "full"
            assert parsed["delivery_method"] == "M-Pesa wallet"
        finally:
            db.close()

    def test_yes_announces_reference_id(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        resp = client.post(
            _confirm_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
            data={"RecordingUrl": "https://api.twilio.com/rec/RECONF2", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        # Check mock_update_call for reference ID announcement
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # Should mention "reference" (EN) and contain a hangup
        assert "reference" in twiml_sent.lower() or "Reference" in twiml_sent
        assert "Hangup" in twiml_sent

    def test_no_redirects_to_correct(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="no"):
            resp = client.post(
                _confirm_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
                data={"RecordingUrl": "https://api.twilio.com/rec/RENO", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "/twilio/voice/service/correct" in twiml_sent
        assert "correction_attempts=1" in twiml_sent

    def test_no_max_corrections_gives_up(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="no"):
            resp = client.post(
                _confirm_url(
                    citizen_id=cid, consent_token_id=tid, service_code="pension",
                    correction_attempts=3,
                ),
                data={"RecordingUrl": "https://api.twilio.com/rec/REMAX", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "Hangup" in twiml_sent
        # Should say something about unable to complete
        assert "unable" in twiml_sent.lower() or "Unable" in twiml_sent

    def test_unclear_first_retries(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="unclear"):
            resp = client.post(
                _confirm_url(
                    citizen_id=cid, consent_token_id=tid, service_code="pension",
                    unclear_attempt=0,
                ),
                data={"RecordingUrl": "https://api.twilio.com/rec/REUNC1", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        # Should redirect back to service with question_index=3, unclear_attempt=1
        assert "question_index=3" in twiml_sent
        assert "unclear_attempt=1" in twiml_sent

    def test_unclear_second_gives_up(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        _seed_answers(mock_redis)
        with patch("twilio_integration.webhook_handler.classify_yes_no", return_value="unclear"):
            resp = client.post(
                _confirm_url(
                    citizen_id=cid, consent_token_id=tid, service_code="pension",
                    unclear_attempt=1,
                ),
                data={"RecordingUrl": "https://api.twilio.com/rec/REUNC2", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "Hangup" in twiml_sent


# ===========================================================================
# TestServiceCorrect
# ===========================================================================


class TestServiceCorrect:
    """POST /twilio/voice/service/correct — "which question?" prompt."""

    def test_prompts_which_question_en(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _correct_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        assert "one" in text.lower() or "One" in text
        assert "Two" in text or "two" in text.lower()
        assert "Three" in text or "three" in text.lower()
        assert _has_record(root)

    def test_unclear_attempt_shows_clarification(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _correct_url(
                citizen_id=cid, consent_token_id=tid, service_code="pension",
                unclear_attempt=1,
            ),
            data={"CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        text = _twiml_text(root)
        # Should contain clarification about not catching input
        assert "didn't catch" in text.lower() or "catch" in text.lower()


# ===========================================================================
# TestServiceCorrectPipeline
# ===========================================================================


class TestServiceCorrectPipeline:
    """Background pipeline _run_service_correct_pipeline via correct/callback."""

    def test_parses_one_redirects_to_q0(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        # parse_question_number is patched to return 0 by default
        resp = client.post(
            _correct_callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
            data={"RecordingUrl": "https://api.twilio.com/rec/RECORR1", "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "question_index=0" in twiml_sent
        assert "correcting_index=0" in twiml_sent

    def test_parses_two_redirects_to_q1(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        with patch("twilio_integration.webhook_handler.parse_question_number", return_value=1):
            resp = client.post(
                _correct_callback_url(citizen_id=cid, consent_token_id=tid, service_code="pension"),
                data={"RecordingUrl": "https://api.twilio.com/rec/RECORR2", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "question_index=1" in twiml_sent
        assert "correcting_index=1" in twiml_sent

    def test_unclear_first_retries(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        with patch("twilio_integration.webhook_handler.parse_question_number", return_value=None):
            resp = client.post(
                _correct_callback_url(
                    citizen_id=cid, consent_token_id=tid, service_code="pension",
                    unclear_attempt=0,
                ),
                data={"RecordingUrl": "https://api.twilio.com/rec/RECORR3", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "/twilio/voice/service/correct" in twiml_sent
        assert "unclear_attempt=1" in twiml_sent

    def test_unclear_second_gives_up(self, consented_citizen):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        with patch("twilio_integration.webhook_handler.parse_question_number", return_value=None):
            resp = client.post(
                _correct_callback_url(
                    citizen_id=cid, consent_token_id=tid, service_code="pension",
                    unclear_attempt=1,
                ),
                data={"RecordingUrl": "https://api.twilio.com/rec/RECORR4", "CallSid": CALL_SID},
            )
        assert resp.status_code == 200
        assert mock_update_call.call_count >= 1
        twiml_sent = mock_update_call.call_args_list[-1][0][1]
        assert "Hangup" in twiml_sent


# ===========================================================================
# TestAllServices
# ===========================================================================


class TestAllServices:
    """Parametrized tests across all 5 service catalog entries."""

    @pytest.mark.parametrize(
        "menu_key,expected_code",
        [
            ("1", "pension"),
            ("2", "mpesa_transfer"),
            ("3", "aid_verification"),
            ("4", "sim_swap"),
            ("5", "telemedicine"),
        ],
    )
    def test_menu_key_maps_to_correct_service_code(self, consented_citizen, menu_key, expected_code):
        client, mock_redis, mock_update_call, cid, nid, tid = consented_citizen
        resp = client.post(
            _menu_url(lang="en", citizen_id=cid, consent_token_id=tid),
            data={"Digits": menu_key, "CallSid": CALL_SID},
        )
        assert resp.status_code == 200
        root = parse_twiml(resp.text)
        redir = _find_redirect(root)
        assert redir is not None, f"No redirect for menu_key={menu_key}"
        assert f"service_code={expected_code}" in redir
