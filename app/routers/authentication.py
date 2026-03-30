"""GET /api/v1/challenge + POST /api/v1/authenticate (PRD Section 10.1)."""

import tempfile
import os

import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
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
    challenge = _challenge_service.generate_challenge(language=language)
    audio_path = _tts_service.synthesize_to_wav(challenge["phrase_text"], language=language)
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
    # ── Resolve citizen ──────────────────────────────────────────────────
    citizen = None
    if citizen_id:
        citizen = get_citizen_by_id(db, citizen_id)
    elif national_id_number:
        citizen = get_citizen_by_national_id(db, national_id_number)

    if citizen is None:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # ── Retrieve stored voice template ───────────────────────────────────
    template = get_active_template(db, citizen.citizen_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No active voice template found")

    # ── Read and preprocess audio ────────────────────────────────────────
    raw_bytes = await audio_file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        preprocessed = _preprocessor.process(tmp_path)
    finally:
        os.unlink(tmp_path)

    # ── Stage 1: Voice Biometric Match (HE dot product) ─────────────────
    embedding_service = EmbeddingService()
    live_embedding = embedding_service.extract_embedding(preprocessed)

    encrypted_centroid = _encryption_service.deserialize_ciphertext(template.he_ciphertext)
    match_result = _matching_service.match(
        live_embedding, encrypted_centroid, _encryption_service.private_key
    )
    # live_embedding is zeroed by match()

    voice_score = match_result["score"]
    voice_granted = match_result["granted"]

    # ── Stage 2: Phrase Transcript Match (Whisper ASR) ───────────────────
    transcription_service = TranscriptionService()
    transcript = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)

    try:
        transcript_result = _challenge_service.match_transcript(
            challenge_phrase_id, transcript, threshold=settings.TRANSCRIPT_MATCH_THRESHOLD
        )
    except KeyError:
        raise HTTPException(status_code=400, detail="Invalid challenge_phrase_id")

    transcript_match = transcript_result["match"]

    # ── Decision: both stages must pass ──────────────────────────────────
    granted = voice_granted and transcript_match
    result = AuthResult.GRANTED if granted else AuthResult.DENIED

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
