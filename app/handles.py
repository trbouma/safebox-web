"""Deterministic default handles for newly onboarded Acorns."""

from __future__ import annotations

import re

from acorn.func_utils import generate_name_from_hex


PUBKEY_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def default_handle_from_pubkey(pubkey_hex: str, *, attempt: int = 0) -> str:
    """Return a BIP39-word handle derived from an Acorn public key.

    Safebox-2 split the first 32 public-key bits into two 11-bit BIP39 word
    indexes and a 10-bit number.  The original suffix could reach 1023.  This
    variant preserves the word derivation while constraining the public handle
    suffix to 0..999.  ``attempt`` deterministically advances that suffix when
    the preferred name is already claimed.
    """

    normalized = str(pubkey_hex or "").strip().lower()
    if not PUBKEY_HEX_PATTERN.fullmatch(normalized):
        raise ValueError("Public key must be a 32-byte hexadecimal value.")
    if not 0 <= attempt < 1000:
        raise ValueError("Handle allocation attempt must be between 0 and 999.")

    first_word, second_word, raw_suffix = generate_name_from_hex(normalized).split(
        "-", 2
    )
    suffix = (int(raw_suffix) % 1000 + attempt) % 1000
    return f"{first_word}{second_word}{suffix}"
