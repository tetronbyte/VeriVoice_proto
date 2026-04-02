"""Phase 19 validation: MosipService — OIDC authorize, JWT validation, replay protection."""

import time
from unittest.mock import patch, MagicMock

import pytest
from jose import jwt, JWTError
from jose.backends import RSAKey
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.services.mosip_service import MosipService


# ── Test RSA Key Pair (for signing mock JWTs) ───────────────────────────────

def _generate_test_rsa_keypair():
    """Generate an RSA key pair and return (private_key, jwks_dict)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # Build JWKS from public key
    pub_numbers = public_key.public_numbers()

    def _int_to_b64url(n, length=None):
        b = n.to_bytes((n.bit_length() + 7) // 8, byteorder="big")
        import base64
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-key-1",
                "use": "sig",
                "alg": "RS256",
                "n": _int_to_b64url(pub_numbers.n),
                "e": _int_to_b64url(pub_numbers.e),
            }
        ]
    }

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return pem, jwks


PRIVATE_KEY_PEM, TEST_JWKS = _generate_test_rsa_keypair()

# Test constants matching the mock config
TEST_CLIENT_ID = "mock-verivoice-client"
TEST_ISSUER = "https://esignet.collab.mosip.net"
TEST_NONCE = "test-nonce-abc123"
TEST_SUBJECT = "MOSIP-IND-12345"


def _make_id_token(
    sub=TEST_SUBJECT,
    aud=TEST_CLIENT_ID,
    iss=TEST_ISSUER,
    nonce=TEST_NONCE,
    exp_offset=300,
    private_key=PRIVATE_KEY_PEM,
):
    """Create a signed RS256 JWT with the given claims."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "nonce": nonce,
        "iat": now,
        "exp": now + exp_offset,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key-1"})


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def mosip_service():
    """MosipService with a mocked Redis backend."""
    with patch("app.services.mosip_service.redis") as mock_redis_mod:
        mock_redis = MagicMock()
        mock_redis_mod.from_url.return_value = mock_redis

        # In-memory store to simulate Redis
        store = {}

        def mock_setex(key, ttl, value):
            store[key] = value

        def mock_getdel(key):
            return store.pop(key, None)

        mock_redis.setex.side_effect = mock_setex
        mock_redis.getdel.side_effect = mock_getdel

        with patch("app.services.mosip_service.settings") as mock_settings:
            mock_settings.ESIGNET_BASE_URL = TEST_ISSUER
            mock_settings.ESIGNET_CLIENT_ID = TEST_CLIENT_ID
            mock_settings.ESIGNET_CLIENT_SECRET = "test-secret"
            mock_settings.ESIGNET_REDIRECT_URI = "http://localhost:8000/api/v1/mosip/callback"
            mock_settings.ESIGNET_SCOPES = "openid profile"
            mock_settings.ESIGNET_JWKS_URI = f"{TEST_ISSUER}/.well-known/jwks.json"
            mock_settings.REDIS_URL = "redis://localhost:6379/0"

            svc = MosipService()
            svc._store = store  # expose for assertions
            yield svc


# ── Authorize URL ───────────────────────────────────────────────────────────

class TestAuthorizeUrl:
    def test_authorize_url_well_formed(self, mosip_service):
        result = mosip_service.get_authorize_url()

        assert "authorize_url" in result
        assert "state" in result
        assert "nonce" in result

        url = result["authorize_url"]
        assert url.startswith(f"{TEST_ISSUER}/v1/esignet/authorize?")
        assert "response_type=code" in url
        assert f"client_id={TEST_CLIENT_ID}" in url
        assert "scope=openid+profile" in url or "scope=openid%20profile" in url or "scope=openid profile" in url
        assert f"state={result['state']}" in url
        assert f"nonce={result['nonce']}" in url

    def test_authorize_stores_oidc_context(self, mosip_service):
        result = mosip_service.get_authorize_url()
        key = f"esignet:state:{result['state']}"
        assert key in mosip_service._store
        assert mosip_service._store[key] == result["nonce"]


# ── JWT Validation (Positive) ──────────────────────────────────────────────

