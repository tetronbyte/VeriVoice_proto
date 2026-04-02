"""Phase 21 validation: e-Signet OIDC router — authorize, callback, link."""

import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from jose import jwt

from app.db.database import Base
from app.main import app

# ── Test RSA key pair (reusable) ────────────────────────────────────────────

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
TEST_SUBJECT = "MOSIP-IND-TEST-001"


def _make_id_token(nonce, sub=TEST_SUBJECT):
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "aud": TEST_CLIENT_ID, "iss": TEST_ISSUER,
         "nonce": nonce, "iat": now, "exp": now + 300},
        PRIVATE_KEY_PEM, algorithm="RS256", headers={"kid": "test-key-1"},
    )


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    """TestClient with in-memory SQLite (shared connection) and mocked Redis."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.database import get_db

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

    test_client = TestClient(app)
    test_client._test_session = TestSession

    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def mock_mosip_service():
    """Patch the MosipService instance used by the router."""
    # In-memory Redis simulation
    store = {}

    def mock_setex(key, ttl, value):
        store[key] = value

    def mock_getdel(key):
        return store.pop(key, None)

    with patch("app.routers.mosip._mosip_service") as mock_svc:
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = mock_setex
        mock_redis.getdel.side_effect = mock_getdel
        mock_svc._redis = mock_redis
        mock_svc._store = store  # expose for test assertions

        # get_authorize_url — use real logic with mocked Redis
        def fake_authorize():
            import secrets
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            store[f"esignet:state:{state}"] = nonce
            return {
                "authorize_url": f"{TEST_ISSUER}/v1/esignet/authorize?state={state}&nonce={nonce}",
                "state": state,
                "nonce": nonce,
            }

        mock_svc.get_authorize_url.side_effect = fake_authorize

        # consume_oidc_context — use real logic with mocked Redis
        def fake_consume(state):
            key = f"esignet:state:{state}"
            nonce = store.pop(key, None)
            if nonce is None:
                raise ValueError("Invalid or expired OIDC state")
            return nonce

        mock_svc.consume_oidc_context.side_effect = fake_consume

        # exchange_code — returns a mock token response (async)
        async def fake_exchange(code):
            # The nonce will be set by the test via _last_nonce
            nonce = mock_svc._last_nonce
            return {"id_token": _make_id_token(nonce), "access_token": "mock-access"}

        mock_svc.exchange_code = AsyncMock(side_effect=fake_exchange)

        # get_individual_id — real JWT validation with test JWKS
        def fake_get_individual_id(id_token, nonce, jwks=None):
            from app.services.mosip_service import MosipService
            real_svc = MosipService.__new__(MosipService)
            with patch("app.services.mosip_service.settings") as s:
                s.ESIGNET_CLIENT_ID = TEST_CLIENT_ID
                s.ESIGNET_BASE_URL = TEST_ISSUER
                return real_svc.get_individual_id(id_token, nonce, jwks=TEST_JWKS)

        mock_svc.get_individual_id.side_effect = fake_get_individual_id

        yield mock_svc


# ── GET /api/v1/mosip/authorize ─────────────────────────────────────────────

class TestMosipAuthorize:
    def test_returns_authorize_url_and_state(self, client, mock_mosip_service):
        resp = client.get("/api/v1/mosip/authorize")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorize_url" in data
        assert "state" in data
        assert data["authorize_url"].startswith(TEST_ISSUER)
        assert "state=" in data["authorize_url"]


# ── GET /api/v1/mosip/callback ──────────────────────────────────────────────

class TestMosipCallback:
    def test_valid_callback_returns_identity(self, client, mock_mosip_service):
        # Step 1: Authorize to get state+nonce
        auth_resp = client.get("/api/v1/mosip/authorize")
        state = auth_resp.json()["state"]
        nonce = mock_mosip_service._store.get(f"esignet:state:{state}")
        # Store nonce for the exchange_code mock
        mock_mosip_service._last_nonce = nonce

        # Step 2: Callback
        resp = client.get("/api/v1/mosip/callback", params={"code": "auth-code-123", "state": state})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mosip_individual_id"] == TEST_SUBJECT
        assert data["identity_verified"] is True

    def test_invalid_state_returns_400(self, client, mock_mosip_service):
        resp = client.get("/api/v1/mosip/callback", params={"code": "auth-code-123", "state": "bogus-state"})
        assert resp.status_code == 400
        assert "Invalid or expired" in resp.json()["detail"]

    def test_replayed_state_returns_400(self, client, mock_mosip_service):
        # Authorize
        auth_resp = client.get("/api/v1/mosip/authorize")
        state = auth_resp.json()["state"]
        nonce = mock_mosip_service._store.get(f"esignet:state:{state}")
        mock_mosip_service._last_nonce = nonce

        # First callback — should succeed
        resp1 = client.get("/api/v1/mosip/callback", params={"code": "code-1", "state": state})
        assert resp1.status_code == 200

        # Replay — same state, should fail
        resp2 = client.get("/api/v1/mosip/callback", params={"code": "code-2", "state": state})
        assert resp2.status_code == 400

    def test_missing_code_returns_422(self, client, mock_mosip_service):
        resp = client.get("/api/v1/mosip/callback", params={"state": "some-state"})
        assert resp.status_code == 422

    def test_missing_state_returns_422(self, client, mock_mosip_service):
        resp = client.get("/api/v1/mosip/callback", params={"code": "some-code"})
        assert resp.status_code == 422


# ── POST /api/v1/mosip/link ─────────────────────────────────────────────────

class TestMosipLink:
    def _create_citizen(self, client, national_id, phone="+254700000099"):
        """Helper: create a citizen directly in DB and return citizen_id."""
        from app.db.crud import create_citizen
        db = client._test_session()
        citizen = create_citizen(db, national_id_number=national_id, phone_number=phone)
        cid = citizen.citizen_id
        db.close()
        return cid

    def test_link_success(self, client, mock_mosip_service):
        cid = self._create_citizen(client, "KE-LINK-001")
        resp = client.post("/api/v1/mosip/link", json={
            "citizen_id": cid,
            "mosip_individual_id": "MOSIP-LINK-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["citizen_id"] == cid
        assert data["mosip_individual_id"] == "MOSIP-LINK-001"
        assert data["identity_verified"] is True
        assert "linked_at" in data

    def test_link_nonexistent_citizen_returns_404(self, client, mock_mosip_service):
        resp = client.post("/api/v1/mosip/link", json={
            "citizen_id": "nonexistent-id",
            "mosip_individual_id": "MOSIP-LINK-002",
        })
        assert resp.status_code == 404

    def test_link_duplicate_mosip_id_returns_409(self, client, mock_mosip_service):
        cid1 = self._create_citizen(client, "KE-DUP-001")
        resp1 = client.post("/api/v1/mosip/link", json={
            "citizen_id": cid1,
            "mosip_individual_id": "MOSIP-DUP-001",
        })
        assert resp1.status_code == 200

        cid2 = self._create_citizen(client, "KE-DUP-002", phone="+254700000098")
        resp2 = client.post("/api/v1/mosip/link", json={
            "citizen_id": cid2,
            "mosip_individual_id": "MOSIP-DUP-001",
        })
        assert resp2.status_code == 409


# ── Full Flow: authorize → callback → link ──────────────────────────────────

class TestFullOIDCFlow:
    def test_authorize_callback_link_e2e(self, client, mock_mosip_service):
        # Create a citizen first
        from app.db.crud import create_citizen
        db = client._test_session()
        citizen = create_citizen(db, national_id_number="KE-E2E-FLOW", phone_number="+254700000077")
        cid = citizen.citizen_id
        assert citizen.identity_verified is False
        db.close()

        # Step 1: Authorize
        auth_resp = client.get("/api/v1/mosip/authorize")
        assert auth_resp.status_code == 200
        state = auth_resp.json()["state"]

        # Grab nonce for token mock
        nonce = mock_mosip_service._store.get(f"esignet:state:{state}")
        mock_mosip_service._last_nonce = nonce

        # Step 2: Callback
        cb_resp = client.get("/api/v1/mosip/callback", params={"code": "real-code", "state": state})
        assert cb_resp.status_code == 200
        mosip_id = cb_resp.json()["mosip_individual_id"]
        assert mosip_id == TEST_SUBJECT

        # Step 3: Link
        link_resp = client.post("/api/v1/mosip/link", json={
            "citizen_id": cid,
            "mosip_individual_id": mosip_id,
        })
        assert link_resp.status_code == 200
        link_data = link_resp.json()
        assert link_data["identity_verified"] is True
        assert link_data["mosip_individual_id"] == TEST_SUBJECT
        assert link_data["citizen_id"] == cid
