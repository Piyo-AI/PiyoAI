"""A throw-away signing key for tests; conftest makes the app trust it."""

import base64

from piyo.skills import signing

PRIVATE, PUBLIC = signing.generate()
KEY_ID = signing.key_id(PUBLIC)
TRUSTED = {KEY_ID: base64.b64encode(PUBLIC).decode("ascii")}


def signature_for(data: bytes) -> str:
    return signing.sign(PRIVATE, data)
