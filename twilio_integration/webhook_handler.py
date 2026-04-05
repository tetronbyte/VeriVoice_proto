"""Twilio Voice webhook endpoints — returns TwiML XML responses (PRD Section 10.2).

State is passed via query parameters to keep the server stateless.
Twilio request validation is enforced when TWILIO_AUTH_TOKEN is configured.

TTS strategy:
  - English prompts → Twilio <Say voice="alice"> (rendered inline, zero latency)
  - Swahili prompts → gTTS MP3 → served via /tts-audio/ → Twilio <Play>
    (Google TTS has proper Swahili pronunciation; Twilio alice does not)
"""

import asyncio
import io
import json
import logging
import tempfile
import os
import uuid

import httpx
import numpy as np
import redis
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from datetime import datetime, timezone

from app.config import settings
from app.db.crud import (
    create_auth_event,
    create_citizen,
    create_consent_token,
    create_service_form,
    create_voice_template,
    get_active_template,
    get_citizen_by_id,
    get_citizen_by_national_id,
    get_consent_token,
)
from app.db.database import get_db, SessionLocal
from app.services.audio_preprocessor import AudioPreprocessor
from app.services.challenge_service import ChallengeService
from app.services.confirmation_service import classify_yes_no
from app.services.consent_service import ConsentService
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
_consent_service = ConsentService()
_redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

# In-memory store for enrollment recording URLs (keyed by session_id)
# Used instead of Redis when Redis is unavailable (Windows dev)
_enroll_recordings: dict[str, list[str]] = {}

# ── Twilio call update helper ────────────────────────────────────────────────

async def _update_call_twiml(call_sid: str, twiml: str) -> None:
    """Redirect an in-progress Twilio call to new TwiML via the REST API.

    Gracefully handles the case where the call has already ended (404/status
    completed) by logging and returning — callers catch their own exceptions.
    """
    url = (f"https://api.twilio.com/2010-04-01/Accounts/"
           f"{settings.TWILIO_ACCOUNT_SID}/Calls/{call_sid}.json")
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient(auth=auth) as client:
        resp = await client.post(url, data={"Twiml": twiml}, timeout=10.0)
        if resp.status_code == 404:
            print(f"[TWILIO] Call {call_sid} already ended (404) — skipping update", flush=True)
            return
        resp.raise_for_status()
    print(f"[TWILIO] Updated call {call_sid} with new TwiML", flush=True)


# ── Background job tracking ─────────────────────────────────────────────────
# Maps job_id → {"status": "processing"|"done"|"error", "result": {...}}
_background_jobs: dict[str, dict] = {}


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
        msg = "Umechagua Kiswahili. Bonyeza 1 kusajili. Bonyeza 2 kuthibitisha. Bonyeza 3 kuthibitisha kitambulisho."
    else:
        msg = "You selected English. Press 1 to enroll. Press 2 to authenticate. Press 3 to verify your identity."

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
        "3": "VERIFY_IDENTITY",
    }.get(Digits, "INVALID")
    print(f"[IVR ACTION] <<< User pressed: {Digits} → Routing to {action} (lang={lang})", flush=True)
    response = VoiceResponse()
    if Digits == "1":
        print("[IVR ACTION] >>> Redirecting to ENROLLMENT flow", flush=True)
        response.redirect(f"/twilio/voice/enroll?lang={lang}&step=0", method="POST")
    elif Digits == "2":
        print("[IVR ACTION] >>> Redirecting to AUTHENTICATION flow", flush=True)
        response.redirect(f"/twilio/voice/authenticate?lang={lang}", method="POST")
    elif Digits == "3":
        print("[IVR ACTION] >>> Redirecting to VERIFY IDENTITY flow", flush=True)
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
    else:
        print(f"[IVR ACTION] >>> Invalid selection '{Digits}', restarting welcome", flush=True)
        response.say("Invalid selection.", voice="alice")
        response.redirect("/twilio/voice/welcome", method="POST")
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# HELPER — Download Twilio recordings
# ═════════════════════════════════════════════════════════════════════════════

