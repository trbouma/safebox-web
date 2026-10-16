"""Offline NFC sizing experiment. Synthetic credentials only; not an issuer.

Run: poetry run python scripts/measure_nfc_card_size.py
No application configuration, wallet, network, or output files are accessed.
Fixed fixture keys/nonces make measurements reproducible; NEVER issue with them.
"""
import base64
import json

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MIME = b"application/vnd.safebox.card+cbor"  # Proposed, not registered.
DOMAIN = b"safebox-web/nfc-card/v1\x00"
QR_PREFIX = "safebox-card:v1:"


def fixture_envelope(issuer, relay, pin="012345", kid=1):
    """Return actual encrypted fixture bytes and intermediate sizes."""
    private = {
        0: bytes.fromhex("11" * 32),  # raw secp256k1 private key, NOT nsec text
        1: relay,
        2: pin,
        3: bytes.fromhex("22" * 16),  # card generation ID; database active value must match
        4: 1792108800,  # fixed 2026-era timestamps, both uint32
        5: 1823644800,
    }
    header = {0: 1, 1: 1, 2: issuer, 3: kid}  # version, login purpose, issuer, key ID
    plaintext = cbor2.dumps(private, canonical=True)
    aad = DOMAIN + cbor2.dumps(header, canonical=True)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=DOMAIN).derive(b"\x33" * 32)
    nonce = b"\x44" * 12  # fixture only: real issuance MUST generate fresh nonces
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    assert AESGCM(key).decrypt(nonce, ciphertext, aad) == plaintext
    envelope = cbor2.dumps({**header, 4: nonce, 5: ciphertext}, canonical=True)
    return envelope, len(plaintext), len(ciphertext)


def ndef_mime_record(payload):
    """One MB/ME MIME record, no ID, no chunking; include actual headers."""
    short = len(payload) <= 255
    header = bytes([0xD2 if short else 0xC2, len(MIME)])
    length = len(payload).to_bytes(1 if short else 4, "big")
    return header + length + MIME + payload


def type2_tlv(message):
    """NDEF Message TLV plus terminator, excluding tag-specific reserved TLVs."""
    size = len(message)
    if size > 65535:
        raise ValueError("NDEF message too large for this Type 2 sizing model")
    length = bytes([size]) if size < 255 else b"\xff" + size.to_bytes(2, "big")
    return b"\x03" + length + message + b"\xfe"


def measure_case(name, issuer, relay, pin="012345", kid=1):
    envelope, plaintext, ciphertext = fixture_envelope(issuer, relay, pin, kid)
    ndef = ndef_mime_record(envelope)
    storage = type2_tlv(ndef)
    text = QR_PREFIX + base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")
    assert base64.urlsafe_b64decode(text[len(QR_PREFIX):] + "=" * (-len(text[len(QR_PREFIX):]) % 4)) == envelope
    return dict(case=name, issuer_utf8=len(issuer.encode()), relay_utf8=len(relay.encode()),
                pin_digits=len(pin), kid=kid, plaintext_cbor=plaintext,
                ciphertext_with_tag=ciphertext, binary_envelope=len(envelope),
                ndef_message=len(ndef), type2_storage=len(storage), qr_characters=len(text))


def measurements():
    return [
        measure_case("short", "https://a.example", "wss://r.example"),
        measure_case("safebox-example", "https://crays.safebox.dev", "wss://spurline.safebox.dev"),
        measure_case("internal-relay", "https://crays.safebox.dev", "ws://relay:7777"),
        measure_case("long-routes", "https://" + "a" * 60 + ".example",
                     "wss://" + "r" * 60 + ".example/" + "p" * 80, pin="01234567", kid=256),
    ]


if __name__ == "__main__":
    print(json.dumps(measurements(), indent=2))
