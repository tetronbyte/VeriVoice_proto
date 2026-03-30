"""Phase 11 validation: POST /api/v1/enroll end-to-end via TestClient."""

import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app

# ── In-memory SQLite for tests (with cross-thread support) ───────────────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

# Share a single connection across threads for in-memory SQLite
_connection = _engine.connect()
Base.metadata.create_all(_connection)

_TestSession = sessionmaker(bind=_connection)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

client = TestClient(app)


def _make_wav_bytes(freq: float = 440.0, duration: float = 2.0, sr: int = 16000) -> bytes:
    """Generate a synthetic sine wave as WAV bytes."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * freq * t)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    buf.seek(0)
    return buf.read()


def _enrollment_files(n: int = 5) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Create n synthetic WAV upload tuples for multipart form."""
    freqs = [440.0, 500.0, 550.0, 600.0, 660.0]
    return [
        ("audio_files", (f"sample_{i}.wav", _make_wav_bytes(freq=freqs[i % len(freqs)]), "audio/wav"))
        for i in range(n)
    ]


class TestEnrollSuccess:
    def test_returns_200_with_ids(self):
        resp = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-SUCCESS-001",
                "preferred_language": "en",
                "phone_number": "+254700000001",
            },
            files=_enrollment_files(5),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "citizen_id" in body
        assert "template_id" in body
        assert body["status"] == "enrolled"
        assert "enrolled_at" in body

    def test_voice_template_stored_in_db(self):
        resp = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-DB-CHECK-001",
                "preferred_language": "sw",
                "phone_number": "+254700000002",
            },
            files=_enrollment_files(5),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Check DB directly
        db = _TestSession()
        from app.db.crud import get_active_template
        template = get_active_template(db, body["citizen_id"])
        assert template is not None
        assert len(template.he_ciphertext) > 0
        db.close()


class TestEnrollErrors:
    def test_duplicate_national_id_returns_409(self):
        # First enrollment
        resp1 = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-DUP-001",
                "preferred_language": "en",
                "phone_number": "+254700000003",
            },
            files=_enrollment_files(5),
        )
        assert resp1.status_code == 200

        # Second enrollment with same national_id
        resp2 = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-DUP-001",
                "preferred_language": "en",
                "phone_number": "+254700000004",
            },
            files=_enrollment_files(5),
        )
        assert resp2.status_code == 409

    def test_wrong_audio_count_returns_400(self):
        resp = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-BAD-COUNT",
                "preferred_language": "en",
                "phone_number": "+254700000005",
            },
            files=_enrollment_files(3),  # only 3, need 5
        )
        assert resp.status_code == 400

    def test_zero_audio_files_returns_400(self):
        resp = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-ZERO-AUDIO",
                "preferred_language": "en",
                "phone_number": "+254700000006",
            },
            files=[],
        )
        assert resp.status_code == 400
