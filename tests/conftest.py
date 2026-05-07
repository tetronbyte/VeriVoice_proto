"""Shared fixtures for IVR flow tests.

Provides: MockRedis, in-memory SQLite, service mocks, TestClient, and
convenience fixtures for enrolled/consented citizens.
"""

import io
import time
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.db.crud import (
    create_citizen,
    create_consent_token,
    create_voice_template,
)

# How long to wait for background pipeline tasks (seconds).
# All I/O is mocked so pipelines complete almost instantly; this just
# lets the TestClient's background event-loop thread process them.
_BG_WAIT = 0.5


# ── Synthetic audio helper ──────────────────────────────────────────────────

def make_wav_bytes(freq: float = 440.0, duration: float = 2.0,
                   sample_rate: int = 16000) -> bytes:
    """Generate a synthetic WAV file as bytes (mono, 16 kHz, float32 sine wave)."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    audio = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="FLOAT")
    return buf.getvalue()


# ── TwiML parsing helper ────────────────────────────────────────────────────

def parse_twiml(content: str) -> ET.Element:
    """Parse TwiML XML, assert <Response> root, return it."""
    root = ET.fromstring(content)
    assert root.tag == "Response", f"Expected <Response>, got <{root.tag}>"
    return root


# ── MockRedis ────────────────────────────────────────────────────────────────

class MockRedis:
    """In-memory dict implementing the Redis methods used by webhook_handler."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value: str, **kwargs):
        self._store[key] = value

    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    def getdel(self, key: str):
        return self._store.pop(key, None)

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def clear(self):
        self._store.clear()


# ── In-memory SQLite engine (shared across all connections) ──────────────────

_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Enable WAL-like behaviour — ensure FK support
@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSessionLocal = sessionmaker(bind=_test_engine)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Core IVR client fixture ─────────────────────────────────────────────────

@pytest.fixture()
def mock_redis():
    """Return a fresh MockRedis instance (also exposed for direct inspection)."""
    return MockRedis()


@pytest.fixture()
def _mock_update_call():
    """AsyncMock for _update_call_twiml — captures TwiML sent to live calls."""
    return AsyncMock()


