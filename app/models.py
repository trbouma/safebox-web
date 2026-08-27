"""Minimal persistent models for public Safebox Web services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return naive UTC for consistent SQLite and PostgreSQL comparisons."""

    return datetime.now(UTC).replace(tzinfo=None)


class ClaimedHandle(SQLModel, table=True):
    """A public NIP-05 handle controlled by one Acorn component."""

    __tablename__ = "claimed_handle"
    __table_args__ = (
        UniqueConstraint("claimed_handle", name="uq_claimed_handle_name"),
        UniqueConstraint("npub", name="uq_claimed_handle_npub"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    claimed_handle: str = Field(nullable=False)
    npub: str = Field(nullable=False)
    home_relay: str = Field(nullable=False)


class ProviderPayment(SQLModel, table=True):
    """Durable LNURL invoice and ecash-delivery state."""

    __tablename__ = "provider_payment"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_provider_payment_payment_id"),
        UniqueConstraint("mint_quote", name="uq_provider_payment_mint_quote"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    payment_id: str = Field(nullable=False, index=True)
    claimed_handle: str = Field(nullable=False, index=True)
    recipient_npub: str = Field(nullable=False)
    recipient_relay: str = Field(nullable=False)
    amount_msat: int = Field(nullable=False)
    amount_sat: int = Field(nullable=False)
    comment: Optional[str] = Field(default=None, nullable=True)
    lnurl_metadata: str = Field(nullable=False)
    status: str = Field(default="QUOTE_PENDING", nullable=False, index=True)
    mint: str = Field(nullable=False)
    mint_quote: Optional[str] = Field(default=None, nullable=True)
    invoice: Optional[str] = Field(default=None, nullable=True)
    delivery_event_id: Optional[str] = Field(default=None, nullable=True)
    error: Optional[str] = Field(default=None, nullable=True)
    attempts: int = Field(default=0, nullable=False)
    delivery_attempts: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    next_check_at: Optional[datetime] = Field(default=None, nullable=True, index=True)


class ProviderIdentity(SQLModel, table=True):
    """Public signing identity of the singleton provider Acorn."""

    __tablename__ = "provider_identity"

    name: str = Field(primary_key=True)
    nostr_pubkey: str = Field(nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class ProviderZap(SQLModel, table=True):
    """Validated NIP-57 context associated with one provider payment."""

    __tablename__ = "provider_zap"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_provider_zap_payment_id"),
        UniqueConstraint("request_event_id", name="uq_provider_zap_request_event_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    payment_id: str = Field(nullable=False, index=True)
    request_event_id: str = Field(nullable=False, index=True)
    request_json: str = Field(nullable=False)
    receipt_relays_json: str = Field(nullable=False)
    receipt_event_id: Optional[str] = Field(default=None, nullable=True)
    receipt_json: Optional[str] = Field(default=None, nullable=True)
    receipt_error: Optional[str] = Field(default=None, nullable=True)


class CurrencyRate(SQLModel, table=True):
    """Last-known-good informational fiat value for one bitcoin."""

    __tablename__ = "currency_rate"

    currency_code: str = Field(primary_key=True)
    fiat_per_btc: float = Field(nullable=False)
    currency_symbol: str = Field(nullable=False)
    currency_description: str = Field(nullable=False)
    source: str = Field(nullable=False)
    fetched_at: datetime = Field(nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class WebWorkerHeartbeat(SQLModel, table=True):
    """Non-secret liveness marker for one Safebox Web process."""

    __tablename__ = "web_worker_heartbeat"

    worker_id: str = Field(primary_key=True)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    heartbeat_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        index=True,
    )


class FundsFinalizationJob(SQLModel, table=True):
    """Non-secret coordination state for one recipient finalization task."""

    __tablename__ = "funds_finalization_job"

    npub: str = Field(primary_key=True)
    status: str = Field(default="RUNNING", nullable=False, index=True)
    owner_token: str = Field(nullable=False)
    owner_worker_id: Optional[str] = Field(
        default=None,
        nullable=True,
        index=True,
    )
    phase: str = Field(default="STARTING", nullable=False)
    discovered_count: int = Field(default=0, nullable=False)
    discovered_amount: int = Field(default=0, nullable=False)
    confirmed_count: int = Field(default=0, nullable=False)
    confirmed_amount: int = Field(default=0, nullable=False)
    pending_count: int = Field(default=0, nullable=False)
    pending_amount: int = Field(default=0, nullable=False)
    error: Optional[str] = Field(default=None, nullable=True)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    lease_expires_at: datetime = Field(nullable=False, index=True)


class ClearAcceptanceJob(SQLModel, table=True):
    """Non-secret coordination state for one Clear acceptance task per Acorn."""

    __tablename__ = "clear_acceptance_job"

    npub: str = Field(primary_key=True)
    event_id: str = Field(nullable=False)
    status: str = Field(default="RUNNING", nullable=False, index=True)
    owner_token: str = Field(nullable=False)
    owner_worker_id: Optional[str] = Field(
        default=None,
        nullable=True,
        index=True,
    )
    phase: str = Field(default="STARTING", nullable=False)
    amount: int = Field(default=0, nullable=False)
    mint: Optional[str] = Field(default=None, nullable=True)
    unit: Optional[str] = Field(default=None, nullable=True)
    error: Optional[str] = Field(default=None, nullable=True)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    lease_expires_at: datetime = Field(nullable=False, index=True)


class OutgoingPaymentJob(SQLModel, table=True):
    """Non-secret coordination state for one outgoing Lightning payment."""

    __tablename__ = "outgoing_payment_job"

    npub: str = Field(primary_key=True)
    owner_token: str = Field(nullable=False)
    owner_worker_id: Optional[str] = Field(default=None, nullable=True, index=True)
    payment_kind: str = Field(nullable=False)
    recipient: str = Field(nullable=False)
    amount: int = Field(nullable=False)
    tendered_amount: Optional[float] = Field(default=None, nullable=True)
    tendered_currency: str = Field(default="SAT", nullable=False)
    status: str = Field(default="RUNNING", nullable=False, index=True)
    phase: str = Field(default="STARTING", nullable=False)
    total_fees: Optional[int] = Field(default=None, nullable=True)
    mint_fees: Optional[int] = Field(default=None, nullable=True)
    lightning_fee: Optional[int] = Field(default=None, nullable=True)
    lightning_fee_reserve: Optional[int] = Field(default=None, nullable=True)
    lightning_fee_return: Optional[int] = Field(default=None, nullable=True)
    message: Optional[str] = Field(default=None, nullable=True)
    error: Optional[str] = Field(default=None, nullable=True)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    lease_expires_at: datetime = Field(nullable=False, index=True)


class DepositFinalizationJob(SQLModel, table=True):
    """Non-secret coordination state for one attached-Acorn deposit poller."""

    __tablename__ = "deposit_finalization_job"

    quote_hash: str = Field(primary_key=True)
    npub: str = Field(nullable=False, index=True)
    owner_token: str = Field(nullable=False)
    owner_worker_id: Optional[str] = Field(default=None, nullable=True, index=True)
    amount: int = Field(nullable=False)
    mint: str = Field(nullable=False)
    status: str = Field(default="RUNNING", nullable=False, index=True)
    phase: str = Field(default="STARTING", nullable=False)
    error: Optional[str] = Field(default=None, nullable=True)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    lease_expires_at: datetime = Field(nullable=False, index=True)
