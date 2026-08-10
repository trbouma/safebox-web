from decimal import getcontext

import pytest

import app.bitcoin_silent_payments as gateway


TXID = "ab" * 32
SIGNED_TXID = "cd" * 32


def _raw_scan_result() -> dict:
    return {
        "silent_payment_address": "sp1example",
        "transactions": [
            {
                "txid": TXID,
                "matched_outputs": [
                    {
                        "vout": 2,
                        "value": 12_345,
                        "scriptpubkey_address": "bc1psource",
                        "priv_key_tweak_hex": "private-derivation-material",
                        "output_pubkey_hex": "public-but-internal",
                    }
                ],
            }
        ],
    }


def _raw_sweep_result() -> dict:
    return {
        "tx_hex": "signed-transaction-must-not-reach-browser",
        "txid": SIGNED_TXID,
        "matched_txid": TXID,
        "matched_vout": 2,
        "source_address": "bc1psource",
        "destination_address": "bc1pdestination",
        "matched_value": 12_345,
        "amount_sats": 12_145,
        "fee_sats": 200,
        "fee_rate": 2.0,
        "vsize": 100,
        "matched_tweak_hex": "private-derivation-material",
    }


def test_openetr_import_does_not_change_process_decimal_context() -> None:
    before = getcontext().copy()

    operations = gateway._load_openetr_operations()

    after = getcontext()
    assert len(operations) == 4
    assert after.prec == before.prec
    assert after.traps == before.traps


def test_detection_returns_only_public_receipt_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "scan_silent_payment_receipts",
        lambda *args, **kwargs: _raw_scan_result(),
    )
    monkeypatch.setattr(
        gateway,
        "fetch_blockstream_address_utxos",
        lambda *args, **kwargs: [
            {
                "txid": TXID,
                "vout": 2,
                "value": 12_345,
                "confirmed": True,
                "block_height": 900_000,
            }
        ],
    )

    result = gateway.detect_silent_payment_receipts(
        nsec="nsec-not-returned",
        txid=TXID,
        api_base="https://bitcoin.example/api",
        timeout=5,
    )

    assert result["matches"] == [
        {
            "txid": TXID,
            "vout": 2,
            "value": 12_345,
            "source_address": "bc1psource",
            "confirmed": True,
            "block_height": 900_000,
            "availability": "available",
        }
    ]
    assert "private" not in repr(result)
    assert "nsec" not in repr(result)


def test_sweep_preview_does_not_return_signed_transaction_or_tweak(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "create_silent_payment_sweep_result",
        lambda *args, **kwargs: _raw_sweep_result(),
    )

    result = gateway.create_silent_payment_sweep_preview(
        nsec="nsec-not-returned",
        txid=TXID,
        vout=2,
        destination_address="bc1pdestination",
        fee_rate=2,
        api_base="https://bitcoin.example/api",
        timeout=5,
    )

    assert result["txid"] == SIGNED_TXID
    assert result["fee_sats"] == 200
    assert "tx_hex" not in result
    assert "tweak" not in repr(result)
    assert "nsec" not in repr(result)


def test_broadcast_rejects_unexpected_backend_transaction_id(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "create_silent_payment_sweep_result",
        lambda *args, **kwargs: _raw_sweep_result(),
    )
    monkeypatch.setattr(
        gateway,
        "broadcast_blockstream_transaction",
        lambda *args, **kwargs: "ef" * 32,
    )

    with pytest.raises(gateway.BitcoinGatewayError, match="unexpected transaction id"):
        gateway.broadcast_silent_payment_sweep(
            nsec="nsec",
            txid=TXID,
            vout=2,
            destination_address="bc1pdestination",
            fee_rate=2,
            api_base="https://bitcoin.example/api",
            timeout=5,
        )


def test_broadcast_failure_reports_expected_txid_and_stops(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "create_silent_payment_sweep_result",
        lambda *args, **kwargs: _raw_sweep_result(),
    )

    def fail_broadcast(*args, **kwargs):
        raise gateway.click.ClickException("backend timed out")

    monkeypatch.setattr(gateway, "broadcast_blockstream_transaction", fail_broadcast)

    with pytest.raises(gateway.BitcoinGatewayError) as exc_info:
        gateway.broadcast_silent_payment_sweep(
            nsec="nsec",
            txid=TXID,
            vout=2,
            destination_address="bc1pdestination",
            fee_rate=2,
            api_base="https://bitcoin.example/api",
            timeout=5,
        )

    assert "uncertain" in str(exc_info.value)
    assert SIGNED_TXID in str(exc_info.value)