@pytest.fixture()
def ivr_client(mock_redis, _mock_update_call):
    """TestClient with all heavy services mocked; database routed to in-memory SQLite.

    Returns ``(client, mock_redis, mock_update_call)`` so tests can inspect
    Redis state and the TwiML that background pipelines sent to live calls.
    """
    from app.main import app  # local import to avoid import-time side effects

    # Re-create tables each test
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)

    # FastAPI DI override (for any route that uses Depends(get_db))
    app.dependency_overrides[get_db] = _override_get_db

    # ── Mock TTS service ────────────────────────────────────────────────
    mock_tts = MagicMock()
    mock_tts.synthesize_with_url.return_value = "http://test/tts/mock.mp3"

    # ── Mock Challenge service ──────────────────────────────────────────
    mock_challenge = MagicMock()
    mock_challenge.generate_challenge.return_value = {
        "challenge_id": "test-chal-001",
        "phrase_text": "The sun rises over the mountain every morning",
    }
    mock_challenge.match_transcript.return_value = {
        "match": True,
        "score": 0.90,
        "matched_words": 7,
        "total_words": 8,
    }

    # ── Mock Preprocessor ───────────────────────────────────────────────
    mock_preprocessor = MagicMock()
    mock_preprocessor.process.return_value = np.zeros(16000, dtype=np.float32)

    # ── Mock Encryption service ─────────────────────────────────────────
    mock_encryption = MagicMock()
    mock_encryption.encrypt_centroid.return_value = b'{"n":"123","dim":192,"ciphertexts":[]}'
    mock_encryption.deserialize_ciphertext.return_value = [MagicMock() for _ in range(192)]
    mock_encryption.private_key = MagicMock()

    # ── Mock Matching service ───────────────────────────────────────────
    mock_matching = MagicMock()
    mock_matching.match.return_value = {"score": 0.92, "granted": True}

    # ── Mock Consent service ────────────────────────────────────────────
    mock_consent = MagicMock()
    mock_consent.sign_consent.return_value = b"\x01" * 64

    # ── Mock MOSIP service ──────────────────────────────────────────────
    mock_mosip = MagicMock()
    mock_mosip.start_otp_auth = AsyncMock(return_value={
        "transaction_id": "txn-test-001",
        "oauth_details_key": "key",
        "oauth_details_hash": "hash",
        "code_verifier": "verifier",
        "nonce": "nonce",
        "state": "state",
        "cookies": {},
    })
    mock_mosip.verify_otp_and_get_identity = AsyncMock(
        return_value="MOSIP-IND-001"
    )

    # ── Mock EmbeddingService class ─────────────────────────────────────
    mock_embedding_cls = MagicMock()
    mock_embedding_instance = MagicMock()
    _fixed_emb = np.random.default_rng(42).standard_normal(192).astype(np.float32)
    _fixed_emb /= np.linalg.norm(_fixed_emb)
    mock_embedding_instance.extract_embedding.return_value = _fixed_emb.copy()
    mock_embedding_instance.compute_centroid.return_value = _fixed_emb.copy()
    mock_embedding_cls.return_value = mock_embedding_instance

    # ── Mock TranscriptionService class ─────────────────────────────────
    mock_transcription_cls = MagicMock()
    mock_transcription_instance = MagicMock()
    mock_transcription_instance.transcribe.return_value = "yes I agree"
    mock_transcription_cls.return_value = mock_transcription_instance

    # ── Mock download helper ────────────────────────────────────────────
    mock_download = AsyncMock(return_value=make_wav_bytes())

    # ── Mock Twilio validation ──────────────────────────────────────────
    mock_validate = AsyncMock()

    patches = [
        patch("twilio_integration.webhook_handler._redis_client", mock_redis),
        patch("twilio_integration.webhook_handler._tts_service", mock_tts),
        patch("twilio_integration.webhook_handler._challenge_service", mock_challenge),
        patch("twilio_integration.webhook_handler._preprocessor", mock_preprocessor),
        patch("twilio_integration.webhook_handler._encryption_service", mock_encryption),
        patch("twilio_integration.webhook_handler._matching_service", mock_matching),
        patch("twilio_integration.webhook_handler._consent_service", mock_consent),
        patch("twilio_integration.webhook_handler._mosip_service", mock_mosip),
        patch("twilio_integration.webhook_handler.SessionLocal", TestSessionLocal),
        patch("twilio_integration.webhook_handler._download_twilio_recording", mock_download),
        patch("twilio_integration.webhook_handler._update_call_twiml", _mock_update_call),
        patch("twilio_integration.webhook_handler._validate_twilio_request", mock_validate),
        patch("twilio_integration.webhook_handler.EmbeddingService", mock_embedding_cls),
        patch("twilio_integration.webhook_handler.TranscriptionService", mock_transcription_cls),
        patch("twilio_integration.webhook_handler.classify_yes_no", return_value="yes"),
        patch("twilio_integration.webhook_handler.parse_question_number", return_value=0),
    ]

    for p in patches:
        p.start()

    raw_client = TestClient(app)

    # Wrap client so that requests to callback/confirm endpoints
    # automatically wait for the background pipeline tasks to complete
    # on the TestClient's event-loop thread.
    class _PipelineAwareClient:
        """Thin wrapper: auto-waits after endpoints that spawn background tasks."""

        _BG_ENDPOINTS = ("/callback", "/confirm")

        def __init__(self, tc):
            self._tc = tc

        def post(self, url, **kwargs):
            resp = self._tc.post(url, **kwargs)
            if any(ep in url for ep in self._BG_ENDPOINTS):
                time.sleep(_BG_WAIT)
            return resp

        def get(self, url, **kwargs):
            return self._tc.get(url, **kwargs)

    client = _PipelineAwareClient(raw_client)

    yield client, mock_redis, _mock_update_call

    for p in reversed(patches):
        p.stop()

    app.dependency_overrides.clear()


# ── Convenience fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def test_db():
    """Provide a raw DB session for direct assertions (e.g. querying records)."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def enrolled_citizen(ivr_client):
    """Pre-create a Citizen + VoiceTemplate.

    Returns ``(client, mock_redis, mock_update_call, citizen_id, national_id)``.
    """
    client, mock_redis, mock_update_call = ivr_client
    db = TestSessionLocal()
    try:
        citizen = create_citizen(
            db,
            national_id_number="KE-TEST-001",
            preferred_language="en",
            phone_number="+254700000000",
        )
        ciphertext = b'{"n":"123","dim":192,"ciphertexts":[]}'
        create_voice_template(db, citizen_id=citizen.citizen_id, he_ciphertext=ciphertext)
        cid = str(citizen.citizen_id)
        nid = citizen.national_id_number
    finally:
        db.close()
    return client, mock_redis, mock_update_call, cid, nid


@pytest.fixture()
def consented_citizen(enrolled_citizen):
    """Pre-create a Citizen + VoiceTemplate + ConsentToken.

    Returns ``(client, mock_redis, mock_update_call, citizen_id, national_id, consent_token_id)``.
    """
    client, mock_redis, mock_update_call, cid, nid = enrolled_citizen
    db = TestSessionLocal()
    try:
        token = create_consent_token(
            db,
            citizen_id=cid,
            ministry_code="GOV",
            data_scope="service_access",
            digital_signature=b"\x01" * 64,
        )
        tid = str(token.token_id)
    finally:
        db.close()
    return client, mock_redis, mock_update_call, cid, nid, tid
