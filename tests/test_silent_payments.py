import pytest

from acorn import BitcoinCapabilityError, derive_nostr_silent_payment_address


OPENETR_SCALAR_ONE_NPUB = (
    "npub10xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqpkge6d"
)
OPENETR_SCALAR_ONE_NSP_ADDRESS = (
    "sp1qqt0uh8dlt9ypxxyl5s9p03ym2t87dxgzqsp8vndjzl4cfn7h4hckqqkh372"
    "d773sgqpka3qlm9kpyyf6p9nmdkzqpepdhhtq79klfq3zlq750cmd"
)


def test_public_nsp_derivation_matches_openetr_vector() -> None:
    assert (
        derive_nostr_silent_payment_address(OPENETR_SCALAR_ONE_NPUB)
        == OPENETR_SCALAR_ONE_NSP_ADDRESS
    )


def test_public_nsp_derivation_is_deterministic() -> None:
    first = derive_nostr_silent_payment_address(OPENETR_SCALAR_ONE_NPUB)
    second = derive_nostr_silent_payment_address(OPENETR_SCALAR_ONE_NPUB)

    assert first == second
    assert first.startswith("sp1")


@pytest.mark.parametrize("value", ["", "npub1", "hello"])
def test_public_nsp_derivation_rejects_invalid_input(value: str) -> None:
    with pytest.raises(BitcoinCapabilityError, match="valid nsec or npub"):
        derive_nostr_silent_payment_address(value)
