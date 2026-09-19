"""Encrypts Google refresh tokens before they ever reach Postgres.

Uses MultiFernet — a list of keys — even though there's only one key today,
so rotating TOKEN_ENCRYPTION_KEY later (comma-separated, new key first) is
just an env var change: existing rows decrypt with whichever key in the list
still works, and the next save re-encrypts with the first one. No schema or
call-site change needed when that day comes.
"""
import os

from cryptography.fernet import Fernet, MultiFernet


def _load_fernet() -> MultiFernet:
    raw = os.environ["TOKEN_ENCRYPTION_KEY"]
    keys = [Fernet(key.strip().encode()) for key in raw.split(",") if key.strip()]
    if not keys:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return MultiFernet(keys)


def encrypt_token(plaintext: str) -> bytes:
    return _load_fernet().encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    return _load_fernet().decrypt(ciphertext).decode()
