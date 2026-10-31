"""AES-256-GCM encryption of secrets (API keys, SMTP passwords) — Module 7.

Master key: 32 bytes from the ``ENCRYPTION_KEY`` env var, accepted as
- hex: 64 characters (``bytes.fromhex``), or
- base64: 44 characters (``base64.b64decode``).

A 64-character fully hexadecimal string is interpreted as hex
(it would also be a valid base64 of 48 bytes, hence ambiguous — hex wins).

Format stored in the database: ``nonce (12 bytes) || ciphertext`` where the
GCM ciphertext includes the authentication tag (16 bytes) at the end of the block.
"""

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

#: Mask returned to the API for an already-defined secret (never the plaintext).
MASK = "••••••••"

_NONCE_SIZE = 12
_KEY_SIZE = 32
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _parse_key(raw: str) -> bytes:
    """Parses ``ENCRYPTION_KEY`` (hex 64 or base64 44) → 32 bytes.

    Fail-fast: raises ``ValueError`` if the key is missing, malformed, or
    has an invalid length.
    """
    if not raw:
        raise ValueError("ENCRYPTION_KEY is not set")
    if len(raw) == 64 and all(c in _HEX_CHARS for c in raw):
        return bytes.fromhex(raw)
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("ENCRYPTION_KEY must be 64 hex chars or 44 base64 chars") from exc
    if len(key) != _KEY_SIZE:
        raise ValueError(f"ENCRYPTION_KEY must decode to {_KEY_SIZE} bytes (got {len(key)})")
    return key


def load_key() -> bytes:
    """32-byte master key from ``ENCRYPTION_KEY`` (fail-fast if invalid)."""
    return _parse_key(get_settings().encryption_key)


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypts a secret → ``nonce (12) || ciphertext+tag (GCM)``.

    The nonce is random: encrypting the same plaintext twice produces
    different ciphertexts.
    """
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(load_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt_secret(data: bytes) -> str:
    """Inverse of :func:`encrypt_secret`.

    Raises ``cryptography.exceptions.InvalidTag`` if the ciphertext was altered
    (tampered) and ``ValueError`` if the format is invalid.
    """
    if len(data) < _NONCE_SIZE + 16:
        raise ValueError("ciphertext too short")
    nonce, ct = data[:_NONCE_SIZE], data[_NONCE_SIZE:]
    return AESGCM(load_key()).decrypt(nonce, ct, None).decode("utf-8")
