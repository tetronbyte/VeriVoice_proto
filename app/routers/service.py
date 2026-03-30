"""POST /api/v1/service-access — Simulated health insurance form (PRD Section 10.1)."""

import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.crud import get_citizen_by_id, get_consent_token
from app.db.database import get_db
from app.schemas.consent import ServiceAccessResponse
from app.services.transcription_service import TranscriptionService
from app.services.audio_preprocessor import AudioPreprocessor

router = APIRouter()

_preprocessor = AudioPreprocessor()

# Simulated form questions
FORM_QUESTIONS = [
    "What is your full name?",
    "What is your date of birth?",
    "What is your current address?",
]


@router.post("/service-access", response_model=ServiceAccessResponse)
async def service_access(
    citizen_id: str = Form(...),
    consent_token_id: str = Form(...),
    audio_file: UploadFile = ...,
    question_index: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """Simulated health insurance form with voice Q&A.

    Pipeline: verify consent token → Whisper transcribe answer → return response.
    """
    # ── Verify citizen exists ────────────────────────────────────────────
    citizen = get_citizen_by_id(db, citizen_id)
    if citizen is None:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # ── Verify consent token is valid and not revoked ────────────────────
    token = get_consent_token(db, consent_token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Consent token not found")
    if token.citizen_id != citizen_id:
        raise HTTPException(status_code=403, detail="Consent token does not belong to this citizen")
    if token.is_revoked:
        raise HTTPException(status_code=403, detail="Consent token has been revoked")

    # ── Read and preprocess audio ────────────────────────────────────────
    raw_bytes = await audio_file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        preprocessed = _preprocessor.process(tmp_path)
    finally:
        os.unlink(tmp_path)

    # ── Transcribe the answer ────────────────────────────────────────────
    transcription_service = TranscriptionService()
    answer = transcription_service.transcribe(preprocessed, language=citizen.preferred_language)

    # ── Determine which question was asked ───────────────────────────────
    qi = min(question_index, len(FORM_QUESTIONS) - 1)
    question = FORM_QUESTIONS[qi]

    return ServiceAccessResponse(
        form_id=str(uuid.uuid4()),
        question=question,
        transcribed_answer=answer,
        status="completed",
    )
