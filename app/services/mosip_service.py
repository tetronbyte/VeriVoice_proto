"""MosipService — OIDC client for MOSIP e-Signet identity verification."""

import secrets
import json

import httpx
import redis
from jose import jwt, JWTError

from app.config import settings

_OIDC_TTL_SECONDS = 300  # 5-minute TTL for state/nonce


class MosipService:
    def __init__(self) -> None:
        self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self._jwks_cache: dict | None = None

    # ── OIDC State Management ───────────────────────────────────────────────

    def store_oidc_context(self, state: str, nonce: str) -> None:
        """Store OIDC state→nonce mapping in Redis with 5-min TTL."""
        key = f"esignet:state:{state}"
        self._redis.setex(key, _OIDC_TTL_SECONDS, nonce)

    def consume_oidc_context(self, state: str) -> str:
        """Retrieve and delete the nonce for a given state (one-time use).

        Raises ValueError if state not found or already consumed.
        """
        key = f"esignet:state:{state}"
        nonce = self._redis.getdel(key)
        if nonce is None:
            raise ValueError("Invalid or expired OIDC state")
        return nonce

    # ── Authorize ───────────────────────────────────────────────────────────

    def get_authorize_url(self) -> dict:
        """Build e-Signet /authorize URL with generated state and nonce.

        Returns {authorize_url, state, nonce}.
        """
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        self.store_oidc_context(state, nonce)

        params = {
            "response_type": "code",
            "client_id": settings.ESIGNET_CLIENT_ID,
            "redirect_uri": settings.ESIGNET_REDIRECT_URI,
            "scope": settings.ESIGNET_SCOPES,
            "state": state,
            "nonce": nonce,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        authorize_url = f"{settings.ESIGNET_BASE_URL}/v1/esignet/authorize?{query}"

        return {"authorize_url": authorize_url, "state": state, "nonce": nonce}

    # ── Token Exchange ──────────────────────────────────────────────────────

    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for tokens at e-Signet /token endpoint.

        Returns the full token response dict (id_token, access_token, etc.).
        """
        token_url = f"{settings.ESIGNET_BASE_URL}/v1/esignet/oauth/v2/token"
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.ESIGNET_REDIRECT_URI,
            "client_id": settings.ESIGNET_CLIENT_ID,
            "client_secret": settings.ESIGNET_CLIENT_SECRET,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            return resp.json()

    # ── JWT / JWKS Validation ───────────────────────────────────────────────

    async def _fetch_jwks(self) -> dict:
        """Fetch JWKS from e-Signet (cached after first call)."""
        if self._jwks_cache is not None:
            return self._jwks_cache
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.ESIGNET_JWKS_URI)
            resp.raise_for_status()
            self._jwks_cache = resp.json()
            return self._jwks_cache

    def _fetch_jwks_sync(self, jwks: dict | None = None) -> dict:
        """Use pre-supplied JWKS (for testing) or the cached copy."""
        if jwks is not None:
            return jwks
        if self._jwks_cache is not None:
            return self._jwks_cache
        # Synchronous fallback — should only happen in edge cases
        resp = httpx.get(settings.ESIGNET_JWKS_URI)
        resp.raise_for_status()
        self._jwks_cache = resp.json()
        return self._jwks_cache

    def validate_id_token(self, id_token: str, nonce: str, jwks: dict | None = None) -> dict:
        """Validate an e-Signet id_token JWT.

        Checks:
          1. Signature — verified against MOSIP JWKS public keys
          2. Expiry    — `exp` claim must be in the future
          3. Audience  — `aud` must contain our ESIGNET_CLIENT_ID
          4. Issuer    — `iss` must match ESIGNET_BASE_URL
          5. Nonce     — `nonce` claim must match the one we stored in Redis

        Args:
            id_token: The raw JWT string from the token response.
            nonce: The nonce that was sent in the original /authorize request.
            jwks: Optional pre-loaded JWKS dict (used in tests to avoid HTTP).

        Returns:
            Decoded JWT claims dict.

        Raises:
            jose.JWTError: If signature, expiry, audience, or issuer is invalid.
            ValueError: If the nonce claim does not match.
        """
        key_set = self._fetch_jwks_sync(jwks)

        claims = jwt.decode(
            id_token,
            key_set,
            algorithms=["RS256"],
            audience=settings.ESIGNET_CLIENT_ID,
            issuer=settings.ESIGNET_BASE_URL,
            options={"verify_exp": True, "verify_aud": True, "verify_iss": True},
        )

        # Nonce check — prevents replay of tokens across sessions
        token_nonce = claims.get("nonce")
        if token_nonce != nonce:
            raise ValueError(
                f"Nonce mismatch: expected {nonce!r}, got {token_nonce!r}"
            )

        return claims

    # ── Convenience ─────────────────────────────────────────────────────────

    def get_individual_id(self, id_token: str, nonce: str, jwks: dict | None = None) -> str:
        """Validate token and return the MOSIP individual_id (sub claim)."""
        claims = self.validate_id_token(id_token, nonce, jwks=jwks)
        sub = claims.get("sub")
        if not sub:
            raise ValueError("id_token missing 'sub' claim")
        return sub
