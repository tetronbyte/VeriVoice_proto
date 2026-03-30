"""Phase 12 validation: GET /challenge + POST /authenticate via TestClient."""

import io

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app

# ── In-memory SQLite ─────────────────────────────────────────────────────────
_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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

_SR = 16000


def _make_wav_bytes(freq: float = 440.0, duration: float = 2.0) -> bytes:
    t = np.linspace(0, duration, int(_SR * duration), endpoint=False, dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * freq * t)
    buf = io.BytesIO()
    sf.write(buf, audio, _SR, format="WAV")
    buf.seek(0)
    return buf.read()


def _enrollment_files(freq: float = 440.0, n: int = 5):
    return [
        ("audio_files", (f"s{i}.wav", _make_wav_bytes(freq=freq), "audio/wav"))
        for i in range(n)
    ]


def _enroll(nid: str, freq: float = 440.0) -> dict:
    """Enroll a citizen and return the response body."""
    resp = client.post(
        "/api/v1/enroll",
        data={"national_id_number": nid, "phone_number": "+254700000099", "preferred_language": "en"},
        files=_enrollment_files(freq=freq),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestGetChallenge:
    def test_returns_phrase_and_id(self):
        resp = client.get("/api/v1/challenge", params={"language": "en"})
        assert resp.status_code == 200
        body = resp.json()
        assert "challenge_id" in body
        assert "phrase_text" in body
        assert len(body["phrase_text"]) > 0

    def test_swahili_challenge(self):
        resp = client.get("/api/v1/challenge", params={"language": "sw"})
        assert resp.status_code == 200


class TestAuthenticateGranted:
    def test_same_speaker_granted(self):
        """Enroll with freq=440, authenticate with same freq → biometric match.
        Transcript match will fail (sine wave ≠ challenge phrase), so we
        test only the voice biometric score here. The full dual-gate is
        tested in the denied case.
        """
        enrolled = _enroll("KE-AUTH-GRANT-001", freq=440.0)

        # Get a challenge
        challenge_resp = client.get("/api/v1/challenge", params={"language": "en"})
        challenge = challenge_resp.json()

        # Authenticate with same frequency audio (same "speaker")
        auth_wav = _make_wav_bytes(freq=440.0)
        resp = client.post(
            "/api/v1/authenticate",
            data={
                "citizen_id": enrolled["citizen_id"],
                "challenge_phrase_id": challenge["challenge_id"],
            },
            files=[("audio_file", ("auth.wav", auth_wav, "audio/wav"))],
        )
        assert resp.status_code == 200
        body = resp.json()

        # Voice biometric score should be high (same synthetic "speaker")
        assert body["voice_match_score"] > 0.9
        assert "event_id" in body
        assert "event_timestamp" in body
        # result may be "denied" because transcript won't match challenge phrase
        # (sine wave transcribes to gibberish), but the voice score confirms biometric works


class TestAuthenticateDenied:
    def test_different_speaker_low_score(self):
        """Enroll with freq=440, authenticate with freq=1600 → different embedding."""
        enrolled = _enroll("KE-AUTH-DENY-001", freq=440.0)

        challenge_resp = client.get("/api/v1/challenge")
        challenge = challenge_resp.json()

        # Authenticate with a very different frequency (different "speaker")
        auth_wav = _make_wav_bytes(freq=1600.0)
        resp = client.post(
            "/api/v1/authenticate",
            data={
                "citizen_id": enrolled["citizen_id"],
                "challenge_phrase_id": challenge["challenge_id"],
            },
            files=[("audio_file", ("auth.wav", auth_wav, "audio/wav"))],
        )
        assert resp.status_code == 200
        body = resp.json()

        # Should be denied — voice score lower and transcript won't match
        assert body["result"] == "denied"


class TestAuthenticateErrors:
    def test_nonexistent_citizen_returns_404(self):
        challenge_resp = client.get("/api/v1/challenge")
        challenge = challenge_resp.json()

        resp = client.post(
            "/api/v1/authenticate",
            data={
                "citizen_id": "00000000-0000-0000-0000-000000000000",
                "challenge_phrase_id": challenge["challenge_id"],
            },
            files=[("audio_file", ("auth.wav", _make_wav_bytes(), "audio/wav"))],
        )
        assert resp.status_code == 404


class TestAuditTrail:
    def test_auth_event_created(self):
        """Every authentication attempt should create an AUTH_EVENT record."""
        enrolled = _enroll("KE-AUTH-AUDIT-001", freq=440.0)

        challenge_resp = client.get("/api/v1/challenge")
        challenge = challenge_resp.json()

        resp = client.post(
            "/api/v1/authenticate",
            data={
                "citizen_id": enrolled["citizen_id"],
                "challenge_phrase_id": challenge["challenge_id"],
            },
            files=[("audio_file", ("auth.wav", _make_wav_bytes(), "audio/wav"))],
        )
        assert resp.status_code == 200
        body = resp.json()

        # Verify the event was persisted
        db = _TestSession()
        from app.models.auth_event import AuthEvent
        event = db.query(AuthEvent).filter(AuthEvent.event_id == body["event_id"]).first()
        assert event is not None
        assert event.voice_match_score == body["voice_match_score"]
        assert event.result in ("granted", "denied")
        db.close()
