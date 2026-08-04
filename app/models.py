"""Minimal persistent models for public Safebox Web services."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


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
