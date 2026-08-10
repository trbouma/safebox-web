"""Public-only Nostr Silent Payments address derivation.

This module implements the OpenETR NSP public derivation contract. It accepts
an npub and never handles the matching nsec or derives private scan/spend keys.
"""

from __future__ import annotations

import hashlib
import re

import bech32
from coincurve import PublicKey
from monstr.encrypt import Keys


SECP256K1_ORDER = (
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
)
BECH32M_CONST = 0x2BC830A3
SILENT_PAYMENT_SCAN_TAG = "nostr-sp/scan"
SILENT_PAYMENT_SPEND_TAG = "nostr-sp/spend"
_HRP_PATTERN = re.compile(r"[a-z0-9]{1,15}")


def _tagged_hash(tag: str, payload: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode("utf-8")).digest()
    return hashlib.sha256(tag_hash + tag_hash + payload).digest()


def _derive_tweak_scalar(base_pubkey: bytes, tag: str) -> int:
    tweak = int.from_bytes(_tagged_hash(tag, base_pubkey), "big")
    tweak %= SECP256K1_ORDER
    if tweak == 0:
        raise ValueError(f"{tag} derivation produced an invalid zero tweak")
    return tweak


def _tweak_pubkey(base_pubkey: bytes, tweak: int) -> bytes:
    try:
        return PublicKey(base_pubkey).add(
            tweak.to_bytes(32, "big")
        ).format(compressed=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("unable to derive Silent Payments public key") from exc


def _bech32m_encode(hrp: str, data: list[int]) -> str:
    values = bech32.bech32_hrp_expand(hrp) + data
    polymod = (
        bech32.bech32_polymod(values + [0, 0, 0, 0, 0, 0])
        ^ BECH32M_CONST
    )
    checksum = [(polymod >> (5 * (5 - index))) & 31 for index in range(6)]
    return hrp + "1" + "".join(bech32.CHARSET[value] for value in data + checksum)


def _encode_silent_payment_address(
    scan_pubkey: bytes,
    spend_pubkey: bytes,
    *,
    hrp: str,
) -> str:
    if len(scan_pubkey) != 33 or len(spend_pubkey) != 33:
        raise ValueError(
            "Silent Payments scan and spend public keys must be compressed"
        )
    converted = bech32.convertbits(scan_pubkey + spend_pubkey, 8, 5, True)
    if converted is None:
        raise ValueError("unable to encode Silent Payments public keys")
    return _bech32m_encode(hrp, [0, *converted])


def derive_nostr_silent_payment_address(npub: str, *, hrp: str = "sp") -> str:
    """Derive the OpenETR NSP address associated with a Nostr public key.

    Nostr public keys are BIP-340 x-only points and therefore use the even-y
    representative. The tagged-hash tweaks and Bech32m encoding are part of
    the OpenETR NSP compatibility contract.
    """

    normalized_npub = str(npub or "").strip()
    normalized_hrp = str(hrp or "").strip().lower()
    if not normalized_npub.startswith("npub1"):
        raise ValueError("a valid npub is required")
    if _HRP_PATTERN.fullmatch(normalized_hrp) is None:
        raise ValueError("Silent Payments address prefix is invalid")

    try:
        public_key_hex = Keys(pub_k=normalized_npub).public_key_hex()
    except Exception as exc:
        raise ValueError("a valid npub is required") from exc
    if public_key_hex is None or len(public_key_hex) != 64:
        raise ValueError("a valid npub is required")

    try:
        base_pubkey = b"\x02" + bytes.fromhex(public_key_hex)
        # Parsing also rejects an x coordinate that is not on secp256k1.
        PublicKey(base_pubkey)
    except (ValueError, TypeError) as exc:
        raise ValueError("npub does not encode a valid secp256k1 public key") from exc

    scan_tweak = _derive_tweak_scalar(base_pubkey, SILENT_PAYMENT_SCAN_TAG)
    spend_tweak = _derive_tweak_scalar(base_pubkey, SILENT_PAYMENT_SPEND_TAG)
    scan_pubkey = _tweak_pubkey(base_pubkey, scan_tweak)
    spend_pubkey = _tweak_pubkey(base_pubkey, spend_tweak)
    return _encode_silent_payment_address(
        scan_pubkey,
        spend_pubkey,
        hrp=normalized_hrp,
    )
