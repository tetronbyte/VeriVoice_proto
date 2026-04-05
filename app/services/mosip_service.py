"""MosipService — OIDC client for MOSIP e-Signet identity verification."""

import base64
import hashlib
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

    async def exchange_code(self, code: str, code_verifier: str | None = None) -> dict:
        """Exchange authorization code for tokens at e-Signet /token endpoint.

        If `code_verifier` is provided, the PKCE code_verifier is sent with the
        request (required by server-driven OAuth flows that used code_challenge).

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
            if code_verifier:
                data["code_verifier"] = code_verifier
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

    # ── Server-driven OTP flow (DTMF / IVR) ─────────────────────────────────

    @staticmethod
    def _pkce_pair() -> tuple[str, str]:
        """Generate a PKCE code_verifier + code_challenge (S256)."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return verifier, challenge

    @staticmethod
    def _compute_oauth_details_hash(response_obj: dict) -> str:
        """Compute oauth-details-hash = base64url(sha256(JSON.stringify(response)))."""
        payload = json.dumps(response_obj, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    async def start_otp_auth(self, individual_id: str) -> dict:
        """Step 1-3 of server-driven OAuth: CSRF → oauth-details → send-otp.

        Returns a dict with {transaction_id, oauth_details_key, oauth_details_hash,
        code_verifier, nonce, state, cookies_jar} to be held in Redis until the
        user enters their OTP.
        """
        base = settings.ESIGNET_BASE_URL
        verifier, challenge = self._pkce_pair()
        nonce = secrets.token_urlsafe(16)
        state = secrets.token_urlsafe(16)

        async with httpx.AsyncClient() as client:
            # CSRF token
            r = await client.get(f"{base}/v1/esignet/csrf/token")
            r.raise_for_status()
            csrf_token = r.json()["token"]
            cookies = {c.name: c.value for c in r.cookies.jar}

            # oauth-details
            payload = {
                "requestTime": _now_iso(),
                "request": {
                    "clientId": settings.ESIGNET_CLIENT_ID,
                    "scope": settings.ESIGNET_SCOPES,
                    "responseType": "code",
                    "redirectUri": settings.ESIGNET_REDIRECT_URI,
                    "display": "popup",
                    "prompt": "login",
                    "acrValues": "mosip:idp:acr:generated-code",
                    "claims": {"userinfo": {}, "id_token": {}},
                    "nonce": nonce,
                    "state": state,
                    "claimsLocales": "en",
                    "codeChallenge": challenge,
                    "codeChallengeMethod": "S256",
                },
            }
            r = await client.post(
                f"{base}/v1/esignet/authorization/v3/oauth-details",
                json=payload,
                headers={"X-XSRF-TOKEN": csrf_token},
                cookies=cookies,
            )
            r.raise_for_status()
            oauth_resp = r.json()["response"]
            transaction_id = oauth_resp["transactionId"]
            oauth_hash = self._compute_oauth_details_hash(oauth_resp)
            cookies = {c.name: c.value for c in r.cookies.jar} or cookies

            # send-otp
            otp_payload = {
                "requestTime": _now_iso(),
                "request": {
                    "transactionId": transaction_id,
                    "individualId": individual_id,
                    "otpChannels": ["email", "phone"],
                    "captchaToken": "dummy",
                },
            }
            r = await client.post(
                f"{base}/v1/esignet/authorization/send-otp",
                json=otp_payload,
                headers={
                    "X-XSRF-TOKEN": csrf_token,
                    "oauth-details-key": transaction_id,
                    "oauth-details-hash": oauth_hash,
                },
                cookies=cookies,
            )
            r.raise_for_status()
            send_body = r.json()
            if send_body.get("errors"):
                raise ValueError(f"send-otp failed: {send_body['errors']}")

        return {
            "transaction_id": transaction_id,
            "oauth_details_key": transaction_id,
            "oauth_details_hash": oauth_hash,
            "csrf_token": csrf_token,
            "cookies": cookies,
            "code_verifier": verifier,
            "nonce": nonce,
            "state": state,
        }

    async def verify_otp_and_get_identity(
        self,
        session: dict,
        individual_id: str,
        otp: str,
    ) -> str:
        """Step 4-7 of server-driven OAuth: authenticate → auth-code → tokens → validate.

        `session` is the dict returned by `start_otp_auth`.
        Returns the verified MOSIP individual_id (sub claim from id_token).
        """
        base = settings.ESIGNET_BASE_URL
        tx = session["transaction_id"]
        csrf = session["csrf_token"]
        cookies = session["cookies"]
        headers = {
            "X-XSRF-TOKEN": csrf,
            "oauth-details-key": session["oauth_details_key"],
            "oauth-details-hash": session["oauth_details_hash"],
        }

        async with httpx.AsyncClient() as client:
            # authenticate
            auth_payload = {
                "requestTime": _now_iso(),
                "request": {
                    "transactionId": tx,
                    "individualId": individual_id,
                    "challengeList": [
                        {
                            "authFactorType": "OTP",
                            "challenge": otp,
                            "format": "alpha-numeric",
                        }
                    ],
                },
            }
            r = await client.post(
                f"{base}/v1/esignet/authorization/v3/authenticate",
                json=auth_payload,
                headers=headers,
                cookies=cookies,
            )
            r.raise_for_status()
            body = r.json()
            if body.get("errors"):
                raise ValueError(f"authenticate failed: {body['errors']}")

            # auth-code
            ac_payload = {
                "requestTime": _now_iso(),
                "request": {
                    "transactionId": tx,
                    "acceptedClaims": [],
                    "permittedAuthorizeScopes": [],
                },
            }
            r = await client.post(
                f"{base}/v1/esignet/authorization/auth-code",
                json=ac_payload,
                headers=headers,
                cookies=cookies,
            )
            r.raise_for_status()
            ac_body = r.json()
            if ac_body.get("errors"):
                raise ValueError(f"auth-code failed: {ac_body['errors']}")
            code = ac_body["response"]["code"]

        # Exchange code + PKCE verifier for id_token
        token_response = await self.exchange_code(code, code_verifier=session["code_verifier"])
        id_token = token_response.get("id_token")
        if not id_token:
            raise ValueError("No id_token in token response")
        return self.get_individual_id(id_token, session["nonce"])


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with millis, matching eSignet's requestTime format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
