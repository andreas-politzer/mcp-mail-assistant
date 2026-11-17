"""A software passkey that answers like a device (real ES256 signatures)."""

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

RP_ID = "relay.example.org"
ORIGIN = f"https://{RP_ID}"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Device:
    def __init__(self, rp_id=RP_ID, origin=ORIGIN):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.rp_id, self.origin = rp_id, origin

    def cose(self):
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def register(self, challenge_id, origin=None, flags=0x45):
        client = json.dumps(
            {"type": "webauthn.create", "challenge": challenge_id, "origin": origin or self.origin},
            separators=(",", ":"),
        ).encode()
        auth = (
            hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big")
            + b"\x00" * 16 + len(self.cred_id).to_bytes(2, "big") + self.cred_id + self.cose()
        )
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {
            "id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "attestationObject": b64(att)},
            "clientExtensionResults": {},
        }

    def assert_(self, challenge_id, origin=None, rp_id=None, flags=0x05, counter=0, tamper=False,
                ctype="webauthn.get", cross_origin=None):
        data = {"type": ctype, "challenge": challenge_id, "origin": origin or self.origin}
        if cross_origin is not None:
            data["crossOrigin"] = cross_origin
        client = json.dumps(data, separators=(",", ":")).encode()
        auth = hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([flags]) + counter.to_bytes(4, "big")
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {
            "id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
            "response": {
                "clientDataJSON": b64(client), "authenticatorData": b64(auth),
                "signature": b64(sig), "userHandle": None,
            },
            "clientExtensionResults": {},
        }
