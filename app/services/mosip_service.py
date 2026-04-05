"""MosipService — OIDC client for MOSIP e-Signet identity verification."""

import secrets
import json
import time
import uuid
from pathlib import Path

import httpx
import redis
import jwt as pyjwt
from jwt import PyJWKClient, InvalidTokenError
from jose import jwt, JWTError

from app.config import settings

_OIDC_TTL_SECONDS = 300  # 5-minute TTL for state/nonce


class MosipService:
    def __init__(self) -> None:
        self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self._jwks_cache: dict | None = None

    # ── OIDC State Management ───────────────────────────────────────────────

    def store_oidc_context(self, state: str, nonce: str, call_sid: str | None = None) -> None:
        """Store OIDC state→nonce mapping in Redis with 5-min TTL.

        If `call_sid` is provided, also maps state→call_sid so the IVR flow
        can correlate the OIDC callback back to the originating phone call.
        """
        self._redis.setex(f"esignet:state:{state}", _OIDC_TTL_SECONDS, nonce)
        if call_sid:
            self._redis.setex(f"esignet:state_call:{state}", _OIDC_TTL_SECONDS, call_sid)

    def store_verified_identity(self, state: str, individual_id: str) -> None:
        """Mark an OIDC state as verified with the MOSIP individual_id.

        IVR poll endpoints read this to detect when a call can proceed.
        """
        self._redis.setex(
            f"esignet:verified_state:{state}", _OIDC_TTL_SECONDS, individual_id
        )

    def get_verified_identity(self, state: str) -> str | None:
        """Look up the verified individual_id for a given state, or None."""
        return self._redis.get(f"esignet:verified_state:{state}")

    def consume_oidc_context(self, state: str) -> str:
        """Retrieve and delete the nonce for a given state (one-time use).

        Raises ValueError if state not found or already consumed.
        """
        key = f"esignet:state:{state}"
        # Atomic GET+DEL via pipeline (GETDEL requires Redis 6.2+)
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        nonce, _ = pipe.execute()
        if nonce is None:
            raise ValueError("Invalid or expired OIDC state")
        return nonce

    # ── Authorize ───────────────────────────────────────────────────────────

    def get_authorize_url(self, call_sid: str | None = None) -> dict:
        """Build e-Signet /authorize URL with generated state and nonce.

        If `call_sid` is provided, the state is also mapped to that call,
        enabling IVR flows to look up verification status by state.

        Returns {authorize_url, state, nonce}.
        """
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        self.store_oidc_context(state, nonce, call_sid=call_sid)

        params = {
            "response_type": "code",
            "client_id": settings.ESIGNET_CLIENT_ID,
            "redirect_uri": settings.ESIGNET_REDIRECT_URI,
            "scope": settings.ESIGNET_SCOPES,
            "state": state,
            "nonce": nonce,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        ui_url = getattr(settings, "ESIGNET_UI_URL", settings.ESIGNET_BASE_URL)
        authorize_url = f"{ui_url}/authorize?{query}"

        return {"authorize_url": authorize_url, "state": state, "nonce": nonce}

    # ── Token Exchange ──────────────────────────────────────────────────────

    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for tokens at e-Signet /token endpoint.

        Returns the full token response dict (id_token, access_token, etc.).
        """
        token_url = f"{settings.ESIGNET_BASE_URL}/v1/esignet/oauth/v2/token"

        # private_key_jwt: sign a JWT assertion with our RSA private key
        private_key_path = getattr(settings, "ESIGNET_PRIVATE_KEY_PATH", "")
        if private_key_path and Path(private_key_path).exists():
            now = int(time.time())
            assertion_claims = {
                "iss": settings.ESIGNET_CLIENT_ID,
                "sub": settings.ESIGNET_CLIENT_ID,
                "aud": token_url,
                "jti": uuid.uuid4().hex,
                "iat": now,
                "exp": now + 300,
            }
            private_key_pem = Path(private_key_path).read_text()
            client_assertion = jwt.encode(
                assertion_claims, private_key_pem, algorithm="RS256"
            )
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.ESIGNET_REDIRECT_URI,
                "client_id": settings.ESIGNET_CLIENT_ID,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": client_assertion,
            }
        else:
            # Fallback: client_secret_post
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

        # Use PyJWT (supports PS256, which python-jose does not).
        # Find the signing key from JWKS by kid.
        unverified_header = pyjwt.get_unverified_header(id_token)
        token_kid = unverified_header.get("kid")
        token_alg = unverified_header.get("alg", "PS256")

        signing_key = None
        for k in key_set.get("keys", []):
            if k.get("kid") == token_kid:
                signing_key = pyjwt.PyJWK(k, algorithm=token_alg).key
                break
        if signing_key is None:
            raise ValueError(f"No matching JWKS key for kid={token_kid}")

        expected_issuer = settings.ESIGNET_ISSUER or settings.ESIGNET_BASE_URL
        claims = pyjwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256", "RS384", "RS512", "PS256", "PS384", "PS512"],
            audience=settings.ESIGNET_CLIENT_ID,
            issuer=expected_issuer,
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
