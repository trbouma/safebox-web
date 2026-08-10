"""Safebox Web adapter around OpenETR Silent Payment operations."""

from __future__ import annotations

from decimal import getcontext, setcontext
from typing import Any

import click

class BitcoinGatewayError(RuntimeError):
    """A user-safe Silent Payment gateway failure."""


def _load_openetr_operations():
    """Import OpenETR without retaining BTClib's global Decimal trap changes."""

    decimal_context = getcontext().copy()
    try:
        from openetr.bitcoin import (
            broadcast_blockstream_transaction as broadcast_operation,
            fetch_blockstream_address_utxos as fetch_utxos_operation,
        )
        from openetr.silent_payments import (
            create_silent_payment_sweep_result as create_sweep_operation,
            scan_silent_payment_receipts as scan_operation,
        )
    finally:
        setcontext(decimal_context)
    return (
        broadcast_operation,
        fetch_utxos_operation,
        create_sweep_operation,
        scan_operation,
    )


def broadcast_blockstream_transaction(*args, **kwargs):
    operation, _, _, _ = _load_openetr_operations()
    return operation(*args, **kwargs)


def fetch_blockstream_address_utxos(*args, **kwargs):
    _, operation, _, _ = _load_openetr_operations()
    return operation(*args, **kwargs)


def create_silent_payment_sweep_result(*args, **kwargs):
    _, _, operation, _ = _load_openetr_operations()
    return operation(*args, **kwargs)


def scan_silent_payment_receipts(*args, **kwargs):
    _, _, _, operation = _load_openetr_operations()
    return operation(*args, **kwargs)


def _gateway_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except click.ClickException as exc:
        raise BitcoinGatewayError(str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise BitcoinGatewayError(str(exc) or "The Bitcoin operation failed.") from exc


def detect_silent_payment_receipts(
    *,
    nsec: str,
    txid: str,
    api_base: str,
    timeout: float,
) -> dict[str, Any]:
    """Detect and sanitize matching outputs from one targeted transaction."""

    result = _gateway_call(
        scan_silent_payment_receipts,
        nsec,
        [txid],
        api_base=api_base,
        timeout=timeout,
        mode="nsp",
    )
    transactions = result.get("transactions") or []
    if not transactions:
        raise BitcoinGatewayError("The transaction could not be inspected.")

    transaction = transactions[0]
    sanitized_matches: list[dict[str, Any]] = []
    for match in transaction.get("matched_outputs") or []:
        address = str(match.get("scriptpubkey_address") or "")
        output_txid = str(transaction.get("txid") or txid).lower()
        vout = int(match.get("vout", 0))
        utxos = _gateway_call(
            fetch_blockstream_address_utxos,
            address,
            api_base=api_base,
            timeout=timeout,
        )
        exact_utxo = next(
            (
                item
                for item in utxos
                if str(item.get("txid") or "").lower() == output_txid
                and int(item.get("vout", -1)) == vout
            ),
            None,
        )
        if exact_utxo is None:
            availability = "spent_or_unavailable"
            confirmed = False
            block_height = 0
        else:
            confirmed = bool(exact_utxo.get("confirmed"))
            availability = "available" if confirmed else "unconfirmed"
            block_height = int(exact_utxo.get("block_height", 0) or 0)

        sanitized_matches.append(
            {
                "txid": output_txid,
                "vout": vout,
                "value": int(match.get("value", 0)),
                "source_address": address,
                "confirmed": confirmed,
                "block_height": block_height,
                "availability": availability,
            }
        )

    return {
        "txid": str(transaction.get("txid") or txid).lower(),
        "silent_payment_address": str(result.get("silent_payment_address") or ""),
        "matches": sanitized_matches,
    }


def create_silent_payment_sweep_preview(
    *,
    nsec: str,
    txid: str,
    vout: int,
    destination_address: str,
    fee_rate: float,
    api_base: str,
    timeout: float,
) -> dict[str, Any]:
    """Build a signed sweep in memory and return public review fields only."""

    raw = _gateway_call(
        create_silent_payment_sweep_result,
        nsec,
        txid,
        destination_address,
        fee_rate,
        api_base,
        timeout,
        vout,
    )
    return _sanitize_sweep_result(raw)


def broadcast_silent_payment_sweep(
    *,
    nsec: str,
    txid: str,
    vout: int,
    destination_address: str,
    fee_rate: float,
    api_base: str,
    timeout: float,
) -> dict[str, Any]:
    """Rebuild against live state, broadcast once, and return public evidence."""

    raw = _gateway_call(
        create_silent_payment_sweep_result,
        nsec,
        txid,
        destination_address,
        fee_rate,
        api_base,
        timeout,
        vout,
    )
    expected_txid = str(raw.get("txid") or "").lower()
    try:
        broadcast_txid = str(
            _gateway_call(
                broadcast_blockstream_transaction,
                str(raw["tx_hex"]),
                api_base,
                timeout,
            )
        ).strip().lower()
    except BitcoinGatewayError as exc:
        raise BitcoinGatewayError(
            "The broadcast result is uncertain. Do not retry automatically; "
            f"inspect the expected transaction id {expected_txid or 'unknown'}. "
            f"Backend response: {exc}"
        ) from exc
    if not expected_txid or broadcast_txid != expected_txid:
        raise BitcoinGatewayError(
            "The Bitcoin backend returned an unexpected transaction id. "
            "Do not retry automatically; inspect the expected transaction id "
            f"{expected_txid or 'unknown'}."
        )
    result = _sanitize_sweep_result(raw)
    result["broadcast_txid"] = broadcast_txid
    return result


def _sanitize_sweep_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Remove signed transaction and private derivation material."""

    return {
        "txid": str(raw.get("txid") or "").lower(),
        "receipt_txid": str(raw.get("matched_txid") or "").lower(),
        "vout": int(raw.get("matched_vout", 0)),
        "source_address": str(raw.get("source_address") or ""),
        "destination_address": str(raw.get("destination_address") or ""),
        "matched_value": int(raw.get("matched_value", 0)),
        "amount_sats": int(raw.get("amount_sats", 0)),
        "fee_sats": int(raw.get("fee_sats", 0)),
        "fee_rate": float(raw.get("fee_rate", 0)),
        "vsize": int(raw.get("vsize", 0)),
    }
