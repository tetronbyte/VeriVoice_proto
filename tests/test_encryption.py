"""Phase 5 validation: Paillier HE encryption/decryption roundtrip."""

import json
import tempfile
import pathlib

import numpy as np
import pytest

from app.utils.security import generate_paillier_keypair, save_paillier_keys, load_paillier_keys
from app.services.encryption_service import EncryptionService


@pytest.fixture(scope="module")
def key_path(tmp_path_factory) -> str:
    """Generate a test keypair once and return the path."""
    path = str(tmp_path_factory.mktemp("keys") / "test_paillier.json")
    pub, priv = generate_paillier_keypair(n_length=1024)  # smaller for speed
    save_paillier_keys(pub, priv, path)
    return path


@pytest.fixture(scope="module")
def service(key_path: str) -> EncryptionService:
    return EncryptionService(key_path=key_path, n_length=1024)


@pytest.fixture()
def sample_centroid() -> np.ndarray:
    """A deterministic 192-dim L2-normalized vector."""
    rng = np.random.default_rng(42)
    vec = rng.standard_normal(192).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


# ── Key Management ───────────────────────────────────────────────────────────

class TestKeyManagement:
    def test_generate_and_save_load(self, key_path: str):
        pub, priv = load_paillier_keys(key_path)
        assert pub.n > 0
        assert priv.p * priv.q == pub.n

    def test_save_creates_directories(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "keys.json")
        pub, priv = generate_paillier_keypair(n_length=1024)
        save_paillier_keys(pub, priv, nested)
        assert pathlib.Path(nested).exists()

    def test_roundtrip_encrypt_decrypt_single(self, key_path: str):
        pub, priv = load_paillier_keys(key_path)
        original = 0.12345
        encrypted = pub.encrypt(original)
        decrypted = priv.decrypt(encrypted)
        assert abs(decrypted - original) < 1e-10


# ── Encryption Service ───────────────────────────────────────────────────────

class TestEncryptCentroid:
    def test_roundtrip_192dim(self, service: EncryptionService, sample_centroid: np.ndarray):
        original = sample_centroid.copy()
        ciphertext_bytes = service.encrypt_centroid(sample_centroid)

        # Deserialize and decrypt
        encrypted_numbers = service.deserialize_ciphertext(ciphertext_bytes)
        decrypted = service.decrypt_vector(encrypted_numbers)

        assert decrypted.shape == (192,)
        assert decrypted.dtype == np.float32
        assert np.allclose(decrypted, original, atol=1e-5)

    def test_ciphertext_format(self, service: EncryptionService, sample_centroid: np.ndarray):
        ciphertext_bytes = service.encrypt_centroid(sample_centroid)
        blob = json.loads(ciphertext_bytes.decode("utf-8"))

        assert "n" in blob
        assert blob["dim"] == 192
        assert len(blob["ciphertexts"]) == 192
        assert "c" in blob["ciphertexts"][0]
        assert "exp" in blob["ciphertexts"][0]

    def test_plaintext_zeroed_after_encrypt(self, service: EncryptionService):
        vec = np.ones(192, dtype=np.float32) * 0.5
        service.encrypt_centroid(vec)
        # The input array should be zeroed in-place
        assert np.all(vec == 0.0)

    def test_ciphertext_is_bytes(self, service: EncryptionService, sample_centroid: np.ndarray):
        result = service.encrypt_centroid(sample_centroid)
        assert isinstance(result, bytes)


class TestDeserializeCiphertext:
    def test_deserialize_returns_correct_count(self, service: EncryptionService, sample_centroid: np.ndarray):
        ciphertext_bytes = service.encrypt_centroid(sample_centroid)
        encrypted_numbers = service.deserialize_ciphertext(ciphertext_bytes)
        assert len(encrypted_numbers) == 192

    def test_decrypt_individual_values(self, service: EncryptionService):
        vec = np.array([0.1, -0.2, 0.3], dtype=np.float32)
        original = vec.copy()
        ct = service.encrypt_centroid(vec)
        enc = service.deserialize_ciphertext(ct)
        for i, ev in enumerate(enc):
            decrypted_val = service.private_key.decrypt(ev)
            assert abs(decrypted_val - float(original[i])) < 1e-5


class TestAutoKeyGeneration:
    def test_generates_keys_if_missing(self, tmp_path):
        path = str(tmp_path / "auto_keys.json")
        svc = EncryptionService(key_path=path, n_length=1024)
        assert pathlib.Path(path).exists()
        assert svc.public_key.n > 0
