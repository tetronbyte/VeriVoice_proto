"""HE-encrypted voice matching via homomorphic dot product (PRD Section 9.4)."""

import logging
import time

import numpy as np
from phe import paillier

from app.config import settings

logger = logging.getLogger(__name__)


class MatchingService:
    """Performs homomorphic dot product between a live embedding and an encrypted centroid."""

    def __init__(self, threshold: float = settings.MATCH_THRESHOLD):
        self.threshold = threshold

    def match(
        self,
        live_embedding: np.ndarray,
        encrypted_centroid: list[paillier.EncryptedNumber],
        private_key: paillier.PaillierPrivateKey,
    ) -> dict:
        """Compute cosine similarity via HE dot product.

        For each dimension i: live_i * Enc(enrolled_i), then homomorphic sum.
        Decrypt the result, clip to [-1, 1], compare against threshold.

        Args:
            live_embedding: L2-normalized float32 array of shape (192,).
            encrypted_centroid: List of Paillier EncryptedNumber (one per dim).
            private_key: Paillier private key for decryption.

        Returns:
            {"score": float, "granted": bool}

        Side effect:
            live_embedding is zeroed in-place after matching.
        """
        start = time.perf_counter()

        # Scalar multiply: live_i * Enc(enrolled_i) for each dimension
        encrypted_products = [
            float(live_embedding[i]) * encrypted_centroid[i]
            for i in range(len(encrypted_centroid))
        ]

        # Homomorphic sum of all encrypted products
        encrypted_dot = encrypted_products[0]
        for ep in encrypted_products[1:]:
            encrypted_dot = encrypted_dot + ep

        # Decrypt the dot product
        score = float(private_key.decrypt(encrypted_dot))

        # Clip to valid cosine similarity range
        score = float(np.clip(score, -1.0, 1.0))

        # Zero live embedding from memory
        live_embedding[:] = 0.0

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("HE matching completed in %.1f ms — score=%.4f", elapsed_ms, score)

        return {"score": score, "granted": score >= self.threshold}
