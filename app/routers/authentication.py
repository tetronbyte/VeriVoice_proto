"""GET /api/v1/challenge + POST /api/v1/authenticate (PRD Section 10.1)."""

import logging
import tempfile
import os

import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger("verivoice.auth")
from app.db.crud import (
    create_auth_event,
    get_active_template,
    get_citizen_by_id,
    get_citizen_by_national_id,
)
from app.db.database import get_db
from app.schemas.authentication import AuthenticationResponse, AuthResult, ChallengeResponse
from app.services.audio_preprocessor import AudioPreprocessor
from app.services.challenge_service import ChallengeService
from app.services.embedding_service import EmbeddingService
from app.services.encryption_service import EncryptionService
from app.services.matching_service import MatchingService
from app.services.transcription_service import TranscriptionService
from app.services.tts_service import TTSService

router = APIRouter()

_preprocessor = AudioPreprocessor()
_encryption_service = EncryptionService()
_matching_service = MatchingService()
_challenge_service = ChallengeService()
_tts_service = TTSService()


# ── GET /challenge ───────────────────────────────────────────────────────────

@router.get("/challenge", response_model=ChallengeResponse)
def get_challenge(language: str = Query(default="en")):
    """Get a random challenge phrase with TTS audio."""
    logger.info("[CHALLENGE] ── Generating challenge phrase (lang=%s)", language)
    challenge = _challenge_service.generate_challenge(language=language)
    logger.info("[CHALLENGE] ── id=%s phrase='%s'", challenge["challenge_id"], challenge["phrase_text"])
    audio_path = _tts_service.synthesize_to_wav(challenge["phrase_text"], language=language)
    logger.info("[CHALLENGE] ── TTS audio generated: %s", audio_path)
    return ChallengeResponse(
        challenge_id=challenge["challenge_id"],
        phrase_text=challenge["phrase_text"],
        audio_url=audio_path,
    )


# ── POST /authenticate ──────────────────────────────────────────────────────

@router.post("/authenticate", response_model=AuthenticationResponse)
async def authenticate(
    audio_file: UploadFile,
    challenge_phrase_id: str = Form(...),
    citizen_id: str | None = Form(default=None),
    national_id_number: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Dual-stage voice authentication: biometric match + phrase transcript match.

    Both stages must pass for the result to be 'granted'.
    Every attempt is logged as an AUTH_EVENT.
    """
    logger.info("[AUTH] ── START ── citizen_id=%s national_id=%s challenge_id=%s",
                 citizen_id or "(none)", national_id_number or "(none)", challenge_phrase_id)

    # ── Resolve citizen ──────────────────────────────────────────────────
    citizen = None
    if citizen_id:
        citizen = get_citizen_by_id(db, citizen_id)
    elif national_id_number:
        citizen = get_citizen_by_national_id(db, national_id_number)

    if citizen is None:
        logger.warning("[AUTH] ── Citizen not found")
        raise HTTPException(status_code=404, detail="Citizen not found")
    logger.info("[AUTH] ── Citizen resolved: %s (lang=%s)", citizen.citizen_id, citizen.preferred_language)

    # ── Retrieve stored voice template ───────────────────────────────────
    template = get_active_template(db, citizen.citizen_id)
    if template is None:
        logger.warning("[AUTH] ── No active voice template for citizen %s", citizen.citizen_id)
        raise HTTPException(status_code=404, detail="No active voice template found")
    logger.info("[AUTH] ── Voice template loaded: %s", template.template_id)

    # ── Read and preprocess audio ────────────────────────────────────────
    raw_bytes = await audio_file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        preprocessed = _preprocessor.process(tmp_path)
        logger.info("[AUTH] ── Audio preprocessed: %d samples (%.2fs)",
                     len(preprocessed), len(preprocessed) / settings.SAMPLE_RATE)
    finally:
        os.unlink(tmp_path)

    # ── Stage 1: Voice Biometric Match (HE dot product) ─────────────────
    logger.info("[AUTH] ── STAGE 1: Voice Biometric Match ──")
    embedding_service = EmbeddingService()
    live_embedding = embedding_service.extract_embedding(preprocessed)
    logger.info("[AUTH] ── Live embedding extracted: dim=%d norm=%.4f",
                 len(live_embedding), float(np.linalg.norm(live_embedding)))

    encrypted_centroid = _encryption_service.deserialize_ciphertext(template.he_ciphertext)
    match_result = _matching_service.match(
        live_embedding, encrypted_centroid, _encryption_service.private_key
    )
    # live_embedding is zeroed by match()

    voice_score = match_result["score"]
    voice_granted = match_result["granted"]
    logger.info("[AUTH] ── Voice score=%.4f threshold=%.2f → %s",
                 voice_score, settings.MATCH_THRESHOLD,
                 "PASS" if voice_granted else "FAIL")

    # ── Stage 2: Phrase Transcript Match ─────────────────────────────────
    logger.info("[AUTH] ── STAGE 2: Phrase Transcript Match (lang=%s, model=%s) ──",
                 citizen.preferred_language,
                 "w2v-BERT" if citizen.preferred_language == "sw" else "Whisper")
    transcription_service = TranscriptionService()
    transcript = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)
    logger.info("[AUTH] ── Transcript: '%s'", transcript)

    try:
        transcript_result = _challenge_service.match_transcript(
            challenge_phrase_id, transcript, threshold=settings.TRANSCRIPT_MATCH_THRESHOLD
        )
    except KeyError:
        logger.error("[AUTH] ── Invalid challenge_phrase_id: %s", challenge_phrase_id)
        raise HTTPException(status_code=400, detail="Invalid challenge_phrase_id")

    transcript_match = transcript_result["match"]
    logger.info("[AUTH] ── Transcript score=%.4f (%d/%d words) threshold=%.2f → %s",
                 transcript_result["score"], transcript_result["matched_words"],
                 transcript_result["total_words"], settings.TRANSCRIPT_MATCH_THRESHOLD,
                 "PASS" if transcript_match else "FAIL")

    # ── Decision: both stages must pass ──────────────────────────────────
    granted = voice_granted and transcript_match
    result = AuthResult.GRANTED if granted else AuthResult.DENIED
    logger.info("[AUTH] ── DECISION: voice=%s transcript=%s → %s",
                 "PASS" if voice_granted else "FAIL",
                 "PASS" if transcript_match else "FAIL",
                 result.value.upper())

    # ── Audit trail ──────────────────────────────────────────────────────
    event = create_auth_event(
        db,
        citizen_id=citizen.citizen_id,
        voice_match_score=voice_score,
        result=result.value,
    )

    return AuthenticationResponse(
        event_id=event.event_id,
        voice_match_score=voice_score,
        transcript_match=transcript_match,
        transcript_match_score=transcript_result["score"],
        result=result,
        event_timestamp=event.event_timestamp,
    )
