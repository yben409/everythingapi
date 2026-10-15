"""PayPal SANDBOX adapter for PaymentProvider (REST API)."""

import os
import time

import httpx
from dotenv import load_dotenv

from app.payments import Capture, TopupOrder, to_amount, to_cents

load_dotenv()

BASE = "https://api-m.sandbox.paypal.com"
RETURN_URL = "https://example.com/return"
CANCEL_URL = "https://example.com/cancel"


class PayPalSandbox:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        webhook_id: str | None = None,
        currency: str = "USD",
        http: httpx.Client | None = None,
    ):
        self.client_id = client_id or os.environ["PAYPAL_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["PAYPAL_CLIENT_SECRET"]
        self.webhook_id = webhook_id or os.environ.get("PAYPAL_WEBHOOK_ID")
        self.currency = currency
        self.http = http or httpx.Client(base_url=BASE, timeout=30)
        self._token: str | None = None
        self._token_expires = 0.0

    def _auth(self) -> dict[str, str]:
        if self._token is None or time.time() > self._token_expires:
            r = self.http.post(
                "/v1/oauth2/token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + body.get("expires_in", 0) - 60
        return {"Authorization": f"Bearer {self._token}"}

    def _post(self, path: str, json: dict | None, ref: str | None = None) -> dict:
        headers = self._auth() | {"Content-Type": "application/json"}
        if ref:
            headers["PayPal-Request-Id"] = ref
        r = self.http.post(path, headers=headers, json=json)
        r.raise_for_status()
        return r.json()

    def _unit(self, amount_cents: int) -> list[dict]:
        return [{"amount": {"currency_code": self.currency, "value": to_amount(amount_cents)}}]

    @staticmethod
    def _capture(result: dict) -> Capture:
        cap = result["purchase_units"][0]["payments"]["captures"][0]
        vault = result.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {})
        return Capture(
            capture_id=cap["id"],
            status=cap["status"],
            amount_cents=to_cents(cap["amount"]["value"]),
            vault_id=vault.get("id"),
        )

    def create_topup(self, amount_cents: int, ref: str, save_method: bool = False) -> TopupOrder:
        paypal: dict = {
            "experience_context": {"return_url": RETURN_URL, "cancel_url": CANCEL_URL, "user_action": "PAY_NOW"}
        }
        if save_method:
            paypal["attributes"] = {
                "vault": {"store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT", "customer_type": "CONSUMER"}
            }
        order = self._post(
            "/v2/checkout/orders",
            {"intent": "CAPTURE", "purchase_units": self._unit(amount_cents), "payment_source": {"paypal": paypal}},
            ref,
        )
        link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
        return TopupOrder(order_id=order["id"], approve_url=link)

    def capture_topup(self, order_id: str, ref: str) -> Capture:
        return self._capture(self._post(f"/v2/checkout/orders/{order_id}/capture", None, ref))

    def charge_vaulted(self, vault_id: str, amount_cents: int, ref: str) -> Capture:
        """Charge a saved PayPal method with no buyer present."""
        result = self._post(
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": self._unit(amount_cents),
                "payment_source": {"paypal": {"vault_id": vault_id}},
            },
            ref,
        )
        if result["status"] != "COMPLETED":
            result = self._post(f"/v2/checkout/orders/{result['id']}/capture", None, f"{ref}:capture")
        return self._capture(result)

    def payout(self, receiver_email: str, amount_cents: int, ref: str) -> str:
        """Send one payout. Returns the payout batch id. Final status arrives via webhooks."""
        result = self._post(
            "/v1/payments/payouts",
            {
                "sender_batch_header": {"sender_batch_id": ref, "email_subject": "AgentPay payout"},
                "items": [
                    {
                        "recipient_type": "EMAIL",
                        "receiver": receiver_email,
                        "amount": {"currency": self.currency, "value": to_amount(amount_cents)},
                        "sender_item_id": ref,
                    }
                ],
            },
            ref,
        )
        return result["batch_header"]["payout_batch_id"]

    def verify_webhook(self, headers: dict[str, str], body: dict) -> bool:
        """Ask PayPal to verify the signature. False on any missing header or config."""
        h = {k.lower(): v for k, v in headers.items()}
        needed = ["paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
                  "paypal-transmission-sig", "paypal-transmission-time"]
        if not self.webhook_id or any(k not in h for k in needed):
            return False
        result = self._post(
            "/v1/notifications/verify-webhook-signature",
            {
                "auth_algo": h["paypal-auth-algo"],
                "cert_url": h["paypal-cert-url"],
                "transmission_id": h["paypal-transmission-id"],
                "transmission_sig": h["paypal-transmission-sig"],
                "transmission_time": h["paypal-transmission-time"],
                "webhook_id": self.webhook_id,
                "webhook_event": body,
            },
        )
        return result.get("verification_status") == "SUCCESS"
