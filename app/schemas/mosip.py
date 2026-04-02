"""Pydantic schemas for MOSIP e-Signet OIDC endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MosipAuthorizeResponse(BaseModel):
    authorize_url: str = Field(
        ...,
        description="Full e-Signet /authorize URL to redirect the citizen to",
        examples=["https://esignet.collab.mosip.net/v1/esignet/authorize?response_type=code&client_id=..."],
    )
    state: str = Field(
        ...,
        description="OIDC state parameter (opaque, passed back in callback)",
        examples=["a1b2c3d4e5f6"],
    )


class MosipCallbackRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        description="Authorization code returned by e-Signet",
        examples=["auth-code-xyz"],
    )
    state: str = Field(
        ...,
        min_length=1,
        description="OIDC state parameter (must match the one from /authorize)",
        examples=["a1b2c3d4e5f6"],
    )


class MosipIdentityResponse(BaseModel):
    mosip_individual_id: str = Field(
        ...,
        description="Verified MOSIP subject ID (sub claim from id_token)",
        examples=["MOSIP-IND-12345"],
    )
    identity_verified: bool = Field(
        ...,
        description="Whether the identity has been cryptographically verified via e-Signet",
        examples=[True],
    )
    linked_citizen_id: Optional[str] = Field(
        default=None,
        description="citizen_id if this MOSIP ID is already linked to a VeriVoice citizen (None if new)",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class MosipLinkRequest(BaseModel):
    citizen_id: str = Field(
        ...,
        min_length=1,
        description="VeriVoice citizen_id to link",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    mosip_individual_id: str = Field(
        ...,
        min_length=1,
        description="Verified MOSIP individual_id from a prior /callback response",
        examples=["MOSIP-IND-12345"],
    )


class MosipLinkResponse(BaseModel):
    citizen_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    mosip_individual_id: str = Field(..., examples=["MOSIP-IND-12345"])
    identity_verified: bool = Field(..., examples=[True])
    linked_at: datetime = Field(
        ...,
        description="UTC timestamp of when the identity was linked",
    )
