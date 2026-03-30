"""Ed25519 consent token signing and verification (PRD Section 9.8)."""

import json
import pathlib

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from app.config import settings
from app.utils.security import (
    generate_ed25519_keypair,
    load_ed25519_signing_key,
    save_ed25519_keys,
)


class ConsentService:
    """Generates and verifies cryptographically signed consent tokens using Ed25519."""

    def __init__(self, key_path: str = settings.ED25519_KEY_PATH):
        self._key_path = key_path
        self._signing_key, self._verify_key = self._load_or_generate_keys()

    def _load_or_generate_keys(self) -> tuple[SigningKey, VerifyKey]:
        """Load keys from disk, or generate and save a new keypair."""
        if pathlib.Path(self._key_path).exists():
            sk = load_ed25519_signing_key(self._key_path)
            return sk, sk.verify_key

        sk, vk = generate_ed25519_keypair()
        save_ed25519_keys(sk, self._key_path)
        return sk, vk

    @property
    def verify_key(self) -> VerifyKey:
        return self._verify_key

    @staticmethod
    def _serialize_payload(citizen_id: str, ministry_code: str, data_scope: str, issued_at: str) -> bytes:
        """Deterministic JSON serialization of the consent payload."""
        payload = {
            "citizen_id": citizen_id,
            "data_scope": data_scope,
            "issued_at": issued_at,
            "ministry_code": ministry_code,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign_consent(
        self,
        citizen_id: str,
        ministry_code: str,
        data_scope: str,
        issued_at: str,
    ) -> bytes:
        """Sign a consent payload using Ed25519.

        Args:
            citizen_id: UUID string of the citizen.
            ministry_code: e.g. "MOH".
            data_scope: e.g. "health_records".
            issued_at: ISO-format UTC timestamp string.

        Returns:
            Ed25519 signature bytes (64 bytes).
        """
        message = self._serialize_payload(citizen_id, ministry_code, data_scope, issued_at)
        signed = self._signing_key.sign(message)
        return signed.signature  # 64-byte signature only

    def verify_consent(
        self,
        citizen_id: str,
        ministry_code: str,
        data_scope: str,
        issued_at: str,
        signature: bytes,
    ) -> bool:
        """Verify a consent token signature.

        Args:
            citizen_id, ministry_code, data_scope, issued_at: The payload fields.
            signature: The 64-byte Ed25519 signature.

        Returns:
            True if the signature is valid, False if tampered.
        """
        message = self._serialize_payload(citizen_id, ministry_code, data_scope, issued_at)
        try:
            self._verify_key.verify(message, signature)
            return True
        except BadSignatureError:
            return False
