import json

import httpx
import pytest

from app.payments import PaymentProvider, to_amount, to_cents
from app.paypal import BASE, PayPalSandbox

WEBHOOK_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
    "PAYPAL-TRANSMISSION-ID": "t1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-15T00:00:00Z",
}


def captured(capture_id="CAP-1", value="20.00", vault_id=None):
    body = {
        "id": "ORDER-1",
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": capture_id, "status": "COMPLETED", "amount": {"value": value}}]}}],
    }
    if vault_id:
        body["payment_source"] = {"paypal": {"attributes": {"vault": {"id": vault_id}}}}
    return body


class FakePayPal:
    """Records requests and answers from a route table. No network."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "TOKEN", "expires_in": 3600})
        return httpx.Response(200, json=self.routes[request.url.path])

    def calls(self, path):
        return [r for r in self.requests if r.url.path == path]


def provider(routes, webhook_id="WH-1"):
    fake = FakePayPal(routes)
    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(fake))
    return PayPalSandbox("id", "secret", webhook_id=webhook_id, http=http), fake


def test_satisfies_interface():
    p, _ = provider({})
    assert isinstance(p, PaymentProvider)


def test_money_conversion():
    assert to_amount(2000) == "20.00"
    assert to_amount(5) == "0.05"
    assert to_cents("20.00") == 2000
    assert to_cents("0.05") == 5
    with pytest.raises(ValueError):
        to_amount(0)
    with pytest.raises(ValueError):
        to_amount(1.5)
    with pytest.raises(ValueError):
        to_cents("1.005")


def test_create_topup_uses_ref_and_saves_method():
    p, fake = provider({"/v2/checkout/orders": {"id": "ORDER-1", "links": [{"rel": "payer-action", "href": "https://approve"}]}})
    order = p.create_topup(2000, ref="topup-1", save_method=True)
    assert (order.order_id, order.approve_url) == ("ORDER-1", "https://approve")
    req = fake.calls("/v2/checkout/orders")[0]
    assert req.headers["PayPal-Request-Id"] == "topup-1"
    assert req.headers["Authorization"] == "Bearer TOKEN"
    body = json.loads(req.content)
    assert body["purchase_units"][0]["amount"]["value"] == "20.00"
    assert body["payment_source"]["paypal"]["attributes"]["vault"]["store_in_vault"] == "ON_SUCCESS"


def test_token_is_cached():
    p, fake = provider({"/v2/checkout/orders": {"id": "O", "links": [{"rel": "approve", "href": "u"}]}})
    p.create_topup(100, ref="a")
    p.create_topup(100, ref="b")
    assert len(fake.calls("/v1/oauth2/token")) == 1


def test_capture_topup_returns_cents_and_vault_id():
    p, _ = provider({"/v2/checkout/orders/ORDER-1/capture": captured(vault_id="VAULT-1")})
    cap = p.capture_topup("ORDER-1", ref="cap-1")
    assert (cap.capture_id, cap.amount_cents, cap.vault_id) == ("CAP-1", 2000, "VAULT-1")


def test_charge_vaulted():
    p, fake = provider({"/v2/checkout/orders": captured(value="5.00")})
    cap = p.charge_vaulted("VAULT-1", 500, ref="auto-1")
    assert cap.amount_cents == 500
    body = json.loads(fake.calls("/v2/checkout/orders")[0].content)
    assert body["payment_source"] == {"paypal": {"vault_id": "VAULT-1"}}


def test_payout():
    p, fake = provider({"/v1/payments/payouts": {"batch_header": {"payout_batch_id": "BATCH-1"}}})
    assert p.payout("worker@example.com", 108, ref="payout-1") == "BATCH-1"
    body = json.loads(fake.calls("/v1/payments/payouts")[0].content)
    assert body["sender_batch_header"]["sender_batch_id"] == "payout-1"
    assert body["items"][0]["amount"]["value"] == "1.08"


@pytest.mark.parametrize("status,expected", [("SUCCESS", True), ("FAILURE", False)])
def test_verify_webhook(status, expected):
    p, fake = provider({"/v1/notifications/verify-webhook-signature": {"verification_status": status}})
    assert p.verify_webhook(WEBHOOK_HEADERS, {"id": "WH-EVENT-1"}) is expected
    body = json.loads(fake.calls("/v1/notifications/verify-webhook-signature")[0].content)
    assert body["webhook_id"] == "WH-1"
    assert body["webhook_event"] == {"id": "WH-EVENT-1"}


def test_verify_webhook_rejects_missing_headers_without_calling_paypal():
    p, fake = provider({})
    assert p.verify_webhook({}, {"id": "x"}) is False
    assert fake.requests == []
