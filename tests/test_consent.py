"""Phase 9 validation: ConsentService — Ed25519 signing and verification."""

import pathlib

import pytest

from app.utils.security import generate_ed25519_keypair, save_ed25519_keys, load_ed25519_signing_key, load_ed25519_verify_key
from app.services.consent_service import ConsentService


SAMPLE_PAYLOAD = {
    "citizen_id": "550e8400-e29b-41d4-a716-446655440000",
    "ministry_code": "MOH",
    "data_scope": "health_records",
    "issued_at": "2026-03-30T12:00:00Z",
}


@pytest.fixture(scope="module")
def key_path(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("keys") / "ed25519.bin")
    sk, _ = generate_ed25519_keypair()
    save_ed25519_keys(sk, path)
    return path


@pytest.fixture(scope="module")
def service(key_path: str) -> ConsentService:
    return ConsentService(key_path=key_path)


# ── Key Management ───────────────────────────────────────────────────────────

class TestKeyManagement:
    def test_generate_and_save_load(self, key_path: str):
        sk = load_ed25519_signing_key(key_path)
        assert len(bytes(sk)) == 32  # Ed25519 signing key is 32 bytes

    def test_verify_key_derived(self, key_path: str):
        vk = load_ed25519_verify_key(key_path)
        assert len(bytes(vk)) == 32  # Ed25519 verify key is 32 bytes

    def test_save_creates_directories(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "ed25519.bin")
        sk, _ = generate_ed25519_keypair()
        save_ed25519_keys(sk, nested)
        assert pathlib.Path(nested).exists()


# ── Signing ──────────────────────────────────────────────────────────────────

class TestSignConsent:
    def test_signature_is_64_bytes(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        assert isinstance(sig, bytes)
        assert len(sig) == 64

    def test_deterministic_signature(self, service: ConsentService):
        sig1 = service.sign_consent(**SAMPLE_PAYLOAD)
        sig2 = service.sign_consent(**SAMPLE_PAYLOAD)
        # Ed25519 is deterministic — same key + same message = same signature
        assert sig1 == sig2


# ── Verification ─────────────────────────────────────────────────────────────

class TestVerifyConsent:
    def test_valid_signature(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        assert service.verify_consent(**SAMPLE_PAYLOAD, signature=sig) is True

    def test_tampered_data_scope(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        tampered = {**SAMPLE_PAYLOAD, "data_scope": "health_record"}  # removed trailing 's'
        assert service.verify_consent(**tampered, signature=sig) is False

    def test_tampered_citizen_id(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        tampered = {**SAMPLE_PAYLOAD, "citizen_id": "00000000-0000-0000-0000-000000000000"}
        assert service.verify_consent(**tampered, signature=sig) is False

    def test_tampered_ministry_code(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        tampered = {**SAMPLE_PAYLOAD, "ministry_code": "MOE"}
        assert service.verify_consent(**tampered, signature=sig) is False

    def test_tampered_timestamp(self, service: ConsentService):
        sig = service.sign_consent(**SAMPLE_PAYLOAD)
        tampered = {**SAMPLE_PAYLOAD, "issued_at": "2026-03-30T13:00:00Z"}
        assert service.verify_consent(**tampered, signature=sig) is False

    def test_garbage_signature(self, service: ConsentService):
        assert service.verify_consent(**SAMPLE_PAYLOAD, signature=b"\x00" * 64) is False


# ── Auto Key Generation ─────────────────────────────────────────────────────

class TestAutoKeyGeneration:
    def test_generates_keys_if_missing(self, tmp_path):
        path = str(tmp_path / "auto_ed25519.bin")
        svc = ConsentService(key_path=path)
        assert pathlib.Path(path).exists()
        sig = svc.sign_consent(**SAMPLE_PAYLOAD)
        assert svc.verify_consent(**SAMPLE_PAYLOAD, signature=sig) is True
