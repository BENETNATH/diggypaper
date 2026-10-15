"""Chiffrement AES-256-GCM des secrets (clés API, mots de passe SMTP) — Module 7.

Clé maîtresse : 32 octets depuis l'env ``ENCRYPTION_KEY``, acceptée en
- hex : 64 caractères (``bytes.fromhex``), ou
- base64 : 44 caractères (``base64.b64decode``).

Une chaîne de 64 caractères entièrement hexadécimale est interprétée en hex
(elle serait aussi un base64 valide de 48 octets, donc ambigu — le hex prime).

Format stocké en base : ``nonce (12 octets) || ciphertext`` où le ciphertext
GCM inclut le tag d'authentification (16 octets) en fin de bloc.
"""

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

#: Masque renvoyé à l'API pour un secret déjà défini (jamais le plaintext).
MASK = "••••••••"

_NONCE_SIZE = 12
_KEY_SIZE = 32
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _parse_key(raw: str) -> bytes:
    """Parse ``ENCRYPTION_KEY`` (hex 64 ou base64 44) → 32 octets.

    Fail-fast : lève ``ValueError`` si la clé est absente, mal formée ou de
    longueur invalide.
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
    """Clé maîtresse 32 octets depuis ``ENCRYPTION_KEY`` (fail-fast si invalide)."""
    return _parse_key(get_settings().encryption_key)


def encrypt_secret(plaintext: str) -> bytes:
    """Chiffre un secret → ``nonce (12) || ciphertext+tag (GCM)``.

    Le nonce est aléatoire : deux chiffrages du même plaintext produisent des
    ciphertexts différents.
    """
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(load_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt_secret(data: bytes) -> str:
    """Inverse de :func:`encrypt_secret`.

    Lève ``cryptography.exceptions.InvalidTag`` si le ciphertext a été altéré
    (tamper) et ``ValueError`` si le format est invalide.
    """
    if len(data) < _NONCE_SIZE + 16:
        raise ValueError("ciphertext too short")
    nonce, ct = data[:_NONCE_SIZE], data[_NONCE_SIZE:]
    return AESGCM(load_key()).decrypt(nonce, ct, None).decode("utf-8")
