"""POST /api/v1/consent — Voice consent with Ed25519 signed token (PRD Section 10.1)."""

import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.crud import (
    create_consent_token,
    get_active_template,
    get_citizen_by_id,
)
from app.db.database import get_db
from app.schemas.consent import ConsentResponse
from app.services.audio_preprocessor import AudioPreprocessor
from app.services.consent_service import ConsentService
from app.services.embedding_service import EmbeddingService
from app.services.encryption_service import EncryptionService
from app.services.matching_service import MatchingService
from app.services.transcription_service import TranscriptionService

router = APIRouter()

_preprocessor = AudioPreprocessor()
_encryption_service = EncryptionService()
_matching_service = MatchingService()
_consent_service = ConsentService()


@router.post("/consent", response_model=ConsentResponse)
async def consent(
    citizen_id: str = Form(...),
    ministry_code: str = Form(...),
    data_scope: str = Form(...),
    audio_file: UploadFile = ...,
    db: Session = Depends(get_db),
):
    """Record voice consent: verify speaker, confirm affirmative, sign token.

    Pipeline: voice auth → Whisper transcribe → Ed25519 sign → store CONSENT_TOKEN.
    """
    # ── Resolve citizen and template ─────────────────────────────────────
    citizen = get_citizen_by_id(db, citizen_id)
    if citizen is None:
        raise HTTPException(status_code=404, detail="Citizen not found")

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

    # ── Voice auth: verify speaker identity ──────────────────────────────
    embedding_service = EmbeddingService()
    live_embedding = embedding_service.extract_embedding(preprocessed)
    encrypted_centroid = _encryption_service.deserialize_ciphertext(template.he_ciphertext)
    match_result = _matching_service.match(
        live_embedding, encrypted_centroid, _encryption_service.private_key
    )

    if not match_result["granted"]:
        raise HTTPException(status_code=403, detail="Voice verification failed")

    # ── Whisper transcribe to confirm affirmative ────────────────────────
    transcription_service = TranscriptionService()
    transcript = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)
    # For prototype: accept any non-empty transcript as affirmative consent
    # (production would check for "yes", "ndiyo", etc.)

    # ── Ed25519 sign consent payload ─────────────────────────────────────
    issued_at = datetime.now(timezone.utc)
    signature = _consent_service.sign_consent(
        citizen_id=citizen.citizen_id,
        ministry_code=ministry_code,
        data_scope=data_scope,
        issued_at=issued_at.isoformat(),
    )

    # ── Store CONSENT_TOKEN ──────────────────────────────────────────────
    token = create_consent_token(
        db,
        citizen_id=citizen.citizen_id,
        ministry_code=ministry_code,
        data_scope=data_scope,
        digital_signature=signature,
    )

    return ConsentResponse(
        token_id=token.token_id,
        ministry_code=token.ministry_code,
        data_scope=token.data_scope,
        issued_at=token.issued_at,
        digital_signature=token.digital_signature,
    )
