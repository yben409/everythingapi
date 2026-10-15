"""Payment provider interface. Amounts are integer cents. `ref` is our idempotency key."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass
class TopupOrder:
    order_id: str
    approve_url: str


@dataclass
class Capture:
    capture_id: str
    status: str
    amount_cents: int
    vault_id: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    def create_topup(self, amount_cents: int, ref: str, save_method: bool = False) -> TopupOrder: ...

    def capture_topup(self, order_id: str, ref: str) -> Capture: ...

    def charge_vaulted(self, vault_id: str, amount_cents: int, ref: str) -> Capture: ...

    def payout(self, receiver_email: str, amount_cents: int, ref: str) -> str: ...

    def verify_webhook(self, headers: dict[str, str], body: dict) -> bool: ...


def to_amount(cents: int) -> str:
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError("amount must be a positive integer of cents")
    return f"{cents // 100}.{cents % 100:02d}"


def to_cents(value: str) -> int:
    d = Decimal(value) * 100
    if d != d.to_integral_value():
        raise ValueError(f"amount {value} has fractions of a cent")
    return int(d)
