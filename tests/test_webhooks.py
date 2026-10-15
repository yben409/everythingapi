import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import ledger
from app.db import get_session, init_db
from app.ledger import Payout, Worker
from app.main import app
from app.webhooks import WebhookEvent, get_provider


class FakeProvider:
    def __init__(self, valid=True):
        self.valid = valid

    def verify_webhook(self, headers, body):
        return self.valid


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    Session = sessionmaker(bind=engine)

    def override():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    with Session() as s:
        yield s
    app.dependency_overrides.clear()


@pytest.fixture
def client(db):
    return TestClient(app)


def payout_event(event_id, event_type, ref="p1"):
    return {"id": event_id, "event_type": event_type, "resource": {"payout_item": {"sender_item_id": ref}}}


def pending_payout(db, cents=600):
    ledger.topup(db, "w1", 1000, ref="CAPTURE-1")
    ledger.hold(db, "w1", "task-1", "worker-a", 1000)
    ledger.settle(db, "task-1", fee_percent=0)
    ledger.start_payout(db, "worker-a", cents, ref="p1")


def refresh(db):
    db.expire_all()


def test_bad_signature_rejected(client, db):
    app.dependency_overrides[get_provider] = lambda: FakeProvider(valid=False)
    r = client.post("/webhooks/paypal", json=payout_event("E1", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"))
    assert r.status_code == 400
    assert db.get(WebhookEvent, "E1") is None


def test_payout_succeeded(client, db):
    pending_payout(db)
    r = client.post("/webhooks/paypal", json=payout_event("E1", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"))
    assert r.json() == {"status": "ok"}
    refresh(db)
    assert db.scalar(select(Payout)).status == "SETTLED"
    assert db.get(Worker, "worker-a").earned_cents == 400


@pytest.mark.parametrize("kind", ["FAILED", "DENIED", "RETURNED", "BLOCKED", "CANCELED"])
def test_payout_failed_restores_worker_balance(client, db, kind):
    pending_payout(db)
    client.post("/webhooks/paypal", json=payout_event("E1", f"PAYMENT.PAYOUTS-ITEM.{kind}"))
    refresh(db)
    assert db.scalar(select(Payout)).status == "FAILED"
    assert db.get(Worker, "worker-a").earned_cents == 1000


@pytest.mark.parametrize("kind", ["UNCLAIMED", "HELD"])
def test_non_final_payout_events_change_nothing(client, db, kind):
    pending_payout(db)
    client.post("/webhooks/paypal", json=payout_event("E1", f"PAYMENT.PAYOUTS-ITEM.{kind}"))
    refresh(db)
    assert db.scalar(select(Payout)).status == "PENDING"
    assert db.get(Worker, "worker-a").earned_cents == 400


def test_replay_ignored(client, db):
    pending_payout(db)
    first = client.post("/webhooks/paypal", json=payout_event("E1", "PAYMENT.PAYOUTS-ITEM.FAILED"))
    second = client.post("/webhooks/paypal", json=payout_event("E1", "PAYMENT.PAYOUTS-ITEM.FAILED"))
    assert first.json() == {"status": "ok"}
    assert second.json() == {"status": "duplicate"}
    refresh(db)
    assert db.get(Worker, "worker-a").earned_cents == 1000


def test_unknown_payout_ref_and_other_events_are_stored(client, db):
    assert client.post("/webhooks/paypal", json=payout_event("E1", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", ref="x")).status_code == 200
    assert client.post("/webhooks/paypal", json={"id": "E2", "event_type": "PAYMENT.CAPTURE.COMPLETED"}).status_code == 200
    assert db.get(WebhookEvent, "E1") is not None
    assert db.get(WebhookEvent, "E2").event_type == "PAYMENT.CAPTURE.COMPLETED"
