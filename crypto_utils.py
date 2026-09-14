"""
crypto_utils.py
Encrypts/decrypts sensitive per-user secrets (Gmail app passwords) before
they're stored in the database, using a server-side key that only the
hosting environment knows (set via the ENCRYPTION_KEY environment variable -
never committed to code, never sent to any browser).

If ENCRYPTION_KEY isn't set, a key is generated at startup and printed once -
fine for local testing, but for real deployment you MUST set a persistent
ENCRYPTION_KEY env var, or every restart will make previously-stored
passwords undecryptable (users would just need to re-enter them, not a
security problem, but an annoyance worth avoiding).
"""

import os
from cryptography.fernet import Fernet

_key = os.environ.get("ENCRYPTION_KEY")
if not _key:
    _key = Fernet.generate_key().decode()
    print(f"[crypto_utils] WARNING: no ENCRYPTION_KEY set - generated a temporary one for this run.")
    print(f"[crypto_utils] For real deployment, set ENCRYPTION_KEY={_key} in your environment.")

_fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)


def encrypt(plaintext):
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""  # key rotated or corrupted - treat as "not set" rather than crash
