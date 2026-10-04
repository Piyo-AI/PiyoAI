import base64

import pytest
from catalog_signing import KEY_ID, TRUSTED, signature_for

from piyo.skills import signing

DATA = b'{"schema": 2, "skills": []}\n'


def test_a_signature_verifies_and_names_its_key():
    assert signing.verify(DATA, signature_for(DATA), TRUSTED) == KEY_ID


def test_any_change_to_the_data_breaks_it():
    sig = signature_for(DATA)
    for changed in (DATA + b" ", DATA.replace(b"2", b"3"), b""):
        with pytest.raises(signing.SignatureError, match="did not verify"):
            signing.verify(changed, sig, TRUSTED)


def test_a_signature_made_without_the_prefix_is_not_accepted():
    # a signature over the bare bytes (made for some other purpose) must not pass as a catalog signature
    private, public = signing.generate()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(private).sign(DATA)
    line = f"{signing.VERSION} {signing.key_id(public)} {base64.b64encode(raw).decode()}"
    trusted = {signing.key_id(public): base64.b64encode(public).decode()}
    with pytest.raises(signing.SignatureError, match="did not verify"):
        signing.verify(DATA, line, trusted)


def test_another_key_cannot_sign_for_a_trusted_one():
    private, public = signing.generate()
    forged = signing.sign(private, DATA).replace(
        signing.key_id(public), KEY_ID
    )  # claims the trusted key's id
    with pytest.raises(signing.SignatureError, match="did not verify"):
        signing.verify(DATA, forged, TRUSTED)


def test_unknown_key_and_malformed_lines_are_refused_with_a_reason():
    private, _ = signing.generate()
    with pytest.raises(signing.SignatureError, match="does not trust"):
        signing.verify(DATA, signing.sign(private, DATA), TRUSTED)
    for bad in (
        "",
        "garbage",
        f"{signing.VERSION} {KEY_ID}",
        f"v2 {KEY_ID} AAAA",
        f"{signing.VERSION} {KEY_ID} !!!!",
    ):
        with pytest.raises(signing.SignatureError):
            signing.verify(DATA, bad, TRUSTED)


def test_with_no_trusted_keys_nothing_verifies():
    assert signing.TRUSTED_KEYS is not None
    with pytest.raises(signing.SignatureError):
        signing.verify(DATA, signature_for(DATA), {})


def test_key_roundtrip():
    private, public = signing.generate()
    assert signing.public_of(private) == public and len(private) == 32 and len(public) == 32
