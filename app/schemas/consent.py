"""Pydantic schemas for consent and service-access endpoints."""

import base64
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator


class ConsentResponse(BaseModel):
    token_id: str = Field(..., examples=["990e8400-e29b-41d4-a716-446655440000"])
    ministry_code: str = Field(..., examples=["MOH"])
    data_scope: str = Field(..., examples=["health_records"])
    issued_at: datetime
    digital_signature: bytes = Field(..., description="Ed25519 signature (base64-encoded in JSON)")

    @field_serializer("digital_signature")
    def serialize_signature(self, v: bytes, _info) -> str:
        """Encode bytes as base64 string for JSON output."""
        return base64.b64encode(v).decode("ascii")

    @field_validator("digital_signature", mode="before")
    @classmethod
    def deserialize_signature(cls, v):
        """Accept both raw bytes and base64-encoded strings."""
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class ServiceAccessResponse(BaseModel):
    form_id: str = Field(..., examples=["aa0e8400-e29b-41d4-a716-446655440000"])
    question: str = Field(..., examples=["Please say your full name."])
    field_key: str = Field(..., examples=["full_name"])
    transcribed_answer: str = Field(..., examples=["Amina Juma Ochieng"])
    raw_transcription: str = Field(..., examples=["Amina Juma Ochieng"])
    questions_remaining: int = Field(..., ge=0, examples=[2])
    status: str = Field(default="completed", examples=["completed"])


class ServiceFormSummary(BaseModel):
    summary_text: str = Field(..., examples=["Thank you. I have recorded: your name is Amina..."])
    audio_url: str = Field(..., examples=["/tmp/verivoice_tts/summary.wav"])
    full_name: str
    dependants: str
    primary_facility: str
