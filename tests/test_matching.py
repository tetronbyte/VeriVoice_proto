"""Phase 6 validation: HE matching — identity, orthogonal, opposite vectors."""

import logging

import numpy as np
import pytest

from app.utils.security import generate_paillier_keypair
from app.services.encryption_service import EncryptionService
from app.services.matching_service import MatchingService


logger = logging.getLogger(__name__)

# Use 1024-bit keys for test speed (2048-bit in production)
_N_LENGTH = 1024


@pytest.fixture(scope="module")
def keys():
    pub, priv = generate_paillier_keypair(n_length=_N_LENGTH)
    return pub, priv


@pytest.fixture(scope="module")
def encryption_service(keys, tmp_path_factory) -> EncryptionService:
    from app.utils.security import save_paillier_keys
    path = str(tmp_path_factory.mktemp("keys") / "paillier.json")
    save_paillier_keys(keys[0], keys[1], path)
    return EncryptionService(key_path=path, n_length=_N_LENGTH)


@pytest.fixture(scope="module")
def matching_service() -> MatchingService:
    return MatchingService(threshold=0.45)


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture(scope="module")
def unit_vector_a() -> np.ndarray:
    """Deterministic L2-normalized 192-dim vector."""
    rng = np.random.default_rng(42)
    return _l2_normalize(rng.standard_normal(192).astype(np.float32))


@pytest.fixture(scope="module")
def encrypted_a(encryption_service: EncryptionService, unit_vector_a: np.ndarray):
    """Encrypt vector A once, return ciphertext bytes."""
    centroid = unit_vector_a.copy()
    ct_bytes = encryption_service.encrypt_centroid(centroid)
    return ct_bytes


class TestIdentityMatch:
    def test_score_near_one(self, matching_service, encryption_service, encrypted_a, unit_vector_a):
        enc = encryption_service.deserialize_ciphertext(encrypted_a)
        live = unit_vector_a.copy()
        result = matching_service.match(live, enc, encryption_service.private_key)

        logger.info("Identity match score: %.6f", result["score"])
        print(f"\n>>> Identity match score: {result['score']:.6f}")

        assert result["score"] > 0.99
        assert result["granted"] is True

    def test_live_embedding_zeroed(self, matching_service, encryption_service, encrypted_a, unit_vector_a):
        enc = encryption_service.deserialize_ciphertext(encrypted_a)
        live = unit_vector_a.copy()
        matching_service.match(live, enc, encryption_service.private_key)
        assert np.all(live == 0.0)


class TestOrthogonalMatch:
    def test_score_near_zero(self, matching_service, encryption_service, encrypted_a, unit_vector_a):
        # Build a vector orthogonal to A using Gram-Schmidt
        rng = np.random.default_rng(99)
        b = rng.standard_normal(192).astype(np.float32)
        b = b - unit_vector_a * np.dot(b, unit_vector_a)
        b = _l2_normalize(b)

        enc = encryption_service.deserialize_ciphertext(encrypted_a)
        live = b.copy()
        result = matching_service.match(live, enc, encryption_service.private_key)

        logger.info("Orthogonal match score: %.6f", result["score"])
        print(f"\n>>> Orthogonal match score: {result['score']:.6f}")

        assert abs(result["score"]) < 0.15
        assert result["granted"] is False


class TestOppositeMatch:
    def test_score_near_negative_one(self, matching_service, encryption_service, encrypted_a, unit_vector_a):
        negated = (-unit_vector_a).copy()

        enc = encryption_service.deserialize_ciphertext(encrypted_a)
        result = matching_service.match(negated, enc, encryption_service.private_key)

        logger.info("Opposite match score: %.6f", result["score"])
        print(f"\n>>> Opposite match score: {result['score']:.6f}")

        assert result["score"] < -0.99
        assert result["granted"] is False
