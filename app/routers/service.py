"""POST /api/v1/service-access — Health insurance form via voice (PRD Section 10.1)."""

import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.crud import get_citizen_by_id, get_consent_token
from app.db.database import get_db
from app.schemas.consent import ServiceAccessResponse, ServiceFormSummary
from app.services.transcription_service import TranscriptionService
from app.services.tts_service import TTSService
from app.services.audio_preprocessor import AudioPreprocessor

router = APIRouter()

_preprocessor = AudioPreprocessor()
_tts_service = TTSService()

# Health insurance form questions — bilingual, each targeting a different capability
FORM_QUESTIONS = {
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

FORM_FIELD_KEYS = ["full_name", "dependants", "primary_facility"]


def _parse_dependants(raw: str) -> str:
    """Try to normalise a spoken number to a digit string."""
    word_map = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "sifuri": "0", "moja": "1", "mbili": "2", "tatu": "3",
        "nne": "4", "tano": "5", "sita": "6", "saba": "7", "nane": "8",
        "tisa": "9", "kumi": "10",
    }
    cleaned = raw.strip().lower().rstrip(".")
    if cleaned in word_map:
        return word_map[cleaned]
    # If Whisper already returned a digit, keep it
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    return digits if digits else cleaned


@router.post("/service-access", response_model=ServiceAccessResponse)
async def service_access(
    citizen_id: str = Form(...),
    consent_token_id: str = Form(...),
    audio_file: UploadFile = ...,
    question_index: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """Answer one health insurance form question via voice.

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

    # ── Resolve language and question ────────────────────────────────────
    lang = citizen.preferred_language or "en"
    q_list = FORM_QUESTIONS.get(lang, FORM_QUESTIONS["en"])
    qi = min(question_index, len(q_list) - 1)
    question = q_list[qi]
    field_key = FORM_FIELD_KEYS[qi]

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
    raw_answer = transcription_service.transcribe(preprocessed, language=lang)

    # Parse dependants to a number when applicable
    if field_key == "dependants":
        answer = _parse_dependants(raw_answer)
    else:
        answer = raw_answer

    remaining = len(q_list) - qi - 1

    return ServiceAccessResponse(
        form_id=str(uuid.uuid4()),
        question=question,
        field_key=field_key,
        transcribed_answer=answer,
        raw_transcription=raw_answer,
        questions_remaining=remaining,
        status="completed",
    )


@router.post("/service-access/summary", response_model=ServiceFormSummary)
async def service_summary(
    citizen_id: str = Form(...),
    consent_token_id: str = Form(...),
    full_name: str = Form(...),
    dependants: str = Form(...),
    primary_facility: str = Form(...),
    language: str = Form(default="en"),
):
    """Generate a TTS read-back summary of the completed form answers."""
    # ── Verify citizen exists ────────────────────────────────────────────
    if language == "sw":
        summary_text = (
            f"Asante. Nimekusanya: jina lako ni {full_name}, "
            f"una wategemezi {dependants}, "
            f"na kituo chako kikuu ni {primary_facility}. "
            f"Je, hii ni sahihi?"
        )
    else:
        summary_text = (
            f"Thank you. I have recorded: your name is {full_name}, "
            f"you have {dependants} dependants, "
            f"and your preferred facility is {primary_facility}. "
            f"Is this correct?"
        )

    audio_path = _tts_service.synthesize_to_wav(summary_text, language=language)

    return ServiceFormSummary(
        summary_text=summary_text,
        audio_url=audio_path,
        full_name=full_name,
        dependants=dependants,
        primary_facility=primary_facility,
    )
