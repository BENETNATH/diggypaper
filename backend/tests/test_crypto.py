"""Tests for AES-256-GCM encryption (app/core/crypto.py, Module 7)."""

import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.core import crypto
from app.core.config import Settings, validate_settings


def test_round_trip():
    plaintext = "ncbi-key-12345"
    ct = crypto.encrypt_secret(plaintext)
    assert crypto.decrypt_secret(ct) == plaintext


def test_ciphertext_differs_from_plaintext():
    ct = crypto.encrypt_secret("super-secret")
    assert ct != b"super-secret"
    assert b"super-secret" not in ct


def test_random_nonce_different_ciphertexts():
    """Random nonce → two encryptions of the same plaintext give distinct ciphertexts."""
    assert crypto.encrypt_secret("same") != crypto.encrypt_secret("same")


def test_tamper_raises():
    """1 byte modified (GCM tag) → authentication exception."""
    ct = bytearray(crypto.encrypt_secret("tamper-me"))
    ct[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        crypto.decrypt_secret(bytes(ct))


def test_invalid_key_fail_fast():
    """Missing / malformed key → fail-fast (ValueError)."""
    with pytest.raises(ValueError):
        crypto._parse_key("")
    with pytest.raises(ValueError):
        crypto._parse_key("not-a-valid-key!!")
    # 64 chars but not hexadecimal → invalid base64 (illegal characters).
    with pytest.raises(ValueError):
        crypto._parse_key("!" * 64)
    # Decoded length != 32 bytes.
    with pytest.raises(ValueError):
        crypto._parse_key(base64.b64encode(b"short").decode())


def test_valid_hex_key():
    key = crypto._parse_key("0123456789abcdef" * 4)
    assert len(key) == 32
    assert key == bytes.fromhex("0123456789abcdef" * 4)


def test_valid_base64_key():
    raw = base64.b64encode(b"A" * 32).decode()
    assert len(raw) == 44
    assert crypto._parse_key(raw) == b"A" * 32


def test_validate_settings_rejects_dev_key():
    """F5: the known dev value of ENCRYPTION_KEY is refused at startup."""
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        validate_settings(Settings(encryption_key="dev-encryption-key-change-me-0000000000"))
