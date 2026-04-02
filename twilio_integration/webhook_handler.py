"""Twilio Voice webhook endpoints — returns TwiML XML responses (PRD Section 10.2).

State is passed via query parameters to keep the server stateless.
Twilio request validation is enforced when TWILIO_AUTH_TOKEN is configured.

TTS strategy:
  - English prompts → Twilio <Say voice="alice"> (rendered inline, zero latency)
  - Swahili prompts → gTTS MP3 → served via /tts-audio/ → Twilio <Play>
    (Google TTS has proper Swahili pronunciation; Twilio alice does not)
"""

import io
import logging
import tempfile
import os

import httpx
import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.config import settings
from app.db.crud import (
    create_auth_event,
    create_citizen,
    create_voice_template,
    get_active_template,
    get_citizen_by_national_id,
)
from app.db.database import get_db
from app.services.audio_preprocessor import AudioPreprocessor
from app.services.challenge_service import ChallengeService
from app.services.embedding_service import EmbeddingService
from app.services.encryption_service import EncryptionService
from app.services.matching_service import MatchingService
from app.services.transcription_service import TranscriptionService
from app.services.tts_service import TTSService
from twilio_integration.ivr_flow import IVRState, pick_random_enrollment_phrase

logger = logging.getLogger("verivoice.ivr")

router = APIRouter()

_preprocessor = AudioPreprocessor()
_encryption_service = EncryptionService()
_matching_service = MatchingService()
_challenge_service = ChallengeService()
_tts_service = TTSService()


def _twiml_response(response: VoiceResponse) -> Response:
    """Return a TwiML XML response with correct content type."""
    return Response(content=str(response), media_type="application/xml")


def _say_or_play(parent, text: str, lang: str) -> None:
    """Speak a prompt via the best TTS for the language.

    - English: Twilio <Say voice="alice"> (inline, zero latency)
    - Swahili: gTTS → MP3 file → <Play url> (proper Swahili pronunciation)

    Works with both VoiceResponse and Gather (both expose .say() and .play()).
    """
    if lang == "sw":
        url = _tts_service.synthesize_with_url(text, language="sw")
        logger.debug("[IVR] <Play> Swahili: '%s...' → %s", text[:50], url)
        parent.play(url)
    else:
        parent.say(text, voice="alice", language="en-US")


async def _validate_twilio_request(request: Request) -> None:
    """Validate that the request originated from Twilio (if auth token is configured)."""
    if not settings.TWILIO_AUTH_TOKEN:
        logger.debug("[TWILIO-VALIDATE] Auth token not set — skipping signature validation")
        return  # Skip validation in development
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    form = await request.form()
    url = str(request.url)
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(url, dict(form), signature):
        logger.warning("[TWILIO-VALIDATE] FAILED — invalid Twilio signature for %s", url)
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    logger.debug("[TWILIO-VALIDATE] OK — valid signature for %s", url)


# ═════════════════════════════════════════════════════════════════════════════
# WELCOME
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/voice/welcome")
async def welcome(request: Request):
    """Play welcome message and prompt for language selection via DTMF."""
    logger.info("[IVR] ── WELCOME ── Playing language selection prompt")
    await _validate_twilio_request(request)
    response = VoiceResponse()
    gather = Gather(
        num_digits=1,
        action="/twilio/voice/welcome/language",
        method="POST",
        timeout=5,
    )
    # Welcome is always in English — caller hasn't chosen a language yet
    gather.say(
        "Welcome to VeriVoice. Press 1 for English. Press 2 for Swahili.",
        voice="alice",
        language="en-US",
    )
    response.append(gather)
    response.say("No input received. Defaulting to English.", voice="alice")
    response.redirect("/twilio/voice/welcome/language?Digits=1", method="POST")
    return _twiml_response(response)


@router.post("/voice/welcome/language")
async def welcome_language(
    request: Request,
    Digits: str = Form(default="1"),
):
    """Handle language selection and prompt for action."""
    await _validate_twilio_request(request)
    lang = "sw" if Digits == "2" else "en"
    logger.info("[IVR] ── LANGUAGE ── Selected: %s (Digits=%s)", lang, Digits)
    response = VoiceResponse()

    if lang == "sw":
        msg = "Umechagua Kiswahili. Bonyeza 1 kusajili. Bonyeza 2 kuthibitisha."
    else:
        msg = "You selected English. Press 1 to enroll. Press 2 to authenticate."

    gather = Gather(
        num_digits=1,
        action=f"/twilio/voice/welcome/action?lang={lang}",
        method="POST",
        timeout=5,
    )
    _say_or_play(gather, msg, lang)
    response.append(gather)
    return _twiml_response(response)


