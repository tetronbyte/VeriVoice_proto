"""Phase 20 validation: Pydantic schemas for MOSIP e-Signet endpoints."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.mosip import (
    MosipAuthorizeResponse,
    MosipCallbackRequest,
    MosipIdentityResponse,
    MosipLinkRequest,
    MosipLinkResponse,
)


# ── MosipAuthorizeResponse ──────────────────────────────────────────────────

class TestMosipAuthorizeResponse:
    def test_valid(self):
        resp = MosipAuthorizeResponse(
            authorize_url="https://esignet.collab.mosip.net/v1/esignet/authorize?response_type=code",
            state="abc123",
        )
        assert resp.authorize_url.startswith("https://")
        assert resp.state == "abc123"

    def test_missing_authorize_url(self):
        with pytest.raises(ValidationError):
            MosipAuthorizeResponse(state="abc123")

    def test_missing_state(self):
        with pytest.raises(ValidationError):
            MosipAuthorizeResponse(authorize_url="https://example.com/authorize")


# ── MosipCallbackRequest ────────────────────────────────────────────────────

class TestMosipCallbackRequest:
    def test_valid(self):
        req = MosipCallbackRequest(code="auth-code-xyz", state="state-abc")
        assert req.code == "auth-code-xyz"
        assert req.state == "state-abc"

    def test_empty_code_raises(self):
        with pytest.raises(ValidationError):
            MosipCallbackRequest(code="", state="state-abc")

    def test_empty_state_raises(self):
        with pytest.raises(ValidationError):
            MosipCallbackRequest(code="auth-code-xyz", state="")

    def test_missing_code_raises(self):
        with pytest.raises(ValidationError):
            MosipCallbackRequest(state="state-abc")

    def test_missing_state_raises(self):
        with pytest.raises(ValidationError):
            MosipCallbackRequest(code="auth-code-xyz")


# ── MosipIdentityResponse ──────────────────────────────────────────────────

class TestMosipIdentityResponse:
    def test_valid_with_linked_citizen(self):
        resp = MosipIdentityResponse(
            mosip_individual_id="MOSIP-IND-12345",
            identity_verified=True,
            linked_citizen_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert resp.mosip_individual_id == "MOSIP-IND-12345"
        assert resp.identity_verified is True
        assert resp.linked_citizen_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_valid_without_linked_citizen(self):
        resp = MosipIdentityResponse(
            mosip_individual_id="MOSIP-IND-67890",
            identity_verified=True,
        )
        assert resp.linked_citizen_id is None

    def test_missing_mosip_id_raises(self):
        with pytest.raises(ValidationError):
            MosipIdentityResponse(identity_verified=True)

    def test_missing_identity_verified_raises(self):
        with pytest.raises(ValidationError):
            MosipIdentityResponse(mosip_individual_id="MOSIP-IND-12345")


# ── MosipLinkRequest ────────────────────────────────────────────────────────

class TestMosipLinkRequest:
    def test_valid(self):
        req = MosipLinkRequest(
            citizen_id="550e8400-e29b-41d4-a716-446655440000",
            mosip_individual_id="MOSIP-IND-12345",
        )
        assert req.citizen_id == "550e8400-e29b-41d4-a716-446655440000"
        assert req.mosip_individual_id == "MOSIP-IND-12345"

    def test_empty_citizen_id_raises(self):
        with pytest.raises(ValidationError):
            MosipLinkRequest(citizen_id="", mosip_individual_id="MOSIP-IND-12345")

    def test_empty_mosip_id_raises(self):
        with pytest.raises(ValidationError):
            MosipLinkRequest(citizen_id="some-id", mosip_individual_id="")


# ── MosipLinkResponse ──────────────────────────────────────────────────────

class TestMosipLinkResponse:
    def test_valid(self):
        now = datetime.now(timezone.utc)
        resp = MosipLinkResponse(
            citizen_id="550e8400-e29b-41d4-a716-446655440000",
            mosip_individual_id="MOSIP-IND-12345",
            identity_verified=True,
            linked_at=now,
        )
        assert resp.identity_verified is True
        assert resp.linked_at == now

    def test_missing_linked_at_raises(self):
        with pytest.raises(ValidationError):
            MosipLinkResponse(
                citizen_id="some-id",
                mosip_individual_id="MOSIP-IND-12345",
                identity_verified=True,
            )
