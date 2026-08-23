"""Jinja2 environment for Safebox Web's server-rendered pages."""

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates


TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))

_MAIN_TEMPLATES = {"home.html", "onboard.html", "wallet.html"}
_LONG_LIST_TEMPLATES = {
    "control_history.html",
    "clear_transactions.html",
    "rates.html",
    "record.html",
    "records.html",
    "silent_payment_receipts.html",
    "transactions.html",
}
_PARENT_NAVIGATION = {
    "balances.html": ("/wallet", "Back to Wallet"),
    "blob_upload.html": ("/records", "Back to Records"),
    "clear_transactions.html": ("/balances", "Back to Balances"),
    "clear_acceptance_status.html": ("/balances", "Back to Balances"),
    "control_history.html": (None, "Back to Record"),
    "receive_funds.html": ("/balances", "Back to Balances"),
    "receive_funds_request.html": ("/receive-funds", "Back to Receive Funds"),
    "deferred_recovery.html": ("/recovery", "Back to Recovery"),
    "deferred_recovery_warning.html": ("/wallet", "Back to Wallet"),
    "handle.html": ("/wallet", "Back to Wallet"),
    "onboard_invite.html": ("/wallet", "Back to Wallet"),
    "pay.html": ("/balances", "Back to Balances"),
    "pay_invoice.html": ("/scan/lightning", "Back to Scanner"),
    "payment_result.html": ("/balances", "Back to Balances"),
    "record.html": ("/records", "Back to Records"),
    "record_deleted.html": ("/records", "Back to Records"),
    "record_form.html": ("/records", "Back to Records"),
    "record_protection_enable.html": ("/wallet", "Back to Wallet"),
    "record_protection_recovery.html": ("/wallet", "Back to Wallet"),
    "record_protection_warning.html": ("/wallet", "Back to Wallet"),
    "records.html": ("/wallet", "Back to Wallet"),
    "scan_lightning_address.html": ("/wallet", "Back to Wallet"),
    "silent_payment_receipts.html": ("/wallet", "Back to Wallet"),
    "silent_payment_sweep_result.html": ("/wallet", "Back to Wallet"),
    "silent_payment_sweep_review.html": ("/wallet", "Back to Wallet"),
    "transactions.html": ("/balances", "Back to Balances"),
}


def render_template(template_name: str, **context: Any) -> str:
    """Render a complete HTML representation without introducing browser state."""

    context.setdefault("show_page_navigation", template_name not in _MAIN_TEMPLATES)
    context.setdefault("repeat_page_navigation", template_name in _LONG_LIST_TEMPLATES)
    context.setdefault("home_url", "/")
    parent = _PARENT_NAVIGATION.get(template_name)
    if parent is not None:
        context.setdefault("page_back_url", parent[0])
        context.setdefault("page_back_label", parent[1])
    if template_name in {"record_share.html", "record_share_qr.html", "record_share_stopped.html"}:
        context.setdefault("page_back_url", context.get("record_url"))
        context.setdefault("page_back_label", "Back to Record")
    elif template_name in {"record_present.html", "record_present_qr.html"}:
        context.setdefault("page_back_url", context.get("record_url"))
        context.setdefault("page_back_label", "Back to Record")
    elif template_name == "control_history.html":
        context["page_back_url"] = context.get("record_url")
        context["page_back_label"] = "Back to Record"
    elif template_name == "record_transfer_review.html":
        context.setdefault("page_back_url", "/scan/lightning")
        context.setdefault("page_back_label", "Back to Scanner")
    elif template_name == "record_transfer_result.html":
        context.setdefault(
            "page_back_url",
            context.get("record_url") or "/scan/lightning",
        )
        context.setdefault(
            "page_back_label",
            "Back to Record" if context.get("record_url") else "Back to Scanner",
        )
    elif template_name == "record_deleted.html" and context.get("return_url"):
        context["page_back_url"] = context["return_url"]
        context["page_back_label"] = "Back to Record"
    return templates.env.get_template(template_name).render(**context)