@router.post("/voice/welcome/action")
async def welcome_action(
    request: Request,
    lang: str = Query(default="en"),
    Digits: str = Form(default="1"),
):
    """Route to enroll or authenticate based on DTMF input."""
    await _validate_twilio_request(request)
    action = {
        "1": "ENROLL",
        "2": "AUTHENTICATE",
    }.get(Digits, "INVALID")
    logger.info("[IVR] ── ACTION ── Routing to %s (lang=%s, Digits=%s)", action, lang, Digits)
    response = VoiceResponse()
    if Digits == "1":
        response.redirect(f"/twilio/voice/enroll?lang={lang}&step=0", method="POST")
    elif Digits == "2":
        response.redirect(f"/twilio/voice/authenticate?lang={lang}", method="POST")
    else:
        logger.warning("[IVR] ── ACTION ── Invalid selection '%s', restarting welcome", Digits)
        response.say("Invalid selection.", voice="alice")
        response.redirect("/twilio/voice/welcome", method="POST")
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# ENROLLMENT (5 recordings)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/voice/enroll")
async def enroll_prompt(
    request: Request,
    lang: str = Query(default="en"),
    step: int = Query(default=0),
    national_id: str = Query(default=""),
):
    """Prompt user for national ID (step 0) or play the next recording prompt."""
    await _validate_twilio_request(request)
    response = VoiceResponse()

    if step == 0 and not national_id:
        # First step: gather national ID via DTMF
        logger.info("[IVR] ── ENROLL step=0 ── Prompting for national ID (lang=%s)", lang)
        gather = Gather(
            action=f"/twilio/voice/enroll?lang={lang}&step=1",
            method="POST",
            finish_on_key="#",
            timeout=10,
        )
        _say_or_play(
            gather,
            "Tafadhali ingiza nambari yako ya kitambulisho kisha bonyeza #."
            if lang == "sw"
            else "Please enter your national ID number followed by the pound key.",
            lang,
        )
        response.append(gather)
        return _twiml_response(response)

    # Received national_id from previous gather (in Digits form field)
    form = await request.form()
    nid = national_id or form.get("Digits", "")

    if step >= settings.ENROLLMENT_PHRASES:
        # All 5 recordings done — handled by enroll_callback
        logger.info("[IVR] ── ENROLL COMPLETE ── All %d samples recorded (nid=%s, lang=%s)",
                     settings.ENROLLMENT_PHRASES, nid, lang)
        _say_or_play(
            response,
            "Usajili umekamilika. Asante." if lang == "sw" else "Enrollment complete. Thank you.",
            lang,
        )
        response.hangup()
        return _twiml_response(response)

    # Pick a random phrase and prompt the caller to repeat it
    phrase = pick_random_enrollment_phrase(language=lang)
    prompt_text = f"Tafadhali sema: {phrase}" if lang == "sw" else f"Please say: {phrase}"

    logger.info("[IVR] ── ENROLL step=%d/%d ── Playing phrase: '%s' (nid=%s, lang=%s)",
                 step + 1, settings.ENROLLMENT_PHRASES, phrase, nid, lang)

    _say_or_play(response, prompt_text, lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=10,
        action=f"/twilio/voice/enroll/callback?lang={lang}&step={step}&national_id={nid}",
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


@router.post("/voice/enroll/callback")
async def enroll_callback(
    request: Request,
    lang: str = Query(default="en"),
    step: int = Query(default=0),
    national_id: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Handle a completed enrollment recording, then advance to the next step."""
    await _validate_twilio_request(request)
    logger.info("[IVR] ── ENROLL CALLBACK step=%d ── RecordingUrl=%s (nid=%s)",
                 step + 1, RecordingUrl[:80] if RecordingUrl else "(none)", national_id)
    response = VoiceResponse()

    # Store the recording URL in session (via query params for next step)
    next_step = step + 1
    if next_step < settings.ENROLLMENT_PHRASES:
        logger.info("[IVR] ── ENROLL CALLBACK ── Advancing to step %d/%d",
                     next_step + 1, settings.ENROLLMENT_PHRASES)
        response.redirect(
            f"/twilio/voice/enroll?lang={lang}&step={next_step}&national_id={national_id}",
            method="POST",
        )
    else:
        logger.info("[IVR] ── ENROLL CALLBACK ── All %d samples received — completing enrollment",
                     settings.ENROLLMENT_PHRASES)
        _say_or_play(
            response,
            "Usajili umekamilika. Asante."
            if lang == "sw"
            else "All five samples recorded. Enrollment complete. Thank you.",
            lang,
        )
        response.hangup()

    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# AUTHENTICATE
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/voice/authenticate")
async def authenticate_prompt(
    request: Request,
    lang: str = Query(default="en"),
):
    """Play a challenge phrase and record the user's spoken response."""
    await _validate_twilio_request(request)
    response = VoiceResponse()

    # Generate challenge phrase
    challenge = _challenge_service.generate_challenge(language=lang)
    phrase = challenge["phrase_text"]
    challenge_id = challenge["challenge_id"]
    logger.info("[IVR] ── AUTHENTICATE ── Challenge generated: id=%s phrase='%s' (lang=%s)",
                 challenge_id, phrase, lang)

    _say_or_play(
        response,
        f"Tafadhali sema: {phrase}" if lang == "sw" else f"Please say the following phrase: {phrase}",
        lang,
    )
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )

    response.record(
        max_length=10,
        action=f"/twilio/voice/authenticate/callback?lang={lang}&challenge_id={challenge_id}",
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


@router.post("/voice/authenticate/callback")
async def authenticate_callback(
    request: Request,
    lang: str = Query(default="en"),
    challenge_id: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Process the authentication recording and announce the result."""
    await _validate_twilio_request(request)
    logger.info("[IVR] ── AUTH CALLBACK ── challenge_id=%s RecordingUrl=%s",
                 challenge_id, RecordingUrl[:80] if RecordingUrl else "(none)")
    response = VoiceResponse()

    if not RecordingUrl:
        logger.warning("[IVR] ── AUTH CALLBACK ── No recording received — retrying")
        response.say("No recording received. Please try again.", voice="alice")
        response.redirect(f"/twilio/voice/authenticate?lang={lang}", method="POST")
        return _twiml_response(response)

    # In production: download RecordingUrl, run through auth pipeline.
    # For prototype, we announce that processing would happen here.
    logger.info("[IVR] ── AUTH CALLBACK ── Processing voice (prototype: placeholder)")
    _say_or_play(
        response,
        "Sauti yako inachakatwa. Tafadhali subiri."
        if lang == "sw"
        else "Your voice is being processed. Please wait.",
        lang,
    )

    logger.info("[IVR] ── AUTH CALLBACK ── Authentication complete — hanging up")
    _say_or_play(
        response,
        "Uthibitishaji umekamilika. Asante."
        if lang == "sw"
        else "Authentication complete. Thank you.",
        lang,
    )
    response.hangup()
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# CONSENT
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/voice/consent")
async def consent_prompt(
    request: Request,
    lang: str = Query(default="en"),
):
    """Read consent text and record the user's verbal affirmation."""
    await _validate_twilio_request(request)
    logger.info("[IVR] ── CONSENT ── Playing consent text (lang=%s)", lang)
    response = VoiceResponse()

    if lang == "sw":
        consent_text = "Ninakubali kushiriki rekodi zangu za afya na Wizara ya Afya. Sema Ndiyo kukubali."
    else:
        consent_text = "I consent to share my health records with the Ministry of Health. Say Yes to agree."

    _say_or_play(response, consent_text, lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=10,
        action=f"/twilio/voice/consent/callback?lang={lang}",
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


@router.post("/voice/consent/callback")
async def consent_callback(
    request: Request,
    lang: str = Query(default="en"),
    RecordingUrl: str = Form(default=""),
):
    """Process consent recording."""
    await _validate_twilio_request(request)
    logger.info("[IVR] ── CONSENT CALLBACK ── RecordingUrl=%s (lang=%s)",
                 RecordingUrl[:80] if RecordingUrl else "(none)", lang)
    response = VoiceResponse()
    _say_or_play(
        response,
        "Idhini yako imerekodiwa. Asante."
        if lang == "sw"
        else "Your consent has been recorded. Thank you.",
        lang,
    )
    logger.info("[IVR] ── CONSENT CALLBACK ── Consent recorded — hanging up")
    response.hangup()
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# SERVICE ACCESS — Health Insurance Form (3 questions + TTS read-back)
# ═════════════════════════════════════════════════════════════════════════════

_SERVICE_QUESTIONS = {
    "en": [
        "Please say your full name.",
        "How many dependants would you like to register?",
        "Which hospital or health centre would you like as your primary facility?",
    ],
    "sw": [
        "Tafadhali sema jina lako kamili.",
        "Ungependa kusajili wategemezi wangapi?",
        "Ungependa hospitali au kituo kipi cha afya kuwa kituo chako kikuu?",
    ],
}

_SERVICE_FIELD_KEYS = ["full_name", "dependants", "primary_facility"]


@router.post("/voice/service")
async def service_prompt(
    request: Request,
    lang: str = Query(default="en"),
    question_index: int = Query(default=0),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
):
    """Play a health insurance form question and record the answer."""
    await _validate_twilio_request(request)
    response = VoiceResponse()

    q_list = _SERVICE_QUESTIONS.get(lang, _SERVICE_QUESTIONS["en"])

    if question_index >= len(q_list):
        # All 3 questions answered — play TTS read-back summary
        logger.info("[IVR] ── SERVICE ── All 3 questions answered — playing read-back summary "
                     "(name=%s, deps=%s, facility=%s)", full_name, dependants, primary_facility)
        if lang == "sw":
            summary = (
                f"Asante. Nimekusanya: jina lako ni {full_name}, "
                f"una wategemezi {dependants}, "
                f"na kituo chako kikuu ni {primary_facility}. "
                f"Je, hii ni sahihi?"
            )
        else:
            summary = (
                f"Thank you. I have recorded: your name is {full_name}, "
                f"you have {dependants} dependants, "
                f"and your preferred facility is {primary_facility}. "
                f"Is this correct?"
            )

        _say_or_play(response, summary, lang)

        # Record confirmation (yes/no)
        _say_or_play(
            response,
            "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
            lang,
        )
        response.record(
            max_length=5,
            action=(
                f"/twilio/voice/service/confirm?lang={lang}"
                f"&full_name={full_name}&dependants={dependants}"
                f"&primary_facility={primary_facility}"
            ),
            method="POST",
            play_beep=True,
            trim="trim-silence",
            timeout=0,
            finish_on_key="#",
        )
        return _twiml_response(response)

    logger.info("[IVR] ── SERVICE Q%d/%d ── Playing question: '%s' (lang=%s)",
                 question_index + 1, len(q_list), q_list[question_index], lang)
    _say_or_play(response, q_list[question_index], lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=15,
        action=(
            f"/twilio/voice/service/callback?lang={lang}"
            f"&question_index={question_index}"
            f"&full_name={full_name}&dependants={dependants}"
            f"&primary_facility={primary_facility}"
        ),
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


@router.post("/voice/service/callback")
async def service_callback(
    request: Request,
    lang: str = Query(default="en"),
    question_index: int = Query(default=0),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Process a form answer recording and advance to the next question.

    In production the RecordingUrl would be downloaded, transcribed via Whisper,
    and the answer stored. For the prototype, the answer is passed via query
    params to the next step (the IVR caller hears the read-back at the end).
    """
    await _validate_twilio_request(request)
    logger.info("[IVR] ── SERVICE CALLBACK Q%d ── RecordingUrl=%s",
                 question_index + 1, RecordingUrl[:80] if RecordingUrl else "(none)")
    response = VoiceResponse()

    # In production: download RecordingUrl → Whisper → parse answer
    # For prototype: placeholder answers show the flow works end-to-end
    placeholder_answers = ["(transcribed name)", "(transcribed number)", "(transcribed facility)"]
    answer = placeholder_answers[question_index] if question_index < len(placeholder_answers) else ""

    # Store the answer in the correct field
    field_key = _SERVICE_FIELD_KEYS[question_index] if question_index < len(_SERVICE_FIELD_KEYS) else ""
    if field_key == "full_name":
        full_name = answer
    elif field_key == "dependants":
        dependants = answer
    elif field_key == "primary_facility":
        primary_facility = answer

    logger.info("[IVR] ── SERVICE CALLBACK ── field=%s answer='%s' — advancing to Q%d",
                 field_key, answer, question_index + 2)

    next_q = question_index + 1
    response.redirect(
        f"/twilio/voice/service?lang={lang}&question_index={next_q}"
        f"&full_name={full_name}&dependants={dependants}"
        f"&primary_facility={primary_facility}",
        method="POST",
    )
    return _twiml_response(response)


@router.post("/voice/service/confirm")
async def service_confirm(
    request: Request,
    lang: str = Query(default="en"),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Handle the yes/no confirmation after the TTS read-back summary."""
    await _validate_twilio_request(request)
    logger.info("[IVR] ── SERVICE CONFIRM ── RecordingUrl=%s (name=%s, deps=%s, facility=%s)",
                 RecordingUrl[:80] if RecordingUrl else "(none)", full_name, dependants, primary_facility)
    response = VoiceResponse()

    # In production: transcribe RecordingUrl, check for "yes"/"ndiyo"
    # For prototype: assume confirmed
    _say_or_play(
        response,
        "Fomu yako imekamilika. Asante kwa kutumia VeriVoice."
        if lang == "sw"
        else "Your form is complete. Thank you for using VeriVoice.",
        lang,
    )
    logger.info("[IVR] ── SERVICE CONFIRM ── Form complete — hanging up")
    response.hangup()
    return _twiml_response(response)
