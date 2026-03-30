"""Pydantic schemas for authentication endpoints."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AuthResult(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"


class AuthenticationResponse(BaseModel):
    event_id: str = Field(..., examples=["770e8400-e29b-41d4-a716-446655440000"])
    voice_match_score: float = Field(..., ge=-1.0, le=1.0, examples=[0.87])
    transcript_match: bool
    transcript_match_score: float = Field(..., ge=0.0, le=1.0, examples=[0.875])
    result: AuthResult
    event_timestamp: datetime


class ChallengeResponse(BaseModel):
    challenge_id: str = Field(..., examples=["880e8400-e29b-41d4-a716-446655440000"])
    phrase_text: str = Field(..., examples=["The sun rises over the mountain every morning"])
    audio_url: str | None = Field(default=None, examples=["/static/challenges/abc.wav"])
