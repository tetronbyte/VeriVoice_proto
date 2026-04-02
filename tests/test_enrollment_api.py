"""Phase 11 + Phase 22 validation: POST /api/v1/enroll end-to-end via TestClient."""

import io
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.main import app

# ── In-memory SQLite for tests ──────────────────────────────────────────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)

_TestSession = sessionmaker(bind=_engine)


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


# ── Phase 22: Identity-Verified Enrollment (MOSIP) ──────────────────────────

class TestMosipEnrollment:
    """Enrollment with optional mosip_individual_id for e-Signet verified identity."""

    def _mock_redis(self, verified_ids=None):
        """Return a patch context that mocks the Redis client in the enrollment router."""
        store = {}
        if verified_ids:
            for mid in verified_ids:
                store[f"esignet:verified:{mid}"] = "1"

        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: store.get(key)
        mock_redis.delete.side_effect = lambda key: store.pop(key, None)

        return patch("app.routers.enrollment._redis_client", mock_redis), store

    def test_enroll_with_mosip_id_verified(self):
        """Enrollment with a valid mosip_individual_id sets identity_verified=True."""
        redis_patch, _ = self._mock_redis(verified_ids=["MOSIP-ENROLL-001"])

        with redis_patch:
            resp = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-MOSIP-ENROLL-001",
                    "preferred_language": "en",
                    "phone_number": "+254700000010",
                    "mosip_individual_id": "MOSIP-ENROLL-001",
                },
                files=_enrollment_files(5),
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["identity_verified"] is True
        assert body["status"] == "enrolled"

    def test_enroll_without_mosip_id_unverified(self):
        """Enrollment without mosip_individual_id defaults to identity_verified=False."""
        resp = client.post(
            "/api/v1/enroll",
            data={
                "national_id_number": "KE-NO-MOSIP-001",
                "preferred_language": "en",
                "phone_number": "+254700000011",
            },
            files=_enrollment_files(5),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["identity_verified"] is False

    def test_enroll_with_invalid_mosip_session_returns_400(self):
        """Enrollment with a mosip_individual_id not in Redis returns 400."""
        redis_patch, _ = self._mock_redis(verified_ids=[])  # nothing in Redis

        with redis_patch:
            resp = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-BAD-MOSIP-001",
                    "preferred_language": "en",
                    "phone_number": "+254700000012",
                    "mosip_individual_id": "MOSIP-NOT-VERIFIED",
                },
                files=_enrollment_files(5),
            )

        assert resp.status_code == 400
        assert "Invalid or expired" in resp.json()["detail"]

    def test_enroll_mosip_id_consumed_after_use(self):
        """The Redis verification token is consumed — second enrollment with same MOSIP ID fails."""
        redis_patch, store = self._mock_redis(verified_ids=["MOSIP-CONSUME-001"])

        with redis_patch:
            # First enrollment — consumes the token
            resp1 = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-CONSUME-001",
                    "preferred_language": "en",
                    "phone_number": "+254700000013",
                    "mosip_individual_id": "MOSIP-CONSUME-001",
                },
                files=_enrollment_files(5),
            )
            assert resp1.status_code == 200

            # Second enrollment with same MOSIP ID — token already consumed
            resp2 = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-CONSUME-002",
                    "preferred_language": "en",
                    "phone_number": "+254700000014",
                    "mosip_individual_id": "MOSIP-CONSUME-001",
                },
                files=_enrollment_files(5),
            )
            assert resp2.status_code == 400

    def test_enroll_duplicate_mosip_id_returns_409(self):
        """Two different citizens cannot share the same mosip_individual_id."""
        redis_patch, store = self._mock_redis(verified_ids=["MOSIP-DUP-ENROLL"])

        with redis_patch:
            # First enrollment
            resp1 = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-DUP-MOSIP-001",
                    "preferred_language": "en",
                    "phone_number": "+254700000015",
                    "mosip_individual_id": "MOSIP-DUP-ENROLL",
                },
                files=_enrollment_files(5),
            )
            assert resp1.status_code == 200

            # Re-add to Redis to simulate a new e-Signet session
            store[f"esignet:verified:MOSIP-DUP-ENROLL"] = "1"

            # Second enrollment with same MOSIP ID, different national_id
            resp2 = client.post(
                "/api/v1/enroll",
                data={
                    "national_id_number": "KE-DUP-MOSIP-002",
                    "preferred_language": "en",
                    "phone_number": "+254700000016",
                    "mosip_individual_id": "MOSIP-DUP-ENROLL",
                },
                files=_enrollment_files(5),
            )
            assert resp2.status_code == 409
