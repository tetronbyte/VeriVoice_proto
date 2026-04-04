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
    print("[IVR WELCOME] >>> Call received. Playing: 'Welcome to VeriVoice. Press 1 for English. Press 2 for Swahili.'", flush=True)
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
    print(f"[IVR LANGUAGE] <<< User pressed: {Digits}", flush=True)
    print(f"[IVR LANGUAGE] Language selected: {'Swahili' if lang == 'sw' else 'English'}", flush=True)
    response = VoiceResponse()

    if lang == "sw":
        msg = "Umechagua Kiswahili. Bonyeza 1 kusajili. Bonyeza 2 kuthibitisha."
    else:
        msg = "You selected English. Press 1 to enroll. Press 2 to authenticate."

    print(f"[IVR LANGUAGE] >>> Playing: '{msg}'", flush=True)
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
    print(f"[IVR ACTION] <<< User pressed: {Digits} → Routing to {action} (lang={lang})", flush=True)
    response = VoiceResponse()
    if Digits == "1":
        print("[IVR ACTION] >>> Redirecting to ENROLLMENT flow", flush=True)
        response.redirect(f"/twilio/voice/enroll?lang={lang}&step=0", method="POST")
    elif Digits == "2":
        print("[IVR ACTION] >>> Redirecting to AUTHENTICATION flow", flush=True)
        response.redirect(f"/twilio/voice/authenticate?lang={lang}", method="POST")
    else:
        print(f"[IVR ACTION] >>> Invalid selection '{Digits}', restarting welcome", flush=True)
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
        print(f"[IVR ENROLL step=0] >>> Playing: 'Please enter your national ID number followed by #' (lang={lang})", flush=True)
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
    if not national_id and form.get("Digits"):
        print(f"[IVR ENROLL] <<< User entered national ID: {nid}", flush=True)

    if step >= settings.ENROLLMENT_PHRASES:
        # All 5 recordings done — handled by enroll_callback
        print(f"[IVR ENROLL COMPLETE] All {settings.ENROLLMENT_PHRASES} samples recorded for national_id={nid} (lang={lang})", flush=True)
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

    print(f"[IVR ENROLL step={step+1}/{settings.ENROLLMENT_PHRASES}] >>> Playing: '{prompt_text}' (nid={nid}, lang={lang})", flush=True)
    print(f"[IVR ENROLL step={step+1}] Waiting for user to speak and record...", flush=True)

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
    print(f"[IVR ENROLL CALLBACK step={step+1}] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'} (nid={national_id})", flush=True)
    response = VoiceResponse()

    # Store the recording URL in session (via query params for next step)
    next_step = step + 1
    if next_step < settings.ENROLLMENT_PHRASES:
        print(f"[IVR ENROLL CALLBACK] >>> Advancing to sample {next_step+1}/{settings.ENROLLMENT_PHRASES}", flush=True)
        response.redirect(
            f"/twilio/voice/enroll?lang={lang}&step={next_step}&national_id={national_id}",
            method="POST",
        )
    else:
        print(f"[IVR ENROLL CALLBACK] All {settings.ENROLLMENT_PHRASES} samples received — enrollment complete!", flush=True)
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
    national_id: str = Query(default=""),
    attempt: int = Query(default=0),
):
    """Collect national ID (if not provided), then play challenge phrase and record."""
    await _validate_twilio_request(request)
    response = VoiceResponse()

    # Step 1: Gather national ID if we don't have it yet
    if not national_id:
        form = await request.form()
        nid_from_form = form.get("Digits", "")
        if nid_from_form:
            national_id = nid_from_form
            print(f"[IVR AUTH] <<< User entered national ID: {national_id}", flush=True)
        else:
            print(f"[IVR AUTH] >>> Playing: 'Enter your national ID followed by #' (lang={lang})", flush=True)
            gather = Gather(
                action=f"/twilio/voice/authenticate?lang={lang}&attempt={attempt}",
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

    # Step 2: Generate challenge phrase and prompt user to speak it
    challenge = _challenge_service.generate_challenge(language=lang)
    phrase = challenge["phrase_text"]
    challenge_id = challenge["challenge_id"]
    print(f"[IVR AUTH] Challenge generated: id={challenge_id}", flush=True)
    print(f"[IVR AUTH] >>> Playing: 'Please say the following phrase: {phrase}' (lang={lang})", flush=True)
    print(f"[IVR AUTH] Waiting for user to speak and record... (attempt={attempt+1})", flush=True)

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
        action=(
            f"/twilio/voice/authenticate/callback?lang={lang}"
            f"&challenge_id={challenge_id}&national_id={national_id}&attempt={attempt}"
        ),
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


async def _download_twilio_recording(recording_url: str) -> bytes:
    """Download a recording from Twilio (requires Basic Auth)."""
    auth = None
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient(auth=auth) as client:
        resp = await client.get(f"{recording_url}.wav", follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        return resp.content


@router.post("/voice/authenticate/callback")
async def authenticate_callback(
    request: Request,
    lang: str = Query(default="en"),
    challenge_id: str = Query(default=""),
    national_id: str = Query(default=""),
    attempt: int = Query(default=0),
    RecordingUrl: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Download recording, run full auth pipeline, announce result."""
    await _validate_twilio_request(request)
    print(f"[IVR AUTH CALLBACK] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'}", flush=True)
    print(f"[IVR AUTH CALLBACK] national_id={national_id} challenge_id={challenge_id} attempt={attempt+1}", flush=True)
    response = VoiceResponse()

    if not RecordingUrl:
        print("[IVR AUTH CALLBACK] WARNING: No recording received — asking user to retry", flush=True)
        _say_or_play(response, "No recording received. Please try again." if lang == "en"
                     else "Hakuna rekodi iliyopokelewa. Tafadhali jaribu tena.", lang)
        response.redirect(
            f"/twilio/voice/authenticate?lang={lang}&national_id={national_id}&attempt={attempt}",
            method="POST",
        )
        return _twiml_response(response)

    # Tell user to wait
    _say_or_play(
        response,
        "Sauti yako inachakatwa. Tafadhali subiri."
        if lang == "sw"
        else "Your voice is being processed. Please wait.",
        lang,
    )

    # ── Resolve citizen ─────────────────────────────────────────────────
    citizen = get_citizen_by_national_id(db, national_id)
    if citizen is None:
        print(f"[IVR AUTH CALLBACK] ERROR: Citizen not found for national_id={national_id}", flush=True)
        _say_or_play(response, "Raia hajapatikana. Tafadhali jisajili kwanza."
                     if lang == "sw" else "Citizen not found. Please enroll first.", lang)
        response.hangup()
        return _twiml_response(response)
    print(f"[IVR AUTH CALLBACK] Citizen found: id={citizen.citizen_id} lang={citizen.preferred_language}", flush=True)

    # ── Retrieve stored voice template ──────────────────────────────────
    template = get_active_template(db, citizen.citizen_id)
    if template is None:
        print(f"[IVR AUTH CALLBACK] ERROR: No active voice template for citizen {citizen.citizen_id}", flush=True)
        _say_or_play(response, "Hakuna kiolezo cha sauti. Tafadhali jisajili kwanza."
                     if lang == "sw" else "No voice template found. Please enroll first.", lang)
        response.hangup()
        return _twiml_response(response)
    print(f"[IVR AUTH CALLBACK] Voice template loaded: {template.template_id}", flush=True)

    # ── Download and preprocess audio ───────────────────────────────────
    try:
        print(f"[IVR AUTH CALLBACK] Downloading recording from Twilio...", flush=True)
        audio_bytes = await _download_twilio_recording(RecordingUrl)
        print(f"[IVR AUTH CALLBACK] Downloaded: {len(audio_bytes)} bytes", flush=True)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        preprocessed = _preprocessor.process(tmp_path)
        os.unlink(tmp_path)
        print(f"[IVR AUTH CALLBACK] Audio preprocessed: {len(preprocessed)} samples ({len(preprocessed)/settings.SAMPLE_RATE:.2f}s)", flush=True)
    except Exception as e:
        print(f"[IVR AUTH CALLBACK] ERROR processing audio: {e}", flush=True)
        _say_or_play(response, "Hitilafu imetokea. Tafadhali jaribu tena."
                     if lang == "sw" else "An error occurred processing your audio. Please try again.", lang)
        if attempt < 1:
            response.redirect(
                f"/twilio/voice/authenticate?lang={lang}&national_id={national_id}&attempt={attempt+1}",
                method="POST",
            )
        else:
            response.hangup()
        return _twiml_response(response)

    # ── Stage 1: Voice Biometric Match ──────────────────────────────────
    print(f"[IVR AUTH CALLBACK] STAGE 1: Voice Biometric Match", flush=True)
    embedding_service = EmbeddingService()
    live_embedding = embedding_service.extract_embedding(preprocessed)
    print(f"[IVR AUTH CALLBACK] Live embedding extracted: dim={len(live_embedding)} norm={float(np.linalg.norm(live_embedding)):.4f}", flush=True)

    encrypted_centroid = _encryption_service.deserialize_ciphertext(template.he_ciphertext)
    match_result = _matching_service.match(
        live_embedding, encrypted_centroid, _encryption_service.private_key
    )
    voice_score = match_result["score"]
    voice_granted = match_result["granted"]
    print(f"[IVR AUTH CALLBACK] Voice score={voice_score:.4f} threshold={settings.MATCH_THRESHOLD} → {'PASS' if voice_granted else 'FAIL'}", flush=True)

    # ── Stage 2: Phrase Transcript Match ────────────────────────────────
    print(f"[IVR AUTH CALLBACK] STAGE 2: Transcript Match (lang={citizen.preferred_language})", flush=True)
    transcription_service = TranscriptionService()
    transcript = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)
    print(f"[IVR AUTH CALLBACK] Transcript: '{transcript}'", flush=True)

    try:
        transcript_result = _challenge_service.match_transcript(
            challenge_id, transcript, threshold=settings.TRANSCRIPT_MATCH_THRESHOLD
        )
    except KeyError:
        print(f"[IVR AUTH CALLBACK] ERROR: Invalid challenge_id={challenge_id}", flush=True)
        transcript_result = {"match": False, "score": 0.0, "matched_words": 0, "total_words": 0}

    transcript_match = transcript_result["match"]
    print(f"[IVR AUTH CALLBACK] Transcript score={transcript_result['score']:.4f} "
          f"({transcript_result['matched_words']}/{transcript_result['total_words']} words) "
          f"→ {'PASS' if transcript_match else 'FAIL'}", flush=True)

    # ── Decision ────────────────────────────────────────────────────────
    granted = voice_granted and transcript_match
    result_str = "granted" if granted else "denied"
    print(f"[IVR AUTH CALLBACK] DECISION: voice={'PASS' if voice_granted else 'FAIL'} "
          f"transcript={'PASS' if transcript_match else 'FAIL'} → {result_str.upper()}", flush=True)

    # ── Audit trail ─────────────────────────────────────────────────────
    create_auth_event(db, citizen_id=citizen.citizen_id, voice_match_score=voice_score, result=result_str)

    if granted:
        # ── ACCESS GRANTED → announce services → consent → service access ──
        print("[IVR AUTH CALLBACK] >>> ACCESS GRANTED — redirecting to service menu", flush=True)
        _say_or_play(
            response,
            f"Imethibitishwa. Alama ya sauti ni asilimia {int(voice_score * 100)}."
            if lang == "sw"
            else f"Access granted. Your voice score is {voice_score:.2f}.",
            lang,
        )
        _say_or_play(
            response,
            "Huduma zinazopatikana: Fomu ya Bima ya Afya. Utaelekezwa kwenye idhini."
            if lang == "sw"
            else "Available services: Health Insurance Form. You will now be directed to consent.",
            lang,
        )
        response.redirect(
            f"/twilio/voice/consent?lang={lang}&citizen_id={citizen.citizen_id}",
            method="POST",
        )
    elif attempt < 1:
        # ── DENIED, first attempt → allow retry ────────────────────────
        print(f"[IVR AUTH CALLBACK] >>> ACCESS DENIED (attempt {attempt+1}) — allowing retry", flush=True)
        _say_or_play(
            response,
            f"Imekataliwa. Alama ya sauti ni asilimia {int(voice_score * 100)}. Tafadhali jaribu tena."
            if lang == "sw"
            else f"Access denied. Your voice score is {voice_score:.2f}. Please try again.",
            lang,
        )
        response.redirect(
            f"/twilio/voice/authenticate?lang={lang}&national_id={national_id}&attempt={attempt+1}",
            method="POST",
        )
    else:
        # ── DENIED, second attempt → hang up ───────────────────────────
        print(f"[IVR AUTH CALLBACK] >>> ACCESS DENIED (attempt {attempt+1}) — max retries, hanging up", flush=True)
        _say_or_play(
            response,
            "Imekataliwa tena. Tafadhali jaribu tena baadaye. Kwaheri."
            if lang == "sw"
            else "Access denied again. Please try again later. Goodbye.",
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
    citizen_id: str = Query(default=""),
):
    """Read consent text and record the user's verbal affirmation."""
    await _validate_twilio_request(request)
    response = VoiceResponse()

    if lang == "sw":
        consent_text = "Ninakubali kushiriki rekodi zangu za afya na Wizara ya Afya. Sema Ndiyo kukubali."
    else:
        consent_text = "I consent to share my health records with the Ministry of Health. Say Yes to agree."

    print(f"[IVR CONSENT] >>> Playing: '{consent_text}' (lang={lang}, citizen_id={citizen_id})", flush=True)
    print(f"[IVR CONSENT] Waiting for user to say 'Yes'/'Ndiyo' and record...", flush=True)
    _say_or_play(response, consent_text, lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=10,
        action=f"/twilio/voice/consent/callback?lang={lang}&citizen_id={citizen_id}",
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
    citizen_id: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Process consent recording, then redirect to service access (health insurance form)."""
    await _validate_twilio_request(request)
    print(f"[IVR CONSENT CALLBACK] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'} (lang={lang}, citizen_id={citizen_id})", flush=True)
    response = VoiceResponse()
    print("[IVR CONSENT CALLBACK] >>> Consent recorded — redirecting to Health Insurance Form", flush=True)
    _say_or_play(
        response,
        "Idhini yako imerekodiwa. Sasa utaelekezwa kwenye fomu ya bima ya afya."
        if lang == "sw"
        else "Your consent has been recorded. You will now be directed to the Health Insurance Form.",
        lang,
    )
    response.redirect(
        f"/twilio/voice/service?lang={lang}&question_index=0&citizen_id={citizen_id}",
        method="POST",
    )
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
    citizen_id: str = Query(default=""),
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
        print(f"[IVR SERVICE SUMMARY] All 3 questions answered. Collected: name='{full_name}', dependants='{dependants}', facility='{primary_facility}'", flush=True)
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

        print(f"[IVR SERVICE SUMMARY] >>> Playing read-back: '{summary}'", flush=True)
        print(f"[IVR SERVICE SUMMARY] Waiting for user to confirm (yes/no)...", flush=True)
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
                f"/twilio/voice/service/confirm?lang={lang}&citizen_id={citizen_id}"
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

    print(f"[IVR SERVICE Q{question_index+1}/{len(q_list)}] >>> Playing: '{q_list[question_index]}' (lang={lang})", flush=True)
    print(f"[IVR SERVICE Q{question_index+1}] Waiting for user to speak and record...", flush=True)
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
            f"&question_index={question_index}&citizen_id={citizen_id}"
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
    citizen_id: str = Query(default=""),
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
    print(f"[IVR SERVICE CALLBACK Q{question_index+1}] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'}", flush=True)
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

    print(f"[IVR SERVICE CALLBACK Q{question_index+1}] Stored: {field_key}='{answer}' — advancing to Q{question_index+2}", flush=True)

    next_q = question_index + 1
    response.redirect(
        f"/twilio/voice/service?lang={lang}&question_index={next_q}&citizen_id={citizen_id}"
        f"&full_name={full_name}&dependants={dependants}"
        f"&primary_facility={primary_facility}",
        method="POST",
    )
    return _twiml_response(response)


@router.post("/voice/service/confirm")
async def service_confirm(
    request: Request,
    lang: str = Query(default="en"),
    citizen_id: str = Query(default=""),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
):
    """Handle the yes/no confirmation after the TTS read-back summary."""
    await _validate_twilio_request(request)
    print(f"[IVR SERVICE CONFIRM] <<< Confirmation recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'}", flush=True)
    print(f"[IVR SERVICE CONFIRM] Final answers: name='{full_name}', dependants='{dependants}', facility='{primary_facility}'", flush=True)
    response = VoiceResponse()

    # In production: transcribe RecordingUrl, check for "yes"/"ndiyo"
    # For prototype: assume confirmed
    print("[IVR SERVICE CONFIRM] >>> Playing: 'Your form is complete. Thank you for using VeriVoice.' — hanging up", flush=True)
    _say_or_play(
        response,
        "Fomu yako imekamilika. Asante kwa kutumia VeriVoice."
        if lang == "sw"
        else "Your form is complete. Thank you for using VeriVoice.",
        lang,
    )
    response.hangup()
    return _twiml_response(response)