async def _download_twilio_recording(recording_url: str) -> bytes:
    """Download a recording from Twilio (requires Basic Auth)."""
    auth = None
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient(auth=auth) as client:
        resp = await client.get(f"{recording_url}.wav", follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        return resp.content


# ═════════════════════════════════════════════════════════════════════════════
# ENROLLMENT (5 recordings)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/voice/enroll")
async def enroll_prompt(
    request: Request,
    lang: str = Query(default="en"),
    step: int = Query(default=0),
    national_id: str = Query(default=""),
    session_id: str = Query(default=""),
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

    # Create a session ID for this enrollment if we don't have one yet
    if not session_id:
        session_id = str(uuid.uuid4())
        print(f"[IVR ENROLL] Created enrollment session: {session_id}", flush=True)

    if step > settings.ENROLLMENT_PHRASES:
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

    print(f"[IVR ENROLL step={step}/{settings.ENROLLMENT_PHRASES}] >>> Playing: '{prompt_text}' (nid={nid}, lang={lang})", flush=True)
    print(f"[IVR ENROLL step={step}] Waiting for user to speak and record...", flush=True)

    _say_or_play(response, prompt_text, lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=10,
        action=f"/twilio/voice/enroll/callback?lang={lang}&step={step}&national_id={nid}&session_id={session_id}",
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


async def _run_enrollment_pipeline(job_id: str, recording_urls: list[str],
                                    national_id: str, lang: str,
                                    call_sid: str):
    """Background task: download recordings, build voice template, create citizen.

    When done, immediately redirects the live Twilio call via REST API
    so the caller hears the result without waiting for hold audio to finish.
    """
    def _build_result_twiml(text: str, hangup: bool = True) -> str:
        r = VoiceResponse()
        _say_or_play(r, text, lang)
        if hangup:
            r.hangup()
        return str(r)

    try:
        print(f"[ENROLL BG {job_id}] Starting enrollment pipeline ({len(recording_urls)} recordings)", flush=True)

        db = SessionLocal()
        try:
            existing = get_citizen_by_national_id(db, national_id)
            if existing:
                print(f"[ENROLL BG {job_id}] ERROR: National ID {national_id} already enrolled", flush=True)
                await _update_call_twiml(call_sid, _build_result_twiml(
                    "Nambari hii ya kitambulisho tayari imesajiliwa." if lang == "sw"
                    else "This national ID is already enrolled."))
                return

            embedding_service = EmbeddingService()
            embeddings: list[np.ndarray] = []

            for i, rec_url in enumerate(recording_urls):
                print(f"[ENROLL BG {job_id}] Downloading recording {i+1}/{len(recording_urls)}...", flush=True)
                audio_bytes = await _download_twilio_recording(rec_url)
                print(f"[ENROLL BG {job_id}] Downloaded: {len(audio_bytes)} bytes", flush=True)

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name

                preprocessed = _preprocessor.process(tmp_path)
                os.unlink(tmp_path)
                print(f"[ENROLL BG {job_id}] Audio {i+1} preprocessed: {len(preprocessed)} samples ({len(preprocessed)/settings.SAMPLE_RATE:.2f}s)", flush=True)

                embedding = embedding_service.extract_embedding(preprocessed)
                print(f"[ENROLL BG {job_id}] Embedding {i+1}: dim={len(embedding)} norm={float(np.linalg.norm(embedding)):.4f}", flush=True)
                embeddings.append(embedding)

            print(f"[ENROLL BG {job_id}] Computing centroid from {len(embeddings)} embeddings", flush=True)
            centroid = embedding_service.compute_centroid(embeddings)
            print(f"[ENROLL BG {job_id}] Encrypting centroid with Paillier HE ({settings.PAILLIER_BITS}-bit)", flush=True)
            ciphertext_bytes = _encryption_service.encrypt_centroid(centroid)

            for emb in embeddings:
                emb[:] = 0.0
            embeddings.clear()

            # If the caller verified their identity via eSignet earlier in this
            # call, link the verified MOSIP individual_id to the new citizen.
            mosip_individual_id = None
            identity_verified = False
            try:
                raw = _redis_client.get(f"ivr:verified_identity:{call_sid}")
                if raw:
                    vdata = json.loads(raw)
                    if vdata.get("national_id") == national_id:
                        mosip_individual_id = vdata.get("mosip_individual_id")
                        identity_verified = True
                        print(f"[ENROLL BG {job_id}] Linking verified MOSIP id "
                              f"{mosip_individual_id} to national_id={national_id}", flush=True)
            except Exception as exc:
                print(f"[ENROLL BG {job_id}] failed to read verified identity: {exc}", flush=True)

            citizen = create_citizen(
                db,
                national_id_number=national_id,
                preferred_language=lang,
                phone_number="",
                mosip_individual_id=mosip_individual_id,
                identity_verified=identity_verified,
            )
            template = create_voice_template(db, citizen_id=citizen.citizen_id,
                                              he_ciphertext=ciphertext_bytes)
            print(f"[ENROLL BG {job_id}] DONE — citizen={citizen.citizen_id} template={template.template_id}", flush=True)

            # Instantly redirect the live call to announce success
            await _update_call_twiml(call_sid, _build_result_twiml(
                "Usajili umekamilika. Asante." if lang == "sw"
                else "Enrollment complete. Thank you."))

        finally:
            db.close()

    except Exception as e:
        print(f"[ENROLL BG {job_id}] ERROR: {e}", flush=True)
        try:
            await _update_call_twiml(call_sid, _build_result_twiml(
                "Hitilafu imetokea wakati wa kusajili. Tafadhali jaribu tena." if lang == "sw"
                else "An error occurred during enrollment. Please try again."))
        except Exception:
            pass  # Call may already be gone


@router.post("/voice/enroll/callback")
async def enroll_callback(
    request: Request,
    lang: str = Query(default="en"),
    step: int = Query(default=0),
    national_id: str = Query(default=""),
    session_id: str = Query(default=""),
    RecordingUrl: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Handle a completed enrollment recording, then advance to the next step.

    Stores recording URLs in memory (keyed by session_id).
    After the final (5th) recording, kicks off background processing and plays
    a long hold loop. The background task uses Twilio REST API to instantly
    redirect the call when processing completes.
    """
    await _validate_twilio_request(request)
    print(f"[IVR ENROLL CALLBACK step={step+1}] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'} (nid={national_id})", flush=True)
    response = VoiceResponse()

    # Store recording URL in memory
    if session_id not in _enroll_recordings:
        _enroll_recordings[session_id] = []
    if RecordingUrl:
        _enroll_recordings[session_id].append(RecordingUrl)
    recording_count = len(_enroll_recordings[session_id])

    next_step = step + 1
    if next_step <= settings.ENROLLMENT_PHRASES:
        print(f"[IVR ENROLL CALLBACK] >>> Advancing to sample {next_step}/{settings.ENROLLMENT_PHRASES} ({recording_count} URLs stored)", flush=True)
        response.redirect(
            f"/twilio/voice/enroll?lang={lang}&step={next_step}&national_id={national_id}&session_id={session_id}",
            method="POST",
        )
        return _twiml_response(response)

    # ── All 5 recordings collected — kick off background pipeline ───────
    all_recordings = _enroll_recordings.pop(session_id, [])
    print(f"[IVR ENROLL CALLBACK] All {settings.ENROLLMENT_PHRASES} samples received — {len(all_recordings)} URLs stored", flush=True)

    if len(all_recordings) < settings.ENROLLMENT_PHRASES:
        print(f"[IVR ENROLL CALLBACK] ERROR: Only {len(all_recordings)} recordings, need {settings.ENROLLMENT_PHRASES}", flush=True)
        _say_or_play(response,
                     "Hitilafu imetokea. Tafadhali jaribu tena." if lang == "sw"
                     else "An error occurred. Not enough recordings. Please try again.", lang)
        response.hangup()
        return _twiml_response(response)

    # Start background job — it will update the call via REST API when done
    job_id = str(uuid.uuid4())
    asyncio.create_task(_run_enrollment_pipeline(
        job_id, all_recordings, national_id, lang, CallSid))

    # Play long hold audio — background task will interrupt this via REST API
    _say_or_play(
        response,
        "Sauti zako zinachakatwa. Tafadhali subiri."
        if lang == "sw"
        else "Processing your voice samples. Please wait.",
        lang,
    )
    wait_msg = ("Bado inachakatwa. Tafadhali subiri."
                if lang == "sw" else "Still processing. Please hold.")
    for _ in range(30):  # ~3 minutes of hold audio (will be interrupted when done)
        response.pause(length=4)
        _say_or_play(response, wait_msg, lang)
    # Fallback if REST API update somehow fails
    _say_or_play(response,
                 "Tafadhali jaribu tena baadaye." if lang == "sw"
                 else "Please try again later.", lang)
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
                timeout=30,
            )
            _say_or_play(
                gather,
                "Tafadhali ingiza nambari yako ya kitambulisho kisha bonyeza #."
                if lang == "sw"
                else "Please enter your national ID number followed by the pound key.",
                lang,
            )
            response.append(gather)
            # No input after timeout — retry once
            _say_or_play(
                response,
                "Hukuingiza nambari yoyote." if lang == "sw" else "No number entered.",
                lang,
            )
            response.redirect(
                f"/twilio/voice/authenticate?lang={lang}&attempt={attempt}",
                method="POST",
            )
            return _twiml_response(response)

    # Step 1.5: Look up citizen. If not enrolled, send them to the enrollment flow.
    db = SessionLocal()
    try:
        citizen = get_citizen_by_national_id(db, national_id)
    finally:
        db.close()
    if citizen is None:
        print(f"[IVR AUTH] Citizen not found for national_id={national_id} — redirecting to enroll", flush=True)
        _say_or_play(
            response,
            "Nambari hiyo haijasajiliwa. Tafadhali sajili kwanza."
            if lang == "sw"
            else "That national ID is not enrolled. Please enroll first.",
            lang,
        )
        response.redirect(
            f"/twilio/voice/enroll?lang={lang}&step=0&national_id={national_id}",
            method="POST",
        )
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


async def _run_auth_pipeline(job_id: str, recording_url: str, national_id: str,
                             challenge_id: str, lang: str, attempt: int,
                             call_sid: str):
    """Background task: download recording, run dual-stage auth.

    When done, instantly redirects the live Twilio call via REST API.
    """
    def _build_twiml(text: str, hangup: bool = True) -> str:
        r = VoiceResponse()
        _say_or_play(r, text, lang)
        if hangup:
            r.hangup()
        return str(r)

    try:
        print(f"[AUTH BG {job_id}] Starting auth pipeline", flush=True)

        db = SessionLocal()
        try:
            citizen = get_citizen_by_national_id(db, national_id)
            if citizen is None:
                print(f"[AUTH BG {job_id}] ERROR: Citizen not found for national_id={national_id}", flush=True)
                await _update_call_twiml(call_sid, _build_twiml(
                    "Raia hajapatikana. Tafadhali jisajili kwanza." if lang == "sw"
                    else "Citizen not found. Please enroll first."))
                return

            template = get_active_template(db, citizen.citizen_id)
            if template is None:
                print(f"[AUTH BG {job_id}] ERROR: No active voice template", flush=True)
                await _update_call_twiml(call_sid, _build_twiml(
                    "Hakuna kiolezo cha sauti. Tafadhali jisajili kwanza." if lang == "sw"
                    else "No voice template found. Please enroll first."))
                return

            print(f"[AUTH BG {job_id}] Downloading recording from Twilio...", flush=True)
            audio_bytes = await _download_twilio_recording(recording_url)
            print(f"[AUTH BG {job_id}] Downloaded: {len(audio_bytes)} bytes", flush=True)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            preprocessed = _preprocessor.process(tmp_path)
            os.unlink(tmp_path)
            print(f"[AUTH BG {job_id}] Audio preprocessed: {len(preprocessed)} samples ({len(preprocessed)/settings.SAMPLE_RATE:.2f}s)", flush=True)

            # Stage 1: Voice Biometric Match
            print(f"[AUTH BG {job_id}] STAGE 1: Voice Biometric Match", flush=True)
            embedding_service = EmbeddingService()
            live_embedding = embedding_service.extract_embedding(preprocessed)
            print(f"[AUTH BG {job_id}] Live embedding: dim={len(live_embedding)} norm={float(np.linalg.norm(live_embedding)):.4f}", flush=True)

            encrypted_centroid = _encryption_service.deserialize_ciphertext(template.he_ciphertext)
            match_result = _matching_service.match(
                live_embedding, encrypted_centroid, _encryption_service.private_key
            )
            voice_score = match_result["score"]
            voice_granted = match_result["granted"]
            print(f"[AUTH BG {job_id}] Voice score={voice_score:.4f} threshold={settings.MATCH_THRESHOLD} → {'PASS' if voice_granted else 'FAIL'}", flush=True)

            # Stage 2: Phrase Transcript Match
            print(f"[AUTH BG {job_id}] STAGE 2: Transcript Match (lang={citizen.preferred_language})", flush=True)
            transcription_service = TranscriptionService()
            transcript = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)
            print(f"[AUTH BG {job_id}] Transcript: '{transcript}'", flush=True)

            try:
                transcript_result = _challenge_service.match_transcript(
                    challenge_id, transcript, threshold=settings.TRANSCRIPT_MATCH_THRESHOLD
                )
            except KeyError:
                print(f"[AUTH BG {job_id}] ERROR: Invalid challenge_id={challenge_id}", flush=True)
                transcript_result = {"match": False, "score": 0.0, "matched_words": 0, "total_words": 0}

            transcript_match = transcript_result["match"]
            print(f"[AUTH BG {job_id}] Transcript score={transcript_result['score']:.4f} "
                  f"({transcript_result['matched_words']}/{transcript_result['total_words']} words) "
                  f"→ {'PASS' if transcript_match else 'FAIL'}", flush=True)

            granted = voice_granted and transcript_match
            result_str = "granted" if granted else "denied"
            print(f"[AUTH BG {job_id}] DECISION: voice={'PASS' if voice_granted else 'FAIL'} "
                  f"transcript={'PASS' if transcript_match else 'FAIL'} → {result_str.upper()}", flush=True)

            create_auth_event(db, citizen_id=citizen.citizen_id,
                              voice_match_score=voice_score, result=result_str)

            # Instantly redirect the live call with result
            result_response = VoiceResponse()
            if granted:
                _say_or_play(
                    result_response,
                    f"Imethibitishwa. Alama ya sauti ni asilimia {int(voice_score * 100)}."
                    if lang == "sw"
                    else f"Access granted. Your voice score is {voice_score:.2f}.",
                    lang,
                )
                _say_or_play(
                    result_response,
                    "Huduma zinazopatikana: Fomu ya Bima ya Afya. Utaelekezwa kwenye idhini."
                    if lang == "sw"
                    else "Available services: Health Insurance Form. You will now be directed to consent.",
                    lang,
                )
                base = settings.PUBLIC_BASE_URL.rstrip("/")
                result_response.redirect(
                    f"{base}/twilio/voice/consent?lang={lang}&citizen_id={citizen.citizen_id}",
                    method="POST",
                )
            elif attempt < 1:
                _say_or_play(
                    result_response,
                    f"Imekataliwa. Alama ya sauti ni asilimia {int(voice_score * 100)}. Tafadhali jaribu tena."
                    if lang == "sw"
                    else f"Access denied. Your voice score is {voice_score:.2f}. Please try again.",
                    lang,
                )
                base = settings.PUBLIC_BASE_URL.rstrip("/")
                result_response.redirect(
                    f"{base}/twilio/voice/authenticate?lang={lang}&national_id={national_id}&attempt={attempt+1}",
                    method="POST",
                )
            else:
                _say_or_play(
                    result_response,
                    "Imekataliwa tena. Tafadhali jaribu tena baadaye. Kwaheri."
                    if lang == "sw"
                    else "Access denied again. Please try again later. Goodbye.",
                    lang,
                )
                result_response.hangup()

            await _update_call_twiml(call_sid, str(result_response))

        finally:
            db.close()

    except Exception as e:
        print(f"[AUTH BG {job_id}] ERROR: {e}", flush=True)
        try:
            await _update_call_twiml(call_sid, _build_twiml(
                "Hitilafu imetokea. Tafadhali jaribu tena." if lang == "sw"
                else "An error occurred. Please try again later."))
        except Exception:
            pass


@router.post("/voice/authenticate/callback")
async def authenticate_callback(
    request: Request,
    lang: str = Query(default="en"),
    challenge_id: str = Query(default=""),
    national_id: str = Query(default=""),
    attempt: int = Query(default=0),
    RecordingUrl: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Kick off auth pipeline in background, play hold audio.

    The background task uses Twilio REST API to instantly redirect the call
    when processing completes — no polling needed.
    """
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

    # Start background job — it will update the call via REST API when done
    job_id = str(uuid.uuid4())
    asyncio.create_task(_run_auth_pipeline(
        job_id, RecordingUrl, national_id, challenge_id, lang, attempt, CallSid
    ))

    # Play long hold audio — background task will interrupt this via REST API
    _say_or_play(
        response,
        "Sauti yako inachakatwa. Tafadhali subiri."
        if lang == "sw"
        else "Your voice is being processed. Please wait.",
        lang,
    )
    wait_msg = ("Bado inachakatwa. Tafadhali subiri."
                if lang == "sw" else "Still processing. Please hold.")
    for _ in range(30):  # ~3 minutes of hold audio (will be interrupted when done)
        response.pause(length=4)
        _say_or_play(response, wait_msg, lang)
    # Fallback if REST API update somehow fails
    _say_or_play(response,
                 "Tafadhali jaribu tena baadaye." if lang == "sw"
                 else "Please try again later.", lang)
    response.hangup()
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# CONSENT
# ═════════════════════════════════════════════════════════════════════════════

_CONSENT_MINISTRY_CODE = "MOH"
_CONSENT_DATA_SCOPE = "health_records"


@router.post("/voice/consent")
async def consent_prompt(
    request: Request,
    lang: str = Query(default="en"),
    citizen_id: str = Query(default=""),
    unclear_attempt: int = Query(default=0),
):
    """Read consent text and record the user's verbal Yes/No response.

    Validates the citizen exists before prompting. If previous attempt was
    unclear, re-prompts with a clarification message.
    """
    await _validate_twilio_request(request)
    response = VoiceResponse()

    # Session validation: bail out if citizen_id is bogus
    db = SessionLocal()
    try:
        citizen = get_citizen_by_id(db, citizen_id) if citizen_id else None
    finally:
        db.close()
    if citizen is None:
        print(f"[IVR CONSENT] ERROR: Invalid citizen_id={citizen_id}", flush=True)
        _say_or_play(response,
                     "Kikao hakipatikani. Kwaheri." if lang == "sw"
                     else "Session not found. Goodbye.", lang)
        response.hangup()
        return _twiml_response(response)

    if unclear_attempt > 0:
        clarify = ("Sikusikia vizuri. Tafadhali sema Ndiyo au Hapana."
                   if lang == "sw"
                   else "I didn't catch that. Please say Yes or No.")
        _say_or_play(response, clarify, lang)

    if lang == "sw":
        consent_text = "Ninakubali kushiriki rekodi zangu za afya na Wizara ya Afya. Sema Ndiyo kukubali au Hapana kukataa."
    else:
        consent_text = "I consent to share my health records with the Ministry of Health. Say Yes to agree or No to decline."

    print(f"[IVR CONSENT] >>> Playing: '{consent_text}' (lang={lang}, citizen_id={citizen_id}, attempt={unclear_attempt})", flush=True)
    _say_or_play(response, consent_text, lang)
    _say_or_play(
        response,
        "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
        lang,
    )
    response.record(
        max_length=10,
        action=(f"/twilio/voice/consent/callback?lang={lang}"
                f"&citizen_id={citizen_id}&unclear_attempt={unclear_attempt}"),
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


async def _run_consent_pipeline(
    recording_url: str, citizen_id: str, lang: str,
    unclear_attempt: int, call_sid: str,
):
    """Background task: transcribe consent, classify yes/no, sign+persist token.

    Updates the live Twilio call via REST API with the result:
      - yes     -> sign Ed25519 token, persist, redirect to /voice/service
      - no      -> "Consent declined" and hangup
      - unclear -> retry once, then hangup after 2 unclear attempts
    """
    base = settings.PUBLIC_BASE_URL.rstrip("/")

    def _build_twiml_with(fn) -> str:
        r = VoiceResponse()
        fn(r)
        return str(r)

    try:
        print(f"[CONSENT BG] Downloading recording...", flush=True)
        audio_bytes = await _download_twilio_recording(recording_url)
        print(f"[CONSENT BG] Downloaded: {len(audio_bytes)} bytes", flush=True)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        preprocessed = _preprocessor.process(tmp_path)
        os.unlink(tmp_path)

        transcription_service = TranscriptionService()
        transcript = transcription_service.transcribe(preprocessed, language=lang).strip()
        print(f"[CONSENT BG] Transcript: '{transcript}'", flush=True)

        intent = classify_yes_no(transcript, lang)
        print(f"[CONSENT BG] Intent: {intent}", flush=True)

        if intent == "yes":
            # Sign + persist consent token
            db = SessionLocal()
            try:
                issued_at = datetime.now(timezone.utc)
                signature = _consent_service.sign_consent(
                    citizen_id=citizen_id,
                    ministry_code=_CONSENT_MINISTRY_CODE,
                    data_scope=_CONSENT_DATA_SCOPE,
                    issued_at=issued_at.isoformat(),
                )
                token = create_consent_token(
                    db,
                    citizen_id=citizen_id,
                    ministry_code=_CONSENT_MINISTRY_CODE,
                    data_scope=_CONSENT_DATA_SCOPE,
                    digital_signature=signature,
                )
                print(f"[CONSENT BG] Signed+persisted token_id={token.token_id}", flush=True)
                token_id = str(token.token_id)
            finally:
                db.close()

            def _granted(r: VoiceResponse):
                _say_or_play(r,
                    "Idhini imerekodiwa. Utaelekezwa kwenye fomu."
                    if lang == "sw"
                    else "Consent recorded. You will now be directed to the form.",
                    lang)
                r.redirect(
                    f"{base}/twilio/voice/service?lang={lang}&question_index=0"
                    f"&citizen_id={citizen_id}&consent_token_id={token_id}",
                    method="POST",
                )
            await _update_call_twiml(call_sid, _build_twiml_with(_granted))
            return

        if intent == "no":
            def _denied(r: VoiceResponse):
                _say_or_play(r,
                    "Idhini imekataliwa. Kwaheri."
                    if lang == "sw"
                    else "Consent declined. Goodbye.",
                    lang)
                r.hangup()
            await _update_call_twiml(call_sid, _build_twiml_with(_denied))
            return

        # unclear
        if unclear_attempt < 1:
            def _retry(r: VoiceResponse):
                r.redirect(
                    f"{base}/twilio/voice/consent?lang={lang}"
                    f"&citizen_id={citizen_id}&unclear_attempt=1",
                    method="POST",
                )
            await _update_call_twiml(call_sid, _build_twiml_with(_retry))
            return

        def _give_up(r: VoiceResponse):
            _say_or_play(r,
                "Sikuweza kuelewa. Tafadhali jaribu tena baadaye. Kwaheri."
                if lang == "sw"
                else "I could not understand. Please try again later. Goodbye.",
                lang)
            r.hangup()
        await _update_call_twiml(call_sid, _build_twiml_with(_give_up))

    except Exception as e:
        print(f"[CONSENT BG] ERROR: {e}", flush=True)
        try:
            def _err(r: VoiceResponse):
                _say_or_play(r,
                    "Hitilafu imetokea. Tafadhali jaribu tena baadaye."
                    if lang == "sw"
                    else "An error occurred. Please try again later.",
                    lang)
                r.hangup()
            await _update_call_twiml(call_sid, _build_twiml_with(_err))
        except Exception as e2:
            print(f"[CONSENT BG] Unable to update call (already ended?): {e2}", flush=True)


@router.post("/voice/consent/callback")
async def consent_callback(
    request: Request,
    lang: str = Query(default="en"),
    citizen_id: str = Query(default=""),
    unclear_attempt: int = Query(default=0),
    RecordingUrl: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Kick off consent pipeline in background, play hold audio until done."""
    await _validate_twilio_request(request)
    print(f"[IVR CONSENT CALLBACK] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'} (lang={lang}, citizen_id={citizen_id})", flush=True)
    response = VoiceResponse()

    if not RecordingUrl:
        # No recording — treat as unclear, retry
        response.redirect(
            f"/twilio/voice/consent?lang={lang}&citizen_id={citizen_id}"
            f"&unclear_attempt={unclear_attempt}",
            method="POST",
        )
        return _twiml_response(response)

    asyncio.create_task(_run_consent_pipeline(
        RecordingUrl, citizen_id, lang, unclear_attempt, CallSid
    ))

    # Hold audio until background task interrupts via REST API
    _say_or_play(
        response,
        "Idhini yako inachakatwa. Tafadhali subiri."
        if lang == "sw"
        else "Processing your consent. Please hold.",
        lang,
    )
    wait_msg = ("Tafadhali subiri." if lang == "sw" else "Please hold.")
    for _ in range(40):  # ~3 min hold until background pipeline interrupts
        response.pause(length=3)
        _say_or_play(response, wait_msg, lang)
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
    citizen_id: str = Query(default=""),
    consent_token_id: str = Query(default=""),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    correction_attempts: int = Query(default=0),
    unclear_attempt: int = Query(default=0),
):
    """Play a health insurance form question and record the answer.

    Requires a valid consent_token_id (gates the entire service flow).
    """
    await _validate_twilio_request(request)
    response = VoiceResponse()

    # Gate: verify consent token is valid before serving any form questions
    db = SessionLocal()
    try:
        if not consent_token_id:
            print("[IVR SERVICE] ERROR: no consent_token_id in URL", flush=True)
            _say_or_play(response,
                "Hakuna idhini halali. Kwaheri." if lang == "sw"
                else "No valid consent on file. Goodbye.", lang)
            response.hangup()
            return _twiml_response(response)
        token = get_consent_token(db, consent_token_id)
        if token is None or token.is_revoked or str(token.citizen_id) != str(citizen_id):
            print(f"[IVR SERVICE] ERROR: invalid/revoked consent token {consent_token_id}", flush=True)
            _say_or_play(response,
                "Idhini si halali. Kwaheri." if lang == "sw"
                else "Consent is not valid. Goodbye.", lang)
            response.hangup()
            return _twiml_response(response)
    finally:
        db.close()

    q_list = _SERVICE_QUESTIONS.get(lang, _SERVICE_QUESTIONS["en"])

    if question_index >= len(q_list):
        # All 3 questions answered — play TTS read-back summary, record yes/no
        print(f"[IVR SERVICE SUMMARY] All 3 questions answered. name='{full_name}', dependants='{dependants}', facility='{primary_facility}' (correction={correction_attempts}, unclear={unclear_attempt})", flush=True)
        if unclear_attempt > 0:
            clarify = ("Sikusikia vizuri. Tafadhali sema Ndiyo au Hapana."
                       if lang == "sw"
                       else "I didn't catch that. Please say Yes or No.")
            _say_or_play(response, clarify, lang)

        if lang == "sw":
            summary = (
                f"Asante. Nimekusanya: jina lako ni {full_name}, "
                f"una wategemezi {dependants}, "
                f"na kituo chako kikuu ni {primary_facility}. "
                f"Je, hii ni sahihi? Sema Ndiyo au Hapana."
            )
        else:
            summary = (
                f"Thank you. I have recorded: your name is {full_name}, "
                f"you have {dependants} dependants, "
                f"and your preferred facility is {primary_facility}. "
                f"Is this correct? Say Yes or No."
            )

        print(f"[IVR SERVICE SUMMARY] >>> Playing read-back: '{summary}'", flush=True)
        _say_or_play(response, summary, lang)
        _say_or_play(
            response,
            "Bonyeza # ukimaliza." if lang == "sw" else "Press pound when you are done.",
            lang,
        )
        from urllib.parse import quote
        response.record(
            max_length=5,
            action=(
                f"/twilio/voice/service/confirm?lang={lang}&citizen_id={citizen_id}"
                f"&consent_token_id={consent_token_id}"
                f"&full_name={quote(full_name)}&dependants={quote(dependants)}"
                f"&primary_facility={quote(primary_facility)}"
                f"&correction_attempts={correction_attempts}"
                f"&unclear_attempt={unclear_attempt}"
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
    from urllib.parse import quote
    response.record(
        max_length=15,
        action=(
            f"/twilio/voice/service/callback?lang={lang}"
            f"&question_index={question_index}&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}"
            f"&full_name={quote(full_name)}&dependants={quote(dependants)}"
            f"&primary_facility={quote(primary_facility)}"
            f"&correction_attempts={correction_attempts}"
        ),
        method="POST",
        play_beep=True,
        trim="trim-silence",
        timeout=0,
        finish_on_key="#",
    )
    return _twiml_response(response)


async def _run_service_transcription(
    recording_url: str, question_index: int, citizen_id: str, consent_token_id: str,
    lang: str, full_name: str, dependants: str, primary_facility: str,
    correction_attempts: int, call_sid: str,
):
    """Background task: transcribe service answer and redirect call to next question."""
    from urllib.parse import quote
    try:
        print(f"[SERVICE BG Q{question_index+1}] Downloading recording...", flush=True)
        audio_bytes = await _download_twilio_recording(recording_url)
        print(f"[SERVICE BG Q{question_index+1}] Downloaded: {len(audio_bytes)} bytes", flush=True)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        preprocessed = _preprocessor.process(tmp_path)
        os.unlink(tmp_path)

        transcription_service = TranscriptionService()
        answer = transcription_service.transcribe(preprocessed, language=lang).strip()
        print(f"[SERVICE BG Q{question_index+1}] Transcribed: '{answer}'", flush=True)

    except Exception as e:
        print(f"[SERVICE BG Q{question_index+1}] ERROR: {e}", flush=True)
        answer = "(unclear)"

    if question_index == 0:
        full_name = answer
    elif question_index == 1:
        dependants = answer
    elif question_index == 2:
        primary_facility = answer

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    next_q = question_index + 1
    r = VoiceResponse()
    r.redirect(
        f"{base}/twilio/voice/service?lang={lang}&question_index={next_q}&citizen_id={citizen_id}"
        f"&consent_token_id={consent_token_id}"
        f"&full_name={quote(full_name)}&dependants={quote(dependants)}"
        f"&primary_facility={quote(primary_facility)}"
        f"&correction_attempts={correction_attempts}",
        method="POST",
    )
    try:
        await _update_call_twiml(call_sid, str(r))
    except Exception as e:
        print(f"[SERVICE BG Q{question_index+1}] Unable to update call (already ended?): {e}", flush=True)


@router.post("/voice/service/callback")
async def service_callback(
    request: Request,
    lang: str = Query(default="en"),
    question_index: int = Query(default=0),
    citizen_id: str = Query(default=""),
    consent_token_id: str = Query(default=""),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    correction_attempts: int = Query(default=0),
    RecordingUrl: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Kick off transcription in background, play hold audio until done."""
    await _validate_twilio_request(request)
    print(f"[IVR SERVICE CALLBACK Q{question_index+1}] <<< Recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'}", flush=True)
    response = VoiceResponse()

    if not RecordingUrl:
        from urllib.parse import quote
        next_q = question_index + 1
        response.redirect(
            f"/twilio/voice/service?lang={lang}&question_index={next_q}&citizen_id={citizen_id}"
            f"&consent_token_id={consent_token_id}"
            f"&full_name={quote(full_name)}&dependants={quote(dependants)}"
            f"&primary_facility={quote(primary_facility)}"
            f"&correction_attempts={correction_attempts}",
            method="POST",
        )
        return _twiml_response(response)

    asyncio.create_task(_run_service_transcription(
        RecordingUrl, question_index, citizen_id, consent_token_id, lang,
        full_name, dependants, primary_facility, correction_attempts, CallSid,
    ))

    _say_or_play(
        response,
        "Jibu lako linachakatwa." if lang == "sw" else "Processing your answer.",
        lang,
    )
    wait_msg = ("Tafadhali subiri." if lang == "sw" else "Please hold.")
    for _ in range(40):  # ~3 min hold until background pipeline interrupts
        response.pause(length=3)
        _say_or_play(response, wait_msg, lang)
    response.hangup()
    return _twiml_response(response)


def _parse_dependants_int(raw: str) -> int:
    """Best-effort parse of the spoken dependants answer to an int.

    Handles numeric digits in the transcript, plus English number-words zero..ten.
    Falls back to 0 if nothing parseable is found.
    """
    import re
    if not raw:
        return 0
    # Try digits first
    m = re.search(r"\d+", raw)
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    # Fall back to number words (English + a couple of Swahili)
    words = {
        "zero": 0, "none": 0, "no": 0,
        "one": 1, "moja": 1,
        "two": 2, "mbili": 2,
        "three": 3, "tatu": 3,
        "four": 4, "nne": 4,
        "five": 5, "tano": 5,
        "six": 6, "sita": 6,
        "seven": 7, "saba": 7,
        "eight": 8, "nane": 8,
        "nine": 9, "tisa": 9,
        "ten": 10, "kumi": 10,
    }
    for tok in raw.lower().split():
        tok = "".join(c for c in tok if c.isalpha())
        if tok in words:
            return words[tok]
    return 0


async def _run_service_confirm_pipeline(
    recording_url: str, citizen_id: str, consent_token_id: str, lang: str,
    full_name: str, dependants: str, primary_facility: str,
    correction_attempts: int, unclear_attempt: int, call_sid: str,
):
    """Background task: classify yes/no on read-back, persist or restart form."""
    from urllib.parse import quote
    base = settings.PUBLIC_BASE_URL.rstrip("/")

    def _build(fn) -> str:
        r = VoiceResponse()
        fn(r)
        return str(r)

    try:
        print(f"[SERVICE CONFIRM BG] Downloading recording...", flush=True)
        audio_bytes = await _download_twilio_recording(recording_url)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        preprocessed = _preprocessor.process(tmp_path)
        os.unlink(tmp_path)

        transcription_service = TranscriptionService()
        transcript = transcription_service.transcribe(preprocessed, language=lang).strip()
        print(f"[SERVICE CONFIRM BG] Transcript: '{transcript}'", flush=True)
        intent = classify_yes_no(transcript, lang)
        print(f"[SERVICE CONFIRM BG] Intent: {intent}", flush=True)

        if intent == "yes":
            # Persist the form
            db = SessionLocal()
            try:
                deps_int = _parse_dependants_int(dependants)
                form = create_service_form(
                    db,
                    citizen_id=citizen_id,
                    consent_token_id=consent_token_id,
                    ministry_code=_CONSENT_MINISTRY_CODE,
                    form_type="health_insurance",
                    full_name=full_name,
                    dependants=deps_int,
                    primary_facility=primary_facility,
                )
                ref_id = str(form.form_id)[-8:].upper()
                print(f"[SERVICE CONFIRM BG] Persisted form_id={form.form_id} ref={ref_id}", flush=True)
            finally:
                db.close()

            def _done(r: VoiceResponse):
                if lang == "sw":
                    msg = (f"Fomu imewasilishwa. Nambari yako ya rejeleo ni {ref_id}. "
                           f"Asante kwa kutumia VeriVoice.")
                else:
                    msg = (f"Form submitted successfully. Your reference ID is {ref_id}. "
                           f"Thank you for using VeriVoice.")
                _say_or_play(r, msg, lang)
                r.hangup()
            await _update_call_twiml(call_sid, _build(_done))
            return

        if intent == "no":
            # Restart the form (bump correction counter, cap at 2)
            if correction_attempts >= 2:
                def _give_up(r: VoiceResponse):
                    _say_or_play(r,
                        "Imeshindikana kukamilisha fomu. Tafadhali jaribu tena baadaye. Kwaheri."
                        if lang == "sw"
                        else "Unable to complete the form at this time. Please try again later. Goodbye.",
                        lang)
                    r.hangup()
                await _update_call_twiml(call_sid, _build(_give_up))
                return

            new_attempts = correction_attempts + 1
            def _restart(r: VoiceResponse):
                _say_or_play(r,
                    "Sawa, hebu tuanze upya fomu."
                    if lang == "sw"
                    else "Okay, let us restart the form.",
                    lang)
                r.redirect(
                    f"{base}/twilio/voice/service?lang={lang}&question_index=0"
                    f"&citizen_id={citizen_id}&consent_token_id={consent_token_id}"
                    f"&correction_attempts={new_attempts}",
                    method="POST",
                )
            await _update_call_twiml(call_sid, _build(_restart))
            return

        # unclear
        if unclear_attempt < 1:
            def _retry(r: VoiceResponse):
                # Jump back to the summary step (question_index == len(questions))
                r.redirect(
                    f"{base}/twilio/voice/service?lang={lang}&question_index=3"
                    f"&citizen_id={citizen_id}&consent_token_id={consent_token_id}"
                    f"&full_name={quote(full_name)}&dependants={quote(dependants)}"
                    f"&primary_facility={quote(primary_facility)}"
                    f"&correction_attempts={correction_attempts}&unclear_attempt=1",
                    method="POST",
                )
            await _update_call_twiml(call_sid, _build(_retry))
            return

        def _give_up(r: VoiceResponse):
            _say_or_play(r,
                "Sikuweza kuthibitisha. Tafadhali jaribu tena baadaye. Kwaheri."
                if lang == "sw"
                else "Unable to confirm. Please try again later. Goodbye.",
                lang)
            r.hangup()
        await _update_call_twiml(call_sid, _build(_give_up))

    except Exception as e:
        print(f"[SERVICE CONFIRM BG] ERROR: {e}", flush=True)
        try:
            def _err(r: VoiceResponse):
                _say_or_play(r,
                    "Hitilafu imetokea. Tafadhali jaribu tena baadaye."
                    if lang == "sw"
                    else "An error occurred. Please try again later.",
                    lang)
                r.hangup()
            await _update_call_twiml(call_sid, _build(_err))
        except Exception as e2:
            print(f"[SERVICE CONFIRM BG] Unable to update call (already ended?): {e2}", flush=True)


@router.post("/voice/service/confirm")
async def service_confirm(
    request: Request,
    lang: str = Query(default="en"),
    citizen_id: str = Query(default=""),
    consent_token_id: str = Query(default=""),
    full_name: str = Query(default=""),
    dependants: str = Query(default=""),
    primary_facility: str = Query(default=""),
    correction_attempts: int = Query(default=0),
    unclear_attempt: int = Query(default=0),
    RecordingUrl: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Handle the yes/no confirmation after the TTS read-back summary.

    Runs ASR + classification in background, then persists on "yes" or
    restarts the form on "no" (capped at 2 corrections).
    """
    await _validate_twilio_request(request)
    print(f"[IVR SERVICE CONFIRM] <<< Confirmation recording received: {RecordingUrl[:80] if RecordingUrl else '(none)'}", flush=True)
    print(f"[IVR SERVICE CONFIRM] Final answers: name='{full_name}', dependants='{dependants}', facility='{primary_facility}' (correction={correction_attempts}, unclear={unclear_attempt})", flush=True)
    response = VoiceResponse()

    if not RecordingUrl:
        # No recording — treat as unclear, redirect to summary retry
        response.redirect(
            f"/twilio/voice/service?lang={lang}&question_index=3"
            f"&citizen_id={citizen_id}&consent_token_id={consent_token_id}"
            f"&full_name={full_name}&dependants={dependants}"
            f"&primary_facility={primary_facility}"
            f"&correction_attempts={correction_attempts}&unclear_attempt={unclear_attempt}",
            method="POST",
        )
        return _twiml_response(response)

    asyncio.create_task(_run_service_confirm_pipeline(
        RecordingUrl, citizen_id, consent_token_id, lang,
        full_name, dependants, primary_facility,
        correction_attempts, unclear_attempt, CallSid,
    ))

    _say_or_play(
        response,
        "Uthibitisho unachakatwa." if lang == "sw" else "Processing your confirmation.",
        lang,
    )
    wait_msg = ("Tafadhali subiri." if lang == "sw" else "Please hold.")
    for _ in range(40):  # ~3 min hold until background pipeline interrupts
        response.pause(length=3)
        _say_or_play(response, wait_msg, lang)
    response.hangup()
    return _twiml_response(response)


# ═════════════════════════════════════════════════════════════════════════════
# VERIFY IDENTITY — DTMF-only eSignet OTP flow (no browser)
# ═════════════════════════════════════════════════════════════════════════════
#
# Flow:
#   1. /voice/verify/start       — prompt for national ID via DTMF
#   2. /voice/verify/nid         — receive NID, call eSignet to send OTP,
#                                  store session in Redis keyed by CallSid
#   3. /voice/verify/otp         — receive OTP, verify with eSignet,
#                                  on success redirect to /voice/enroll
#                                  with the verified national_id pre-filled.

from app.services.mosip_service import MosipService  # noqa: E402

_mosip_service = MosipService()

_VERIFY_SESSION_TTL = 600  # 10 min


def _verify_session_key(call_sid: str) -> str:
    return f"ivr:verify_session:{call_sid}"


@router.post("/voice/verify/start")
async def verify_start(
    request: Request,
    lang: str = Query(default="en"),
    CallSid: str = Form(default=""),
):
    """Prompt the caller to enter their national ID via DTMF."""
    await _validate_twilio_request(request)
    print(f"[IVR VERIFY START] >>> call={CallSid} lang={lang}", flush=True)

    response = VoiceResponse()
    gather = Gather(
        action=f"/twilio/voice/verify/nid?lang={lang}",
        method="POST",
        finish_on_key="#",
        timeout=10,
    )
    prompt = (
        "Tafadhali ingiza nambari yako ya kitambulisho kisha bonyeza #."
        if lang == "sw"
        else "Please enter your national identity number followed by the pound key."
    )
    _say_or_play(gather, prompt, lang)
    response.append(gather)
    # If nothing entered, loop back
    response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
    return _twiml_response(response)


@router.post("/voice/verify/nid")
async def verify_nid(
    request: Request,
    lang: str = Query(default="en"),
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """Kick off eSignet OTP flow for the entered national ID, then prompt for OTP."""
    await _validate_twilio_request(request)
    national_id = Digits.strip()
    print(f"[IVR VERIFY NID] <<< call={CallSid} national_id={national_id}", flush=True)

    response = VoiceResponse()
    if not national_id:
        _say_or_play(
            response,
            "Hukuingiza nambari yoyote." if lang == "sw" else "No number entered.",
            lang,
        )
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
        return _twiml_response(response)

    # Call eSignet to send the OTP
    try:
        session = await _mosip_service.start_otp_auth(national_id)
    except Exception as exc:
        print(f"[IVR VERIFY NID] eSignet start_otp_auth failed: {exc}", flush=True)
        _say_or_play(
            response,
            "Nambari ya kitambulisho haijatambuliwa. Tafadhali jaribu tena." if lang == "sw"
            else "That identity could not be verified. Please try again.",
            lang,
        )
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
        return _twiml_response(response)

    # Persist the eSignet session keyed by CallSid so the OTP step can finish it
    _redis_client.setex(
        _verify_session_key(CallSid),
        _VERIFY_SESSION_TTL,
        json.dumps({"national_id": national_id, "session": session}),
    )
    print(f"[IVR VERIFY NID] OTP sent, transaction_id={session['transaction_id']}", flush=True)

    # Prompt for OTP
    gather = Gather(
        action=f"/twilio/voice/verify/otp?lang={lang}",
        method="POST",
        finish_on_key="#",
        timeout=30,
        num_digits=6,
    )
    _say_or_play(
        gather,
        "OTP imetumwa. Tafadhali ingiza OTP yenye tarakimu sita kisha bonyeza #."
        if lang == "sw"
        else "An OTP has been sent. Please enter the six-digit OTP followed by the pound key.",
        lang,
    )
    response.append(gather)
    # Timeout fallback
    _say_or_play(
        response,
        "Hukuingiza OTP. Tafadhali piga simu tena." if lang == "sw"
        else "No OTP entered. Please call again.",
        lang,
    )
    response.hangup()
    return _twiml_response(response)


@router.post("/voice/verify/otp")
async def verify_otp(
    request: Request,
    lang: str = Query(default="en"),
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """Verify OTP with eSignet, then redirect into the voice enrollment flow."""
    await _validate_twilio_request(request)
    otp = Digits.strip()
    print(f"[IVR VERIFY OTP] <<< call={CallSid} otp_len={len(otp)}", flush=True)

    response = VoiceResponse()

    raw = _redis_client.get(_verify_session_key(CallSid))
    if not raw:
        _say_or_play(
            response,
            "Kipindi kimeisha. Tafadhali anza upya." if lang == "sw"
            else "Session expired. Please start again.",
            lang,
        )
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
        return _twiml_response(response)
    data = json.loads(raw)
    national_id = data["national_id"]
    session = data["session"]

    if not otp:
        _say_or_play(
            response,
            "Hukuingiza OTP." if lang == "sw" else "No OTP entered.",
            lang,
        )
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
        return _twiml_response(response)

    try:
        individual_id = await _mosip_service.verify_otp_and_get_identity(
            session, national_id, otp
        )
    except Exception as exc:
        print(f"[IVR VERIFY OTP] verification failed: {exc}", flush=True)
        _say_or_play(
            response,
            "OTP si sahihi. Tafadhali jaribu tena." if lang == "sw"
            else "OTP is incorrect. Please try again.",
            lang,
        )
        response.redirect(f"/twilio/voice/verify/start?lang={lang}", method="POST")
        return _twiml_response(response)

    print(f"[IVR VERIFY OTP] >>> Verified! individual_id={individual_id}", flush=True)

    # Record the verification so enrollment can link it to the voice template
    _redis_client.setex(
        f"ivr:verified_identity:{CallSid}",
        _VERIFY_SESSION_TTL,
        json.dumps({"national_id": national_id, "mosip_individual_id": individual_id}),
    )
    # Cleanup session
    _redis_client.delete(_verify_session_key(CallSid))

    _say_or_play(
        response,
        "Kitambulisho chako kimethibitishwa. Sasa tutasajili sauti yako."
        if lang == "sw"
        else "Your identity has been verified. Now we will enroll your voice.",
        lang,
    )
    # Jump into the enrollment flow with the verified national_id pre-filled
    response.redirect(
        f"/twilio/voice/enroll?lang={lang}&step=0&national_id={national_id}",
        method="POST",
    )
    return _twiml_response(response)
