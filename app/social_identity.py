"""Public-only social npub validation; never accepts private keys."""
from stroma import Keys


def validate_social_npub(value: str) -> str:
    value = value.strip()
    if value == value.upper():
        value = value.lower()
    if len(value) != 63 or not value.startswith('npub1'):
        raise ValueError('Enter a valid public npub, not a private key.')
    try:
        return Keys(pub_k=value).public_key_bech32()
    except Exception:
        raise ValueError('Enter a valid public npub, not a private key.') from None
