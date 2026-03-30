"""Phase 4 validation: EmbeddingService — shape, L2-norm, centroid, and error handling."""

import numpy as np
import pytest

from app.services.embedding_service import EmbeddingService


SAMPLE_RATE = 16000


def _make_sine(freq: float = 440.0, duration: float = 2.0) -> np.ndarray:
    """Synthetic sine wave matching AudioPreprocessor output format."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False, dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * freq * t)
    return audio.astype(np.float32)


@pytest.fixture(scope="module")
def service() -> EmbeddingService:
    return EmbeddingService()


@pytest.fixture(scope="module")
def sample_embedding(service: EmbeddingService) -> np.ndarray:
    """Extract once and reuse across tests in this module."""
    audio = _make_sine(freq=440.0, duration=2.0)
    return service.extract_embedding(audio)


class TestExtractEmbedding:
    def test_output_shape(self, sample_embedding: np.ndarray):
        assert sample_embedding.shape == (192,)

    def test_output_dtype(self, sample_embedding: np.ndarray):
        assert sample_embedding.dtype == np.float32

    def test_l2_norm_is_unit(self, sample_embedding: np.ndarray):
        norm = float(np.linalg.norm(sample_embedding))
        assert abs(norm - 1.0) < 1e-5

    def test_different_audio_gives_different_embedding(self, service: EmbeddingService, sample_embedding: np.ndarray):
        other_audio = _make_sine(freq=880.0, duration=2.0)
        other_emb = service.extract_embedding(other_audio)
        # Different input should produce a different embedding vector
        assert not np.allclose(sample_embedding, other_emb, atol=1e-3)


class TestComputeCentroid:
    def test_centroid_shape(self, service: EmbeddingService, sample_embedding: np.ndarray):
        centroid = service.compute_centroid([sample_embedding, sample_embedding])
        assert centroid.shape == (192,)

    def test_centroid_l2_norm(self, service: EmbeddingService, sample_embedding: np.ndarray):
        centroid = service.compute_centroid([sample_embedding, sample_embedding])
        norm = float(np.linalg.norm(centroid))
        assert abs(norm - 1.0) < 1e-5

    def test_centroid_of_identical_is_same(self, service: EmbeddingService, sample_embedding: np.ndarray):
        centroid = service.compute_centroid([sample_embedding, sample_embedding, sample_embedding])
        # Centroid of identical vectors should equal the vector itself
        assert np.allclose(centroid, sample_embedding, atol=1e-5)


class TestZeroNormRejection:
    def test_zero_vector_raises(self, service: EmbeddingService):
        zeros = [np.zeros(192, dtype=np.float32)]
        with pytest.raises(ValueError, match="below threshold"):
            service.compute_centroid(zeros)


class TestSingleton:
    def test_same_instance(self):
        a = EmbeddingService()
        b = EmbeddingService()
        assert a is b
