import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api-m.sandbox.paypal.com"


def token() -> str:
    r = httpx.post(
        f"{BASE}/v1/oauth2/token",
        auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def create_order(amount: str = "20.00", currency: str = "USD") -> dict:
    r = httpx.post(
        f"{BASE}/v2/checkout/orders",
        headers={"Authorization": f"Bearer {token()}", "PayPal-Request-Id": str(uuid.uuid4())},
        json={
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": currency, "value": amount}}],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": "https://example.com/return",
                        "cancel_url": "https://example.com/cancel",
                        "user_action": "PAY_NOW",
                    }
                }
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def capture_order(order_id: str) -> dict:
    r = httpx.post(
        f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers={
            "Authorization": f"Bearer {token()}",
            "PayPal-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()
