"""Encrypts the MFA TOTP secret before it's stored, using a key derived
from the app's existing SECRET_KEY rather than requiring a whole separate
secret to generate, store, and rotate. Backup codes are hashed one-way
(appropriate since a submitted code only ever needs to be checked against
them, never recovered) — the TOTP secret has to be recoverable to
generate/verify live codes, so it's encrypted (two-way) instead.

decrypt_mfa_secret() treats a value that isn't a valid Fernet token as
already-plaintext rather than raising, so a secret stored outside this
module's control still decrypts (returns unchanged) instead of locking
that account out of login. Every secret this module writes is encrypted.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet():
    key_material = hashlib.sha256(config.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def encrypt_mfa_secret(plain_secret):
    if not plain_secret:
        return plain_secret
    return _fernet().encrypt(plain_secret.encode()).decode()


def decrypt_mfa_secret(stored_value):
    if not stored_value:
        return stored_value
    try:
        return _fernet().decrypt(stored_value.encode()).decode()
    except (InvalidToken, ValueError):
        return stored_value
