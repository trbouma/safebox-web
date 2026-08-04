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
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    next_check_at: Optional[datetime] = Field(default=None, nullable=True, index=True)