class TestValidateIdToken:
    def test_valid_token_returns_claims(self, mosip_service):
        token = _make_id_token()
        claims = mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

        assert claims["sub"] == TEST_SUBJECT
        assert claims["aud"] == TEST_CLIENT_ID
        assert claims["iss"] == TEST_ISSUER
        assert claims["nonce"] == TEST_NONCE

    def test_get_individual_id_returns_sub(self, mosip_service):
        token = _make_id_token()
        individual_id = mosip_service.get_individual_id(token, nonce=TEST_NONCE, jwks=TEST_JWKS)
        assert individual_id == TEST_SUBJECT


# ── JWT Validation (Negative) ──────────────────────────────────────────────

class TestValidateIdTokenNegative:
    def test_expired_token_raises(self, mosip_service):
        token = _make_id_token(exp_offset=-60)  # expired 60s ago
        with pytest.raises(JWTError):
            mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_wrong_audience_raises(self, mosip_service):
        token = _make_id_token(aud="wrong-client-id")
        with pytest.raises(JWTError):
            mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_wrong_issuer_raises(self, mosip_service):
        token = _make_id_token(iss="https://evil.example.com")
        with pytest.raises(JWTError):
            mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_wrong_nonce_raises(self, mosip_service):
        token = _make_id_token(nonce="wrong-nonce")
        with pytest.raises(ValueError, match="Nonce mismatch"):
            mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_tampered_signature_raises(self, mosip_service):
        token = _make_id_token()
        # Corrupt the signature (last segment of the JWT)
        parts = token.rsplit(".", 1)
        tampered = parts[0] + ".AAAA_tampered_signature"
        with pytest.raises(JWTError):
            mosip_service.validate_id_token(tampered, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_token_signed_with_different_key_raises(self, mosip_service):
        other_pem, _ = _generate_test_rsa_keypair()
        token = _make_id_token(private_key=other_pem)
        # Validate against the original JWKS — signature won't match
        with pytest.raises(JWTError):
            mosip_service.validate_id_token(token, nonce=TEST_NONCE, jwks=TEST_JWKS)

    def test_missing_sub_raises(self, mosip_service):
        token = _make_id_token(sub=None)
        # jose rejects non-string sub at decode time
        with pytest.raises((JWTError, ValueError)):
            mosip_service.get_individual_id(token, nonce=TEST_NONCE, jwks=TEST_JWKS)


# ── Replay Protection (Redis state/nonce) ──────────────────────────────────

class TestReplayProtection:
    def test_consume_valid_state(self, mosip_service):
        mosip_service.store_oidc_context("state-1", "nonce-1")
        nonce = mosip_service.consume_oidc_context("state-1")
        assert nonce == "nonce-1"

    def test_reusing_state_raises(self, mosip_service):
        mosip_service.store_oidc_context("state-2", "nonce-2")
        mosip_service.consume_oidc_context("state-2")  # first use OK
        with pytest.raises(ValueError, match="Invalid or expired"):
            mosip_service.consume_oidc_context("state-2")  # second use fails

    def test_unknown_state_raises(self, mosip_service):
        with pytest.raises(ValueError, match="Invalid or expired"):
            mosip_service.consume_oidc_context("never-stored")

    def test_full_flow_replay_rejected(self, mosip_service):
        """Simulate a full authorize→callback flow, then replay the callback."""
        # Step 1: Authorize — generates state+nonce, stores in Redis
        auth_result = mosip_service.get_authorize_url()
        state = auth_result["state"]
        nonce = auth_result["nonce"]

        # Step 2: Callback — consume state, validate token
        consumed_nonce = mosip_service.consume_oidc_context(state)
        assert consumed_nonce == nonce

        token = _make_id_token(nonce=nonce)
        claims = mosip_service.validate_id_token(token, nonce=consumed_nonce, jwks=TEST_JWKS)
        assert claims["sub"] == TEST_SUBJECT

        # Step 3: Replay — same state should be rejected
        with pytest.raises(ValueError, match="Invalid or expired"):
            mosip_service.consume_oidc_context(state)
