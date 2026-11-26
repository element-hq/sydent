from typing import TypedDict

import attr

from sydent.types import JsonDict


class VerifyKey(TypedDict):
    key: str


VerifyKeys = dict[str, VerifyKey]


@attr.s(frozen=True, slots=True, auto_attribs=True)
class CachedVerificationKeys:
    verify_keys: VerifyKeys
    valid_until_ts: int


# key: "signing key identifier"; value: signature encoded as unpadded base 64
# See https://spec.matrix.org/unstable/appendices/#signing-details
Signature = dict[str, str]


@attr.s(frozen=True, slots=True, auto_attribs=True)
class SignedMatrixRequest:
    method: str
    uri: str
    destination_is: str
    signatures: dict[str, Signature]
    origin: str
    content: JsonDict
