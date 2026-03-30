"""Paillier homomorphic encryption for voice templates (PRD Section 9.3)."""

import json

import numpy as np
from phe import paillier

from app.config import settings
from app.utils.security import generate_paillier_keypair, load_paillier_keys, save_paillier_keys


class EncryptionService:
    """Handles Paillier HE encryption/decryption of 192-dim speaker centroids."""

    def __init__(
        self,
        key_path: str = settings.PAILLIER_KEY_PATH,
        n_length: int = settings.PAILLIER_BITS,
    ):
        self._key_path = key_path
        self._n_length = n_length
        self._public_key, self._private_key = self._load_or_generate_keys()

    def _load_or_generate_keys(self) -> tuple[paillier.PaillierPublicKey, paillier.PaillierPrivateKey]:
        """Load keys from disk, or generate and save a new keypair."""
        import pathlib

        if pathlib.Path(self._key_path).exists():
            return load_paillier_keys(self._key_path)

        public_key, private_key = generate_paillier_keypair(self._n_length)
        save_paillier_keys(public_key, private_key, self._key_path)
        return public_key, private_key

    @property
    def public_key(self) -> paillier.PaillierPublicKey:
        return self._public_key

    @property
    def private_key(self) -> paillier.PaillierPrivateKey:
        return self._private_key

    def encrypt_centroid(self, centroid: np.ndarray) -> bytes:
        """Encrypt a 192-dim float32 centroid dimension-wise using Paillier HE.

        Args:
            centroid: L2-normalized float32 numpy array of shape (192,).

        Returns:
            JSON-serialized ciphertext as bytes:
            {n: str, dim: int, ciphertexts: [{c: str, exp: int}, ...]}

        Side effect:
            The input centroid array is zeroed in-place after encryption.
        """
        dim = len(centroid)
        encrypted_values = [
            self._public_key.encrypt(float(centroid[i]))
            for i in range(dim)
        ]

        # Zero plaintext from memory immediately
        centroid[:] = 0.0

        blob = {
            "n": str(self._public_key.n),
            "dim": dim,
            "ciphertexts": [
                {"c": str(ev.ciphertext(be_secure=False)), "exp": ev.exponent}
                for ev in encrypted_values
            ],
        }
        return json.dumps(blob).encode("utf-8")

    def deserialize_ciphertext(self, data: bytes) -> list[paillier.EncryptedNumber]:
        """Reconstruct Paillier EncryptedNumber objects from a JSON blob.

        Args:
            data: JSON bytes as produced by encrypt_centroid.

        Returns:
            List of EncryptedNumber objects (one per dimension).
        """
        blob = json.loads(data.decode("utf-8"))
        n = int(blob["n"])
        pub_key = paillier.PaillierPublicKey(n=n)
        return [
            paillier.EncryptedNumber(pub_key, int(ct["c"]), int(ct["exp"]))
            for ct in blob["ciphertexts"]
        ]

    def decrypt_vector(self, encrypted: list[paillier.EncryptedNumber]) -> np.ndarray:
        """Decrypt a list of EncryptedNumbers back to a float32 numpy array.

        Args:
            encrypted: List of EncryptedNumber objects.

        Returns:
            Float32 numpy array of decrypted values.
        """
        return np.array(
            [self._private_key.decrypt(ev) for ev in encrypted],
            dtype=np.float32,
        )
