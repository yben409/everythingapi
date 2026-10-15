import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import ledger
from app.db import init_db
from app.ledger import Entry, InsufficientFunds, InvalidHoldState, Wallet, Worker


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        yield s


def entries(session):
    return session.scalar(select(func.count()).select_from(Entry))


def test_topup_same_ref_counts_once(session):
    ledger.topup(session, "w1", 1000, ref="CAPTURE-1")
    ledger.topup(session, "w1", 1000, ref="CAPTURE-1")
    assert session.get(Wallet, "w1").available_cents == 1000
    assert entries(session) == 1


def test_hold_then_settle(session):
    ledger.topup(session, "w1", 1000, ref="CAPTURE-1")
    ledger.hold(session, "w1", "task-1", "worker-a", 120)
    w = session.get(Wallet, "w1")
    assert (w.available_cents, w.locked_cents) == (880, 120)

    ledger.settle(session, "task-1")
    ledger.settle(session, "task-1")  # idempotent
    w = session.get(Wallet, "w1")
    assert (w.available_cents, w.locked_cents) == (880, 0)
    assert session.get(Worker, "worker-a").earned_cents == 108  # 120 - 10% fee
    fee = session.scalar(select(Entry).where(Entry.kind == "FEE"))
    assert fee.amount_cents == 12


def test_hold_then_release_twice(session):
    ledger.topup(session, "w1", 500, ref="CAPTURE-1")
    ledger.hold(session, "w1", "task-1", "worker-a", 200)
    ledger.release(session, "task-1")
    ledger.release(session, "task-1")
    w = session.get(Wallet, "w1")
    assert (w.available_cents, w.locked_cents) == (500, 0)
    assert session.get(Worker, "worker-a") is None


def test_insufficient_funds(session):
    ledger.topup(session, "w1", 100, ref="CAPTURE-1")
    with pytest.raises(InsufficientFunds):
        ledger.hold(session, "w1", "task-1", "worker-a", 101)
    w = session.get(Wallet, "w1")
    assert (w.available_cents, w.locked_cents) == (100, 0)


def test_same_task_held_once(session):
    ledger.topup(session, "w1", 500, ref="CAPTURE-1")
    ledger.hold(session, "w1", "task-1", "worker-a", 200)
    ledger.hold(session, "w1", "task-1", "worker-a", 200)
    assert session.get(Wallet, "w1").locked_cents == 200


def test_cannot_settle_after_release(session):
    ledger.topup(session, "w1", 500, ref="CAPTURE-1")
    ledger.hold(session, "w1", "task-1", "worker-a", 200)
    ledger.release(session, "task-1")
    with pytest.raises(InvalidHoldState):
        ledger.settle(session, "task-1")


@pytest.mark.parametrize("bad", [0, -5, 1.5, "100", True])
def test_amount_must_be_positive_int_cents(session, bad):
    with pytest.raises(ValueError):
        ledger.topup(session, "w1", bad, ref="CAPTURE-1")
