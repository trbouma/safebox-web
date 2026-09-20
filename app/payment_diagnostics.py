"""Read-only diagnostics for Lightning-address provider payments."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from typing import Any, Callable, Sequence

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.config import ServiceAcornSettings
from app.database import create_database_engine
from app.models import ClaimedHandle, ProviderPayment, utc_now
from app.service_acorn_worker import service_worker_health


STALE_IN_PROGRESS_SECONDS = 15 * 60
IN_PROGRESS_STATES = {
    "WORKER_QUOTE_CREATING",
    "PAID_RECONCILIATION_PENDING",
    "DELIVERING",
    "RECEIPT_PUBLISHING",
}
REVIEW_STATES = {"FAILED", "DELIVERY_FAILED", "RECEIPT_FAILED"}
PENDING_SETTLEMENT_STATES = {"INVOICE_PENDING", "SETTLEMENT_UNCONFIRMED"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _payment_age_seconds(payment: ProviderPayment) -> int:
    return max(0, int((utc_now() - payment.updated_at).total_seconds()))


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _safe_quote_payload(payload: Any) -> dict[str, Any]:
    """Return only non-secret settlement fields from a mint response."""

    if not isinstance(payload, dict):
        raise ValueError("Mint quote response was not a JSON object")
    return {
        "state": str(payload.get("state") or "UNKNOWN").upper(),
        "amount": payload.get("amount"),
        "amount_paid": payload.get("amount_paid"),
        "amount_issued": payload.get("amount_issued"),
        "updated_at": payload.get("updated_at"),
    }


def _fetch_quote_status(
    payment: ProviderPayment,
    *,
    timeout: float,
    http_get: Callable[..., Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if not payment.mint_quote:
        return None, "Payment has no durable mint quote"
    url = (
        payment.mint.rstrip("/")
        + "/v1/mint/quote/bolt11/"
        + payment.mint_quote
    )
    try:
        response = http_get(url, timeout=timeout)
        response.raise_for_status()
        return _safe_quote_payload(response.json()), None
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return None, f"{type(exc).__name__}: {detail}"


def _payment_issues(
    payment: ProviderPayment,
    registration: ClaimedHandle | None,
    mint_quote: dict[str, Any] | None,
    mint_error: str | None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    age_seconds = _payment_age_seconds(payment)

    if registration is not None:
        if payment.recipient_npub != registration.npub:
            issues.append(
                _issue(
                    "ERROR",
                    "RECIPIENT_IDENTITY_MISMATCH",
                    "Payment recipient npub differs from the handle registration.",
                )
            )
        if payment.recipient_relay.rstrip("/") != registration.home_relay.rstrip(
            "/"
        ):
            issues.append(
                _issue(
                    "ERROR",
                    "RECIPIENT_RELAY_MISMATCH",
                    "Payment recipient relay differs from the handle registration.",
                )
            )

    if payment.status in IN_PROGRESS_STATES and age_seconds > STALE_IN_PROGRESS_SECONDS:
        issues.append(
            _issue(
                "ERROR",
                "STALE_IN_PROGRESS_STATE",
                f"{payment.status} has not changed for {age_seconds} seconds.",
            )
        )
    if payment.status in REVIEW_STATES:
        issues.append(
            _issue(
                "ERROR",
                "MANUAL_REVIEW_REQUIRED",
                payment.error or f"Payment is in {payment.status}.",
            )
        )
    if payment.status == "DELIVERED" and not payment.delivery_event_id:
        issues.append(
            _issue(
                "ERROR",
                "DELIVERY_EVENT_MISSING",
                "Payment is DELIVERED but has no recorded delivery event id.",
            )
        )

    if mint_error:
        issues.append(
            _issue(
                "WARNING",
                "MINT_QUOTE_UNREACHABLE",
                f"Mint quote could not be inspected: {mint_error}",
            )
        )
        return issues
    if mint_quote is None:
        if payment.status not in {"QUOTE_PENDING", "WORKER_QUOTE_CREATING"}:
            issues.append(
                _issue(
                    "ERROR",
                    "MINT_QUOTE_MISSING",
                    "Payment state requires a mint quote, but none is stored.",
                )
            )
        return issues

    mint_state = str(mint_quote["state"])
    amount_paid = int(mint_quote.get("amount_paid") or 0)
    amount_issued = int(mint_quote.get("amount_issued") or 0)
    if (
        mint_state == "PAID"
        and amount_paid >= payment.amount_sat
        and amount_issued == 0
        and payment.status in PENDING_SETTLEMENT_STATES
    ):
        if payment.attempts == 0:
            message = (
                "Mint reports PAID, but the service worker has never recorded a "
                "settlement check. The quote remains recoverable."
            )
            code = "PAID_NOT_POLLED"
        else:
            message = (
                "Mint reports PAID, but Safebox still shows settlement pending. "
                "The quote requires worker reconciliation."
            )
            code = "PAID_NOT_RECONCILED"
        issues.append(_issue("ERROR", code, message))
    if (
        (mint_state == "ISSUED" or amount_issued > 0)
        and payment.status in PENDING_SETTLEMENT_STATES
    ):
        issues.append(
            _issue(
                "ERROR",
                "ISSUED_BUT_APPLICATION_PENDING",
                "Mint reports value issued, but Safebox still shows settlement pending.",
            )
        )
    return issues


def diagnose_handle(
    engine: Engine,
    settings: ServiceAcornSettings,
    handle: str,
    *,
    limit: int = 20,
    problems_only: bool = False,
    timeout: float = 10.0,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Build a redacted, read-only diagnostic report for one local handle."""

    normalized_handle = handle.strip().lower()
    if not normalized_handle:
        raise ValueError("Handle is required")
    if not 1 <= int(limit) <= 200:
        raise ValueError("Limit must be between 1 and 200")
    if timeout <= 0:
        raise ValueError("Timeout must be positive")

    with Session(engine) as session:
        registration = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.claimed_handle == normalized_handle
            )
        ).first()
        payments = list(
            session.exec(
                select(ProviderPayment)
                .where(ProviderPayment.claimed_handle == normalized_handle)
                .order_by(ProviderPayment.id.desc())
                .limit(int(limit))
            )
        )

    global_issues: list[dict[str, str]] = []
    if registration is None:
        global_issues.append(
            _issue(
                "ERROR",
                "HANDLE_NOT_REGISTERED",
                "No local claimed-handle registration was found.",
            )
        )
    try:
        worker = service_worker_health(settings)
        worker_report = {"status": "HEALTHY", **worker}
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        worker_report = {"status": "UNHEALTHY", "error": detail}
        global_issues.append(
            _issue(
                "ERROR",
                "SERVICE_WORKER_UNHEALTHY",
                f"Service Acorn worker is unhealthy: {detail}",
            )
        )

    payment_reports: list[dict[str, Any]] = []
    paid_unissued_amount_sat = 0
    for payment in payments:
        mint_quote, mint_error = _fetch_quote_status(
            payment,
            timeout=timeout,
            http_get=http_get,
        )
        issues = _payment_issues(
            payment,
            registration,
            mint_quote,
            mint_error,
        )
        if mint_quote is not None:
            mint_state = str(mint_quote.get("state") or "UNKNOWN")
            amount_paid = int(mint_quote.get("amount_paid") or 0)
            amount_issued = int(mint_quote.get("amount_issued") or 0)
            if mint_state == "PAID" and amount_paid > 0 and amount_issued == 0:
                paid_unissued_amount_sat += amount_paid
        report = {
            "id": payment.id,
            "payment_id": payment.payment_id,
            "created_at": _iso(payment.created_at),
            "updated_at": _iso(payment.updated_at),
            "age_seconds": _payment_age_seconds(payment),
            "amount_sat": payment.amount_sat,
            "status": payment.status,
            "mint": payment.mint,
            "mint_quote_id": payment.mint_quote,
            "mint_quote": mint_quote,
            "mint_error": mint_error,
            "recipient_npub": payment.recipient_npub,
            "recipient_relay": payment.recipient_relay,
            "settlement_attempts": payment.attempts,
            "delivery_attempts": payment.delivery_attempts,
            "delivery_event_id": payment.delivery_event_id,
            "application_error": payment.error,
            "next_check_at": _iso(payment.next_check_at),
            "issues": issues,
        }
        if not problems_only or issues:
            payment_reports.append(report)

    status_counts = Counter(payment.status for payment in payments)
    payment_issue_count = sum(len(payment["issues"]) for payment in payment_reports)
    return {
        "status": "PROBLEMS_FOUND" if global_issues or payment_issue_count else "OK",
        "generated_at": _iso(utc_now()),
        "handle": normalized_handle,
        "registration": (
            {
                "npub": registration.npub,
                "home_relay": registration.home_relay,
            }
            if registration is not None
            else None
        ),
        "worker": worker_report,
        "queue": {
            "examined": len(payments),
            "status_counts": dict(sorted(status_counts.items())),
            "paid_unissued_amount_sat": paid_unissued_amount_sat,
        },
        "issues": global_issues,
        "payments": payment_reports,
        "problem_count": len(global_issues) + payment_issue_count,
        "read_only": True,
    }


