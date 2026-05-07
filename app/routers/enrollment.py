"""POST /api/v1/enroll — Voice enrollment endpoint (PRD Section 10.1)."""

import io
import logging
import os
import tempfile

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

import redis
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.utils.upload_validation import validate_audio_upload

logger = logging.getLogger("verivoice.enroll")
from app.db.crud import create_citizen, create_voice_template, get_citizen_by_mosip_id, get_citizen_by_national_id
from app.db.database import get_db
from app.schemas.enrollment import EnrollmentResponse

_redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
from app.services.audio_preprocessor import AudioPreprocessor
from app.services.embedding_service import EmbeddingService
from app.services.encryption_service import EncryptionService

router = APIRouter()

_preprocessor = AudioPreprocessor()
_encryption_service = EncryptionService()


@router.post("/enroll", response_model=EnrollmentResponse)
async def enroll(
    national_id_number: str = Form(...),
    preferred_language: str = Form(default="en"),
    phone_number: str = Form(...),
    mosip_individual_id: str = Form(default=None),
    audio_files: list[UploadFile] = [],
    db: Session = Depends(get_db),
):
    """Enroll a citizen with 5 voice samples.

    Pipeline: create citizen → preprocess 5 audios → extract 5 embeddings →
    compute centroid → Paillier HE encrypt → store voice template.

    If mosip_individual_id is provided, verifies it was obtained from a
    valid e-Signet callback (checked via Redis) and creates the citizen
    with identity_verified=True.
    """
    logger.info("[ENROLL] ── START ── national_id=%s lang=%s phone=%s mosip_id=%s audio_count=%d",
                 national_id_number, preferred_language, phone_number,
                 mosip_individual_id or "(none)", len(audio_files))

    # ── Validate audio count ─────────────────────────────────────────────
    if len(audio_files) != settings.ENROLLMENT_PHRASES:
        raise HTTPException(
            status_code=400,
            detail=f"Exactly {settings.ENROLLMENT_PHRASES} audio files required, got {len(audio_files)}",
        )

    # ── Check for duplicate national ID ──────────────────────────────────
    if get_citizen_by_national_id(db, national_id_number) is not None:
        logger.warning("[ENROLL] ── DUPLICATE ── National ID '%s' already enrolled", national_id_number)
        raise HTTPException(
            status_code=409,
            detail=f"National ID '{national_id_number}' is already enrolled",
        )

    # ── Resolve identity verification ────────────────────────────────────
    identity_verified = False
    if mosip_individual_id:
        # Atomically consume the one-time verification token (B4 fix)
        redis_key = f"esignet:verified:{mosip_individual_id}"
        if not _redis_client.getdel(redis_key):
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired MOSIP identity session. "
                "Complete e-Signet verification before enrolling.",
            )

        # Check for duplicate MOSIP ID
        if get_citizen_by_mosip_id(db, mosip_individual_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="This MOSIP individual_id is already linked to another citizen",
            )
        identity_verified = True
        logger.info("[ENROLL] ── MOSIP ── Verified MOSIP ID consumed: %s", mosip_individual_id)

    # ── Create citizen record ────────────────────────────────────────────
    try:
        citizen = create_citizen(
            db,
            national_id_number=national_id_number,
            preferred_language=preferred_language,
            phone_number=phone_number,
            mosip_individual_id=mosip_individual_id,
            identity_verified=identity_verified,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This MOSIP individual_id is already linked to another citizen",
        )

    logger.info("[ENROLL] ── CITIZEN CREATED ── citizen_id=%s identity_verified=%s",
                 citizen.citizen_id, citizen.identity_verified)

    # ── Process each audio file ──────────────────────────────────────────
    embedding_service = EmbeddingService()
    embeddings: list[np.ndarray] = []

    for i, upload in enumerate(audio_files):
        logger.info("[ENROLL] ── AUDIO %d/%d ── Processing file: %s (%s)",
                     i + 1, len(audio_files), upload.filename, upload.content_type)
        raw_bytes = await validate_audio_upload(upload)

        # Write to a temp WAV so librosa/preprocessor can load it
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name

        try:
            preprocessed = _preprocessor.process(tmp_path)
            logger.info("[ENROLL] ── AUDIO %d/%d ── Preprocessed: %d samples (%.2fs)",
                         i + 1, len(audio_files), len(preprocessed),
                         len(preprocessed) / settings.SAMPLE_RATE)
            embedding = embedding_service.extract_embedding(preprocessed)
            logger.info("[ENROLL] ── AUDIO %d/%d ── Embedding extracted: dim=%d norm=%.4f",
                         i + 1, len(audio_files), len(embedding), float(np.linalg.norm(embedding)))
            embeddings.append(embedding)
        except Exception as exc:
            logger.error("[ENROLL] ── AUDIO %d/%d ── FAILED: %s", i + 1, len(audio_files), exc)
            raise
        finally:
            os.unlink(tmp_path)

    # ── Compute centroid and encrypt ─────────────────────────────────────
    logger.info("[ENROLL] ── CENTROID ── Computing centroid from %d embeddings", len(embeddings))
    centroid = embedding_service.compute_centroid(embeddings)
    logger.info("[ENROLL] ── ENCRYPT ── Encrypting centroid with Paillier HE (%d-bit)", settings.PAILLIER_BITS)
    ciphertext_bytes = _encryption_service.encrypt_centroid(centroid)
    # centroid is already zeroed by encrypt_centroid

    # ── Zero temporary embeddings ────────────────────────────────────────
    for emb in embeddings:
        emb[:] = 0.0
    embeddings.clear()
    logger.info("[ENROLL] ── SECURITY ── Plaintext centroid and embeddings zeroed from memory")

    # ── Store voice template ─────────────────────────────────────────────
    template = create_voice_template(
        db,
        citizen_id=citizen.citizen_id,
        he_ciphertext=ciphertext_bytes,
    )
    logger.info("[ENROLL] ── DONE ── template_id=%s citizen_id=%s identity_verified=%s",
                 template.template_id, citizen.citizen_id, citizen.identity_verified)

    return EnrollmentResponse(
        citizen_id=citizen.citizen_id,
        enrolled_at=citizen.enrolled_at,
        template_id=template.template_id,
        status="enrolled",
        identity_verified=citizen.identity_verified,
    )
