"""Tests for the design-only size model, not production credential issuance."""
import runpy
from pathlib import Path

import cbor2
import pytest

model = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/measure_nfc_card_size.py"))


@pytest.mark.parametrize("size,header,length_bytes", [(254, 0xD2, 1), (255, 0xD2, 1), (256, 0xC2, 4)])
def test_ndef_short_and_long_lengths(size, header, length_bytes):
    payload = b"x" * size
    result = model["ndef_mime_record"](payload)
    mime = model["MIME"]
    assert result[0] == header
    assert result[1] == len(mime)
    assert int.from_bytes(result[2:2 + length_bytes], "big") == size
    assert result[2 + length_bytes:] == mime + payload


@pytest.mark.parametrize("size,overhead", [(254, 3), (255, 5), (256, 5)])
def test_type2_extended_length_boundary(size, overhead):
    message = b"x" * size
    result = model["type2_tlv"](message)
    assert len(result) == size + overhead
    assert result[0] == 3 and result[-1] == 254
    if size >= 255:
        assert result[1] == 255
        assert int.from_bytes(result[2:4], "big") == size


def test_real_encryption_envelope_fields_and_roundtrip():
    envelope, plaintext, ciphertext = model["fixture_envelope"]("https://a.example", "wss://r.example")
    decoded = cbor2.loads(envelope)
    assert set(decoded) == set(range(6))
    assert decoded[0] == 1 and decoded[1] == 1
    assert len(decoded[4]) == 12
    assert len(decoded[5]) == ciphertext == plaintext + 16
    assert b"012345" not in envelope
    assert b"wss://r.example" not in envelope


def test_sizing_is_deterministic_and_counts_all_layers():
    rows = model["measurements"]()
    assert rows == model["measurements"]()
    for row in rows:
        assert row["plaintext_cbor"] < row["ciphertext_with_tag"] < row["binary_envelope"] < row["ndef_message"] < row["type2_storage"]
        assert row["qr_characters"] > row["binary_envelope"]
