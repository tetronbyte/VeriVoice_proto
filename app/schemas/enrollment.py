"""Pydantic schemas for the enrollment endpoint (POST /api/v1/enroll)."""

from datetime import datetime

from pydantic import BaseModel, Field


class EnrollmentRequest(BaseModel):
    national_id_number: str = Field(..., min_length=1, examples=["KE-123456"])
    preferred_language: str = Field(default="en", pattern=r"^[a-z]{2}$", examples=["en", "sw"])
    phone_number: str = Field(
        ...,
        pattern=r"^\+\d{10,15}$",
        examples=["+254700000000"],
        description="E.164 international phone format",
    )


class EnrollmentResponse(BaseModel):
    citizen_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    enrolled_at: datetime
    template_id: str = Field(..., examples=["660e8400-e29b-41d4-a716-446655440000"])
    status: str = Field(default="enrolled", examples=["enrolled"])
