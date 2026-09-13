"""Password hashing and token primitives (stdlib only).

Uses scrypt (memory-hard KDF) for passwords and timecompat-safe constant-time
comparison. No third-party crypto dependency is introduced; secrets come from
the stdlib ``secrets`` module. Session tokens are opaque random strings; only
their hash is ever stored in the database, so a DB leak does not expose usable
sessions.

These primitives are NOT used to secure anything in production until Phase 10
hardening; they exist to avoid ever storing plaintext credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14          # 16 MiB memory
_SCRYPT_R = 8
_SCRYPT_P = 1
# pbkdf2 is used only for deriving a fixed-length token/verifier when scrypt
# is not practical; scrypt is preferred for passwords.
_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash string ``$scrypt$N$r$p$salt$hash``.

    Salt is 16 random bytes (hex). scrypt is deliberately memory-hard to slow
    offline brute-force of a stolen password hash.
    """
    salt = secrets.token_hex(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"$scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a hash produced by :func:`hash_password`."""
    if not stored or not stored.startswith("$scrypt$"):
        return False
    try:
        scheme, n_s, r_s, p_s, salt, expected_hex = stored.split("$")[1:]
        if scheme != "scrypt" or not (salt and expected_hex):
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt_bytes = bytes.fromhex(salt)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, TypeError):
        return False
    try:
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt_bytes,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def new_token(length: int = 32) -> str:
    """Generate a high-entropy opaque session/API token."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """Return the stable SHA-256 (hex) of a token for DB lookup.

    The raw token is only ever presented to the client; storage and comparison
    use this digest so a DB read cannot be replayed as a live session.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()