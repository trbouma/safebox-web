import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ProviderPayment, ProviderPaymentIntervention
from app.operator_payments import ClosePaymentRequest, close_payment


def test_audit_failure_rolls_back_payment_closure(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ProviderPayment(
            payment_id="test", claimed_handle="trbouma", recipient_npub="test",
            recipient_relay="wss://relay.example", amount_msat=111000, amount_sat=111,
            lnurl_metadata="[]", mint="https://mint.example", status="DELIVERY_FAILED",
            error="Original error",
        ))
        session.commit()
    original = Session.add

    def fail_audit(self, instance, **kwargs):
        if isinstance(instance, ProviderPaymentIntervention):
            raise RuntimeError("Audit unavailable")
        return original(self, instance, **kwargs)

    monkeypatch.setattr(Session, "add", fail_audit)
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        close_payment(engine, "test", ClosePaymentRequest(
            handle="trbouma", amount_sat=111, operator="tester",
            reason="Test cleanup", confirmed=True,
        ))
    with Session(engine) as session:
        payment = session.exec(select(ProviderPayment)).one()
        assert payment.status == "DELIVERY_FAILED"
        assert payment.error == "Original error"
        assert session.exec(select(ProviderPaymentIntervention)).all() == []
    engine.dispose()
