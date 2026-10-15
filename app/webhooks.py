"""PayPal webhooks: verify the signature with PayPal, ignore replays, update payouts."""

import logging
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import ledger
from app.db import Base, get_session
from app.payments import PaymentProvider

log = logging.getLogger(__name__)
router = APIRouter()

PAYOUT_SUCCEEDED = {"PAYMENT.PAYOUTS-ITEM.SUCCEEDED"}
PAYOUT_FAILED = {
    "PAYMENT.PAYOUTS-ITEM.FAILED",
    "PAYMENT.PAYOUTS-ITEM.DENIED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.CANCELED",
}
# UNCLAIMED and HELD are not final: the item can still succeed or come back as RETURNED.


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


@lru_cache
def get_provider() -> PaymentProvider:
    from app.paypal import PayPalSandbox

    return PayPalSandbox()


@router.post("/webhooks/paypal")
async def paypal_webhook(
    request: Request,
    session: Session = Depends(get_session),
    provider: PaymentProvider = Depends(get_provider),
):
    event = await request.json()
    if not provider.verify_webhook(dict(request.headers), event):
        raise HTTPException(status_code=400, detail="invalid signature")

    event_id, event_type = event["id"], event.get("event_type", "")
    if session.get(WebhookEvent, event_id) is not None:
        return {"status": "duplicate"}

    if event_type in PAYOUT_SUCCEEDED | PAYOUT_FAILED:
        ref = event.get("resource", {}).get("payout_item", {}).get("sender_item_id")
        payout = ledger.finish_payout(session, ref, succeeded=event_type in PAYOUT_SUCCEEDED) if ref else None
        log.info("webhook %s %s payout_ref=%s known=%s", event_id, event_type, ref, payout is not None)
    else:
        log.info("webhook %s %s stored, no action", event_id, event_type)

    session.add(WebhookEvent(event_id=event_id, event_type=event_type))
    session.commit()
    return {"status": "ok"}
