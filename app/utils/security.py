"""Key management utilities for Paillier HE and Ed25519 signatures."""

import json
import pathlib

from nacl.signing import SigningKey, VerifyKey
from phe import paillier


def generate_paillier_keypair(n_length: int = 2048) -> tuple[paillier.PaillierPublicKey, paillier.PaillierPrivateKey]:
    """Generate a Paillier keypair with the given bit length."""
    public_key, private_key = paillier.generate_paillier_keypair(n_length=n_length)
    return public_key, private_key


def save_paillier_keys(
    public_key: paillier.PaillierPublicKey,
    private_key: paillier.PaillierPrivateKey,
    path: str,
) -> None:
    """Save Paillier keypair to a JSON file."""
    filepath = pathlib.Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "public_key": {"n": str(public_key.n)},
        "private_key": {"p": str(private_key.p), "q": str(private_key.q)},
    }
    filepath.write_text(json.dumps(data, indent=2))


def load_paillier_keys(path: str) -> tuple[paillier.PaillierPublicKey, paillier.PaillierPrivateKey]:
    """Load Paillier keypair from a JSON file."""
    data = json.loads(pathlib.Path(path).read_text())
    n = int(data["public_key"]["n"])
    p = int(data["private_key"]["p"])
    q = int(data["private_key"]["q"])
    public_key = paillier.PaillierPublicKey(n=n)
    private_key = paillier.PaillierPrivateKey(public_key, p, q)
    return public_key, private_key


# ── Ed25519 ──────────────────────────────────────────────────────────────────

def generate_ed25519_keypair() -> tuple[SigningKey, VerifyKey]:
    """Generate an Ed25519 signing keypair."""
    signing_key = SigningKey.generate()
    return signing_key, signing_key.verify_key


def save_ed25519_keys(signing_key: SigningKey, path: str) -> None:
    """Save Ed25519 signing key (private) to a file. Verify key is derived from it."""
    filepath = pathlib.Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(bytes(signing_key))


def load_ed25519_signing_key(path: str) -> SigningKey:
    """Load Ed25519 signing key from a file."""
    return SigningKey(pathlib.Path(path).read_bytes())


def load_ed25519_verify_key(path: str) -> VerifyKey:
    """Load the Ed25519 verify key (derived from the signing key on disk)."""
    signing_key = load_ed25519_signing_key(path)
    return signing_key.verify_key
