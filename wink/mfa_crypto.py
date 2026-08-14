"""Encrypts the MFA TOTP secret before it's stored, using a key derived
from the app's existing SECRET_KEY rather than requiring a whole separate
secret to generate, store, and rotate. Backup codes are already hashed
(one-way, appropriate since we only ever need to check a submitted code
against them, never recover the original) — the TOTP secret is different:
it has to be recoverable to actually generate/verify codes, so it can only
be encrypted (two-way), not hashed. Without this, a database compromise
would hand over the admin account's second factor directly, in plaintext,
defeating a meaningful part of what MFA is supposed to add.

decrypt_mfa_secret() falls back to returning the value unchanged if it
doesn't look like a Fernet token — this exists purely as a safe migration
path for any secret that was already stored in plaintext before this
module existed, not as an ongoing accepted state. Every secret written
going forward is always encrypted."""
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
        # Not a Fernet token — a secret stored before this module existed.
        # Treat as already-plaintext rather than failing every login for
        # an account that enabled MFA before this fix.
        return stored_value
