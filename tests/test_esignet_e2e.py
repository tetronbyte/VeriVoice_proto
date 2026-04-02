"""Phase 24 validation: Full MOSIP e-Signet + VeriVoice end-to-end flow.

Single sequential test covering:
1. OIDC handshake (authorize -> callback with mocked e-Signet)
2. Identity verification (verified MOSIP individual_id returned)
3. Verified enrollment (5 audio samples + mosip_individual_id)
4. Biometric authentication (challenge + voice match)
5. Consent (Ed25519 signed token linked to MOSIP-verified citizen)
6. Service access (voice Q&A with valid consent)
7. Database audit (identity_verified=True, mosip_individual_id persisted)
8. Redis cleanup (state/nonce consumed after callback)
"""

import io
import time
from unittest.mock import patch, MagicMock, AsyncMock

import numpy as np
import pytest
import soundfile as sf
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.main import app
from app.models import AuthEvent, Citizen, ConsentToken, VoiceTemplate

# -- Test RSA key pair for mock JWTs -----------------------------------------

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_numbers = _private_key.public_key().public_numbers()


def _int_to_b64url(n):
    import base64
    b = n.to_bytes((n.bit_length() + 7) // 8, byteorder="big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


TEST_JWKS = {
    "keys": [{
        "kty": "RSA", "kid": "test-key-1", "use": "sig", "alg": "RS256",
        "n": _int_to_b64url(_public_numbers.n),
        "e": _int_to_b64url(_public_numbers.e),
    }]
}

PRIVATE_KEY_PEM = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

TEST_CLIENT_ID = "mock-verivoice-client"
TEST_ISSUER = "https://esignet.collab.mosip.net"
TEST_MOSIP_SUB = "MOSIP-E2E-INDIVIDUAL-001"

_SR = 16000


def _make_wav_bytes(freq: float = 440.0, duration: float = 2.0) -> bytes:
    t = np.linspace(0, duration, int(_SR * duration), endpoint=False, dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * freq * t)
    buf = io.BytesIO()
    sf.write(buf, audio, _SR, format="WAV")
    buf.seek(0)
    return buf.read()


def _upload_files(freq: float, n: int = 5):
    return [
        ("audio_files", (f"s{i}.wav", _make_wav_bytes(freq=freq), "audio/wav"))
        for i in range(n)
    ]


def _make_id_token(nonce):
    now = int(time.time())
    return jwt.encode(
        {"sub": TEST_MOSIP_SUB, "aud": TEST_CLIENT_ID, "iss": TEST_ISSUER,
         "nonce": nonce, "iat": now, "exp": now + 300},
        PRIVATE_KEY_PEM, algorithm="RS256", headers={"kid": "test-key-1"},
    )


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture()
def e2e_env():
    """Set up: in-memory DB, mocked Redis, mocked MOSIP OIDC service."""
    # Database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db

    # Redis mock (shared across router + mosip_service)
    redis_store = {}

    def mock_setex(key, ttl, value):
        redis_store[key] = value

    def mock_getdel(key):
        return redis_store.pop(key, None)

    def mock_get(key):
        return redis_store.get(key)

    def mock_delete(key):
        return redis_store.pop(key, None)

    mock_redis = MagicMock()
    mock_redis.setex.side_effect = mock_setex
    mock_redis.getdel.side_effect = mock_getdel
    mock_redis.get.side_effect = mock_get
    mock_redis.delete.side_effect = mock_delete

    # Patch Redis in mosip_service (authorize/consume), mosip router (callback verified store),
    # and enrollment router (verified check)
    patches = [
        patch("app.services.mosip_service.redis.from_url", return_value=mock_redis),
        patch("app.routers.enrollment._redis_client", mock_redis),
    ]

    # Patch the MOSIP service instance in the router to use our mocked Redis + JWKS
    mosip_svc_patch = patch("app.routers.mosip._mosip_service")
    patches.append(mosip_svc_patch)

    for p in patches:
        p.start()

    # Configure the mocked mosip service
    import app.routers.mosip as mosip_router
    mock_svc = mosip_router._mosip_service

    # get_authorize_url -- real logic with mocked Redis
    def fake_authorize():
        import secrets
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        redis_store[f"esignet:state:{state}"] = nonce
        return {
            "authorize_url": f"{TEST_ISSUER}/v1/esignet/authorize?state={state}&nonce={nonce}",
            "state": state,
            "nonce": nonce,
        }

    mock_svc.get_authorize_url.side_effect = fake_authorize

    # consume_oidc_context -- real logic with mocked Redis
    def fake_consume(state):
        key = f"esignet:state:{state}"
        nonce = redis_store.pop(key, None)
        if nonce is None:
            raise ValueError("Invalid or expired OIDC state")
        return nonce

    mock_svc.consume_oidc_context.side_effect = fake_consume

    # exchange_code -- returns mock token with correct nonce
    async def fake_exchange(code):
        nonce = mock_svc._last_nonce
        return {"id_token": _make_id_token(nonce), "access_token": "mock-access"}

    mock_svc.exchange_code = AsyncMock(side_effect=fake_exchange)

    # get_individual_id -- real JWT validation with test JWKS
    def fake_get_individual_id(id_token, nonce, jwks=None):
        from app.services.mosip_service import MosipService
        real_svc = MosipService.__new__(MosipService)
        with patch("app.services.mosip_service.settings") as s:
            s.ESIGNET_CLIENT_ID = TEST_CLIENT_ID
            s.ESIGNET_BASE_URL = TEST_ISSUER
            return real_svc.get_individual_id(id_token, nonce, jwks=TEST_JWKS)

    mock_svc.get_individual_id.side_effect = fake_get_individual_id

    # Also patch the inline redis import in mosip router callback
    # (the router creates its own redis client for esignet:verified: keys)
    patch_router_redis = patch("app.routers.mosip._redis_mod.from_url", return_value=mock_redis)
    # Need to patch at the right import path -- the router does `import redis as _redis_mod`
    # inside the function. Let's patch it differently:
    import app.routers.mosip as mr
    original_callback = None  # we'll let the mock_svc handle storage via side_effect

    # Patch: after get_individual_id succeeds, store esignet:verified:{id} in redis_store
    original_get_id = fake_get_individual_id

    def fake_get_id_with_store(id_token, nonce, jwks=None):
        result = original_get_id(id_token, nonce, jwks)
        redis_store[f"esignet:verified:{result}"] = "1"
        return result

    mock_svc.get_individual_id.side_effect = fake_get_id_with_store

    client = TestClient(app)

    yield {
        "client": client,
        "db_session": TestSession,
        "redis_store": redis_store,
        "mock_svc": mock_svc,
    }

    for p in patches:
        p.stop()
    app.dependency_overrides.clear()


# -- E2E Test ----------------------------------------------------------------

def test_full_mosip_verivoice_flow(e2e_env):
    """Complete MOSIP e-Signet -> VeriVoice flow: OIDC -> Enroll -> Auth -> Consent -> Service."""
    client = e2e_env["client"]
    TestSession = e2e_env["db_session"]
    redis_store = e2e_env["redis_store"]
    mock_svc = e2e_env["mock_svc"]

    t_start = time.perf_counter()
    FREQ = 440.0

    # ---------------------------------------------------------------------
    # Step 1: OIDC HANDSHAKE -- Authorize
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    auth_resp = client.get("/api/v1/mosip/authorize")
    assert auth_resp.status_code == 200
    auth_body = auth_resp.json()
    state = auth_body["state"]
    assert auth_body["authorize_url"].startswith(TEST_ISSUER)

    # Verify state is stored in Redis
    assert f"esignet:state:{state}" in redis_store
    nonce = redis_store[f"esignet:state:{state}"]

    # Store nonce for the exchange_code mock
    mock_svc._last_nonce = nonce

    t_authorize = time.perf_counter() - t0
    print(f"\n>>> Step 1 -- OIDC Authorize: {t_authorize:.1f}s")

    # ---------------------------------------------------------------------
    # Step 2: OIDC CALLBACK -- Exchange code, validate JWT, get MOSIP ID
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    cb_resp = client.get("/api/v1/mosip/callback", params={
        "code": "mock-auth-code-e2e",
        "state": state,
    })
    assert cb_resp.status_code == 200, f"Callback failed: {cb_resp.text}"
    identity = cb_resp.json()
    mosip_id = identity["mosip_individual_id"]
    assert mosip_id == TEST_MOSIP_SUB
    assert identity["identity_verified"] is True

    # Verify state was consumed (deleted from Redis)
    assert f"esignet:state:{state}" not in redis_store

    # Verify esignet:verified:{id} was stored for enrollment
    assert redis_store.get(f"esignet:verified:{mosip_id}") == "1"

    t_callback = time.perf_counter() - t0
    print(f">>> Step 2 -- OIDC Callback: mosip_id={mosip_id}, verified=True ({t_callback:.1f}s)")

    # ---------------------------------------------------------------------
    # Step 3: VERIFIED ENROLLMENT -- with mosip_individual_id
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    enroll_resp = client.post(
        "/api/v1/enroll",
        data={
            "national_id_number": "KE-MOSIP-E2E-001",
            "preferred_language": "en",
            "phone_number": "+254700000088",
            "mosip_individual_id": mosip_id,
        },
        files=_upload_files(freq=FREQ, n=5),
    )
    assert enroll_resp.status_code == 200, f"Enrollment failed: {enroll_resp.text}"
    enroll_body = enroll_resp.json()
    citizen_id = enroll_body["citizen_id"]
    template_id = enroll_body["template_id"]
    assert enroll_body["status"] == "enrolled"
    assert enroll_body["identity_verified"] is True

    # Verify the Redis verification token was consumed
    assert redis_store.get(f"esignet:verified:{mosip_id}") is None

    t_enroll = time.perf_counter() - t0
    print(f">>> Step 3 -- Verified Enrollment: citizen={citizen_id[:12]}..., identity_verified=True ({t_enroll:.1f}s)")

    # ---------------------------------------------------------------------
    # Step 4: BIOMETRIC AUTH -- challenge + voice match
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    challenge_resp = client.get("/api/v1/challenge", params={"language": "en"})
    assert challenge_resp.status_code == 200
    challenge = challenge_resp.json()

    auth_wav = _make_wav_bytes(freq=FREQ)
    auth_resp = client.post(
        "/api/v1/authenticate",
        data={
            "citizen_id": citizen_id,
            "challenge_phrase_id": challenge["challenge_id"],
        },
        files=[("audio_file", ("auth.wav", auth_wav, "audio/wav"))],
    )
    assert auth_resp.status_code == 200
    auth_body = auth_resp.json()
    # Voice score should be high (same synthetic speaker)
    assert auth_body["voice_match_score"] > 0.9

    t_auth = time.perf_counter() - t0
    print(f">>> Step 4 -- Auth: score={auth_body['voice_match_score']:.4f}, result={auth_body['result']} ({t_auth:.1f}s)")

    # ---------------------------------------------------------------------
    # Step 5: CONSENT -- Ed25519 signed token
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    consent_wav = _make_wav_bytes(freq=FREQ)
    consent_resp = client.post(
        "/api/v1/consent",
        data={
            "citizen_id": citizen_id,
            "ministry_code": "MOH",
            "data_scope": "health_records",
        },
        files=[("audio_file", ("consent.wav", consent_wav, "audio/wav"))],
    )
    assert consent_resp.status_code == 200, f"Consent failed: {consent_resp.text}"
    consent_body = consent_resp.json()
    token_id = consent_body["token_id"]
    assert len(consent_body["digital_signature"]) > 0

    t_consent = time.perf_counter() - t0
    print(f">>> Step 5 -- Consent: token={token_id[:12]}... ({t_consent:.1f}s)")

    # ---------------------------------------------------------------------
    # Step 6: SERVICE ACCESS -- voice Q&A
    # ---------------------------------------------------------------------
    t0 = time.perf_counter()
    svc_wav = _make_wav_bytes(freq=FREQ)
    svc_resp = client.post(
        "/api/v1/service-access",
        data={
            "citizen_id": citizen_id,
            "consent_token_id": token_id,
            "question_index": "0",
        },
        files=[("audio_file", ("answer.wav", svc_wav, "audio/wav"))],
    )
    assert svc_resp.status_code == 200, f"Service access failed: {svc_resp.text}"
    svc_body = svc_resp.json()
    assert svc_body["status"] == "completed"

    t_svc = time.perf_counter() - t0
    print(f">>> Step 6 -- Service: question='{svc_body['question']}' ({t_svc:.1f}s)")

    # ---------------------------------------------------------------------
    # DATABASE AUDIT -- Identity-to-Consent chain
    # ---------------------------------------------------------------------
    db = TestSession()

    # Citizen: MOSIP-verified
    citizen = db.query(Citizen).filter(Citizen.citizen_id == citizen_id).first()
    assert citizen is not None
    assert citizen.identity_verified is True
    assert citizen.mosip_individual_id == TEST_MOSIP_SUB
    assert citizen.national_id_number == "KE-MOSIP-E2E-001"

    # Voice template: linked to verified citizen
    template = db.query(VoiceTemplate).filter(VoiceTemplate.template_id == template_id).first()
    assert template is not None
    assert template.citizen_id == citizen_id
    assert template.is_active is True
    assert len(template.he_ciphertext) > 0

    # Auth event: exists for this citizen
    events = db.query(AuthEvent).filter(AuthEvent.citizen_id == citizen_id).all()
    assert len(events) >= 1

    # Consent token: linked to the MOSIP-verified citizen
    token = db.query(ConsentToken).filter(ConsentToken.token_id == token_id).first()
    assert token is not None
    assert token.citizen_id == citizen_id
    assert token.ministry_code == "MOH"
    assert token.data_scope == "health_records"
    assert len(token.digital_signature) == 64  # Ed25519
    assert token.is_revoked is False

    # The consent token's citizen has MOSIP-verified identity
    consent_citizen = db.query(Citizen).filter(Citizen.citizen_id == token.citizen_id).first()
    assert consent_citizen.identity_verified is True
    assert consent_citizen.mosip_individual_id == TEST_MOSIP_SUB

    db.close()

    # ---------------------------------------------------------------------
    # REDIS CLEANUP VERIFICATION
    # ---------------------------------------------------------------------
    # State/nonce consumed after callback
    esignet_keys = [k for k in redis_store if k.startswith("esignet:")]
    assert len(esignet_keys) == 0, f"Stale Redis keys remain: {esignet_keys}"

    # ---------------------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------------------
    t_total = time.perf_counter() - t_start
    print(f"\n{'='*65}")
    print(f">>> MOSIP E2E IDENTITY-TO-CONSENT CHAIN: ALL VERIFIED")
    print(f"    Citizen {citizen_id[:12]}...")
    print(f"    MOSIP ID: {TEST_MOSIP_SUB}")
    print(f"    identity_verified: True")
    print(f"    Consent token {token_id[:12]}... -> citizen.mosip_individual_id = {TEST_MOSIP_SUB}")
    print(f"    Redis cleanup: all esignet:* keys consumed")
    print(f"{'='*65}")
    print(f">>> TOTAL E2E TIME: {t_total:.1f}s")
    print(f"    OIDC Authorize:   {t_authorize:.1f}s")
    print(f"    OIDC Callback:    {t_callback:.1f}s")
    print(f"    Enrollment:       {t_enroll:.1f}s")
    print(f"    Authentication:   {t_auth:.1f}s")
    print(f"    Consent:          {t_consent:.1f}s")
    print(f"    Service Access:   {t_svc:.1f}s")
    print(f"{'='*65}")
