"""Prepaid wallet ledger. Money is integer cents. Every operation is idempotent.

Each function takes a Session and commits its own transaction.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base

HELD, RELEASED, SETTLED = "HELD", "RELEASED", "SETTLED"


class InsufficientFunds(Exception):
    pass


class InvalidHoldState(Exception):
    pass


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    available_cents: Mapped[int] = mapped_column(Integer, default=0)
    locked_cents: Mapped[int] = mapped_column(Integer, default=0)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    earned_cents: Mapped[int] = mapped_column(Integer, default=0)


class Hold(Base):
    __tablename__ = "holds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, unique=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    worker_id: Mapped[str] = mapped_column(String)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default=HELD)


class Entry(Base):
    """Append-only. Never update or delete rows."""

    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)  # TOPUP, HOLD, RELEASE, SETTLE, FEE
    wallet_id: Mapped[str | None] = mapped_column(String, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


def _check_cents(amount_cents) -> None:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")


def _seen(session: Session, ref: str) -> bool:
    return session.scalar(select(Entry.id).where(Entry.ref == ref)) is not None


def _wallet(session: Session, wallet_id: str) -> Wallet:
    w = session.get(Wallet, wallet_id, with_for_update=True)
    if w is None:
        w = Wallet(id=wallet_id, available_cents=0, locked_cents=0)
        session.add(w)
    return w


def _hold(session: Session, task_id: str) -> Hold:
    h = session.scalar(select(Hold).where(Hold.task_id == task_id).with_for_update())
    if h is None:
        raise InvalidHoldState(f"no hold for task {task_id}")
    return h


def topup(session: Session, wallet_id: str, amount_cents: int, ref: str) -> Wallet:
    """Credit a wallet. ref = the payment capture id, so a replay counts once."""
    _check_cents(amount_cents)
    w = _wallet(session, wallet_id)
    if not _seen(session, ref):
        w.available_cents += amount_cents
        session.add(Entry(ref=ref, kind="TOPUP", wallet_id=wallet_id, amount_cents=amount_cents))
        session.commit()
    return w


def hold(session: Session, wallet_id: str, task_id: str, worker_id: str, amount_cents: int) -> Hold:
    """Move the price from available to locked. Raises InsufficientFunds."""
    _check_cents(amount_cents)
    existing = session.scalar(select(Hold).where(Hold.task_id == task_id))
    if existing is not None:
        return existing
    w = _wallet(session, wallet_id)
    if w.available_cents < amount_cents:
        session.rollback()
        raise InsufficientFunds(f"wallet {wallet_id} has {w.available_cents}, needs {amount_cents}")
    w.available_cents -= amount_cents
    w.locked_cents += amount_cents
    h = Hold(task_id=task_id, wallet_id=wallet_id, worker_id=worker_id, amount_cents=amount_cents, status=HELD)
    session.add(h)
    session.add(Entry(ref=f"hold:{task_id}", kind="HOLD", wallet_id=wallet_id, task_id=task_id, amount_cents=amount_cents))
    session.commit()
    return h


def release(session: Session, task_id: str) -> Hold:
    """Rejected output: give the held amount back to the buyer."""
    h = _hold(session, task_id)
    if h.status == RELEASED:
        return h
    if h.status != HELD:
        raise InvalidHoldState(f"task {task_id} is {h.status}")
    w = _wallet(session, h.wallet_id)
    w.locked_cents -= h.amount_cents
    w.available_cents += h.amount_cents
    h.status = RELEASED
    session.add(Entry(ref=f"release:{task_id}", kind="RELEASE", wallet_id=h.wallet_id, task_id=task_id, amount_cents=h.amount_cents))
    session.commit()
    return h


def settle(session: Session, task_id: str, fee_percent: int = 10) -> Hold:
    """Validated output: pay the worker the price minus the platform fee (fee rounds down)."""
    h = _hold(session, task_id)
    if h.status == SETTLED:
        return h
    if h.status != HELD:
        raise InvalidHoldState(f"task {task_id} is {h.status}")
    fee = h.amount_cents * fee_percent // 100
    payout = h.amount_cents - fee
    w = _wallet(session, h.wallet_id)
    w.locked_cents -= h.amount_cents
    worker = session.get(Worker, h.worker_id, with_for_update=True)
    if worker is None:
        worker = Worker(id=h.worker_id, earned_cents=0)
        session.add(worker)
    worker.earned_cents += payout
    h.status = SETTLED
    session.add(Entry(ref=f"settle:{task_id}", kind="SETTLE", wallet_id=h.wallet_id, worker_id=h.worker_id, task_id=task_id, amount_cents=payout))
    if fee:
        session.add(Entry(ref=f"fee:{task_id}", kind="FEE", wallet_id=h.wallet_id, task_id=task_id, amount_cents=fee))
    session.commit()
    return h
