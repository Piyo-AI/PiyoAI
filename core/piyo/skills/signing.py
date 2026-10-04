"""The catalog's signature: Ed25519 over `index.json`, checked before the app reads a single entry.

The index lists every package's hash, so one signature over it vouches for the whole catalog at one commit.
The maintainer signs on their own machine (`piyo-skills/scripts/sign_index.py`; the private key lives in
that machine's OS keychain) and commits `index.json.sig` next to the index. The app carries the matching
public keys in `TRUSTED_KEYS` and refuses a catalog whose signature does not verify, so a stolen GitHub
account or a bad merge cannot publish a skill by itself. The signature does not expire, so it does not stop
someone replaying an older signed index; that is why the app always resolves the branch head over HTTPS and
pins to that commit.

A signature file is one line: `piyo-sig-v1 <key id> <base64 of the 64-byte signature>`. The key id names which
public key to use, so keys can be rotated: ship an app that trusts both, sign with the new one, drop the old.
"""

from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

VERSION = "piyo-sig-v1"
# The signed message is this prefix plus the file's bytes, so a signature made for something else is useless.
PREFIX = b"piyo-catalog-index-v1\n"

# key id -> base64 of the raw 32-byte public key. Add the output of `sign_index.py public` here.
TRUSTED_KEYS: dict[str, str] = {
    "c0a9015bf8e7a5db": "Pq9ZDbpTJDYbEpmoyt4GUfq7spa4dMbhuq6SnGZK788=",  # Piyo AI catalog key, 2026-10-04
}


class SignatureError(ValueError):
    """Why a signature was not accepted; the message is safe to show the user."""


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def generate() -> tuple[bytes, bytes]:
    """A new key pair as raw bytes: (private seed, public key)."""
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        ),
        private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
    )


def public_of(private_raw: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(private_raw)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )


def sign(private_raw: bytes, data: bytes) -> str:
    """The signature line for `data`."""
    private = Ed25519PrivateKey.from_private_bytes(private_raw)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    signature = private.sign(PREFIX + data)
    return f"{VERSION} {key_id(public)} {base64.b64encode(signature).decode('ascii')}\n"


def verify(data: bytes, signature_text: str, trusted: dict[str, str] | None = None) -> str:
    """Checks `data` against a signature line and returns the id of the key that signed it, or raises."""
    keys = TRUSTED_KEYS if trusted is None else trusted
    parts = signature_text.split()
    if len(parts) != 3 or parts[0] != VERSION:
        raise SignatureError("The catalog's signature is not in a format this version of Piyo understands.")
    _, signer, encoded = parts
    if signer not in keys:
        raise SignatureError(
            "The catalog is signed with a key this version of Piyo does not trust. Update Piyo."
        )
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[signer], validate=True))
        signature = base64.b64decode(encoded, validate=True)
        public.verify(signature, PREFIX + data)
    except (binascii.Error, ValueError, InvalidSignature):
        raise SignatureError("The catalog's signature did not verify, so it was not used.") from None
    return signer