def format_diagnostic_report(report: dict[str, Any]) -> str:
    """Render an operator-oriented report without exposing bearer material."""

    lines = [f"Payment diagnostics: {report['handle']}", ""]
    registration = report.get("registration")
    lines.append("Registration")
    if registration:
        lines.append(f"  Recipient: {registration['npub']}")
        lines.append(f"  Relay:     {registration['home_relay']}")
    else:
        lines.append("  Not found")
    lines.extend(["", "Worker"])
    worker = report["worker"]
    lines.append(f"  Status: {worker['status']}")
    if "heartbeat_age_seconds" in worker:
        lines.append(f"  Heartbeat age: {worker['heartbeat_age_seconds']} seconds")
    if worker.get("error"):
        lines.append(f"  Error: {worker['error']}")
    lines.extend(["", "Queue"])
    lines.append(f"  Examined: {report['queue']['examined']}")
    lines.append(
        "  Paid, unissued: "
        f"{report['queue']['paid_unissued_amount_sat']} sats"
    )
    for status, count in report["queue"]["status_counts"].items():
        lines.append(f"  {status}: {count}")

    for payment in report["payments"]:
        lines.extend(
            [
                "",
                f"Payment {payment['id']} — {payment['amount_sat']} sats",
                f"  Application: {payment['status']}",
                f"  Quote:       {payment['mint_quote_id'] or 'none'}",
                f"  Checks:      {payment['settlement_attempts']}",
                f"  Deliveries:  {payment['delivery_attempts']}",
            ]
        )
        mint_quote = payment.get("mint_quote")
        if mint_quote:
            lines.append(f"  Mint:         {mint_quote['state']}")
            lines.append(f"  Amount paid:  {mint_quote['amount_paid'] or 0}")
            lines.append(f"  Amount issued:{mint_quote['amount_issued'] or 0}")
        elif payment.get("mint_error"):
            lines.append(f"  Mint check:   {payment['mint_error']}")
        if payment["issues"]:
            lines.append("  Findings:")
            for issue in payment["issues"]:
                lines.append(
                    f"    {issue['severity']} {issue['code']}: {issue['message']}"
                )
        else:
            lines.append("  Findings: none")

    if report["issues"]:
        lines.extend(["", "Global findings"])
        for issue in report["issues"]:
            lines.append(f"  {issue['severity']} {issue['code']}: {issue['message']}")
    lines.extend(
        [
            "",
            f"Result: {report['status']} ({report['problem_count']} finding(s))",
            "No wallet or payment state was changed.",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safebox-payment-diagnostics",
        description="Inspect one local handle's provider-payment pipeline safely.",
    )
    parser.add_argument("handle", help="local claimed handle, without the domain")
    parser.add_argument("--limit", type=int, default=20, help="payments to inspect")
    parser.add_argument(
        "--problems-only",
        action="store_true",
        help="omit payment rows with no detected issue",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="per-mint request timeout in seconds",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = ServiceAcornSettings.from_env()
    engine = create_database_engine(settings.database_url)
    try:
        report = diagnose_handle(
            engine,
            settings,
            args.handle,
            limit=args.limit,
            problems_only=args.problems_only,
            timeout=args.timeout,
        )
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_diagnostic_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
