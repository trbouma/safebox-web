"""Guarded provider-payment intervention for the authenticated root CLI."""
import json

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, field_validator
from sqlalchemy import update
from sqlmodel import Session, select

from app.models import ProviderPayment, ProviderPaymentIntervention, utc_now


class ClosePaymentRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=200)
    amount_sat: int = Field(gt=0, strict=True)
    operator: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    confirmed: StrictBool = False

    @field_validator("handle", "operator", "reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()


def payment_view(payment):
    return {key: getattr(payment, key) for key in (
        "id", "payment_id", "claimed_handle", "amount_sat", "status", "mint",
        "delivery_event_id", "error", "delivery_attempts",
    )}


def inspect_payments(engine, *, handle=None, payment_id=None):
    with Session(engine) as session:
        query = select(ProviderPayment)
        if payment_id is not None:
            query = query.where(ProviderPayment.payment_id == payment_id)
        elif handle:
            query = query.where(ProviderPayment.claimed_handle == handle)
        else:
            raise HTTPException(400, "A handle or payment ID is required")
        rows = session.exec(query.order_by(ProviderPayment.id.desc()).limit(100)).all()
        if payment_id and not rows:
            raise HTTPException(404, "Payment not found")
        result = {"payments": [payment_view(row) for row in rows]}
        if payment_id:
            audits = session.exec(select(ProviderPaymentIntervention).where(
                ProviderPaymentIntervention.payment_id == payment_id
            ).order_by(ProviderPaymentIntervention.id)).all()
            result["interventions"] = [{
                "id": audit.id, "operator": audit.operator, "reason": audit.reason,
                "created_at": audit.created_at.isoformat(),
                "before": json.loads(audit.before_json), "after": json.loads(audit.after_json),
            } for audit in audits]
        return result


def close_payment(engine, payment_id, request: ClosePaymentRequest):
    with Session(engine) as session:
        payment = session.exec(select(ProviderPayment).where(
            ProviderPayment.payment_id == payment_id
        )).first()
        if payment is None:
            raise HTTPException(404, "Payment not found")
        if payment.claimed_handle != request.handle or payment.amount_sat != request.amount_sat:
            raise HTTPException(409, "Handle or amount does not match; inspect the payment again")
        if payment.status != "DELIVERY_FAILED" or payment.delivery_event_id is not None:
            raise HTTPException(409, "Only DELIVERY_FAILED payments without a delivery event can be closed")
        before = payment_view(payment)
        after = {**before, "status": "FAILED"}
        result = {
            "dry_run": not request.confirmed, "before": before, "after": after,
            "operator": request.operator, "reason": request.reason,
            "warning": "Closes an abandoned/test payment without retrying, refunding, or asserting delivery. Any unpaid obligation is not settled by this action.",
        }
        if not request.confirmed:
            return result
        changed = session.execute(update(ProviderPayment).where(
            ProviderPayment.id == payment.id,
            ProviderPayment.status == "DELIVERY_FAILED",
            ProviderPayment.delivery_event_id.is_(None),
            ProviderPayment.updated_at == payment.updated_at,
            ProviderPayment.claimed_handle == request.handle,
            ProviderPayment.amount_sat == request.amount_sat,
        ).values(status="FAILED", next_check_at=None, updated_at=utc_now()))
        if changed.rowcount != 1:
            raise HTTPException(409, "Payment changed concurrently; inspect it again")
        audit = ProviderPaymentIntervention(
            payment_id=payment_id, operator=request.operator, reason=request.reason,
            before_json=json.dumps(before), after_json=json.dumps(after),
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        result["audit_id"] = audit.id
        return result
