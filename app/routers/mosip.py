"""MOSIP e-Signet OIDC endpoints (PRD Section 10.1)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.crud import get_citizen_by_id, get_citizen_by_mosip_id, link_mosip_identity
from app.db.database import get_db
from app.schemas.mosip import (
    MosipAuthorizeResponse,
    MosipIdentityResponse,
    MosipLinkRequest,
    MosipLinkResponse,
)
from app.services.mosip_service import MosipService

router = APIRouter()

_mosip_service = MosipService()


@router.get("/mosip/authorize", response_model=MosipAuthorizeResponse)
async def mosip_authorize():
    """Initiate e-Signet OIDC login — generates state+nonce, returns redirect URL."""
    result = _mosip_service.get_authorize_url()
    return MosipAuthorizeResponse(
        authorize_url=result["authorize_url"],
        state=result["state"],
    )


@router.get("/mosip/callback", response_model=MosipIdentityResponse)
async def mosip_callback(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
):
    """e-Signet OIDC callback — validates state, exchanges code, verifies JWT.

    Security flow:
      1. Retrieve and DELETE the nonce for this state from Redis (atomic GETDEL).
         If the state is unknown or already consumed, reject immediately (400).
      2. Exchange the authorization code for an id_token at e-Signet /token.
      3. Validate the id_token JWT: signature (JWKS), exp, aud, iss, nonce.
      4. Extract the verified sub (MOSIP individual_id).
    """
    # Step 1: Validate state — atomic consume from Redis
    try:
        nonce = _mosip_service.consume_oidc_context(state)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC state")

    # Step 2: Exchange code for tokens
    try:
        token_response = await _mosip_service.exchange_code(code)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token exchange failed: {exc}")

    id_token = token_response.get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="No id_token in token response")

    # Step 3-4: Validate JWT and extract sub
    try:
        individual_id = _mosip_service.get_individual_id(id_token, nonce)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"JWT validation failed: {exc}")

    # Store verified MOSIP ID in Redis so enrollment can reference it (5-min TTL)
    import redis as _redis_mod
    from app.config import settings as _settings
    _r = _redis_mod.from_url(_settings.REDIS_URL, decode_responses=True)
    _r.setex(f"esignet:verified:{individual_id}", 300, "1")

    linked_citizen_id = None

    return MosipIdentityResponse(
        mosip_individual_id=individual_id,
        identity_verified=True,
        linked_citizen_id=linked_citizen_id,
    )


@router.post("/mosip/link", response_model=MosipLinkResponse)
async def mosip_link(
    request: MosipLinkRequest,
    db: Session = Depends(get_db),
):
    """Link a verified MOSIP identity to an existing VeriVoice citizen."""
    # Verify citizen exists
    citizen = get_citizen_by_id(db, request.citizen_id)
    if citizen is None:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Attempt to link
    try:
        linked = link_mosip_identity(db, request.citizen_id, request.mosip_individual_id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This MOSIP individual_id is already linked to another citizen",
        )

    return MosipLinkResponse(
        citizen_id=linked.citizen_id,
        mosip_individual_id=linked.mosip_individual_id,
        identity_verified=linked.identity_verified,
        linked_at=datetime.now(timezone.utc),
    )
