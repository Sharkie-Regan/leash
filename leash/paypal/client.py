"""Thin PayPal Orders v2 client (sandbox or live) with a dry mode for tests.

Leash only ever CREATES orders the policy engine ALLOWed; the payer approves at
PayPal (the human's one click — by design, not a limitation), then Leash can
capture on approval notification or on-demand.
"""
import base64
import threading
import time

import httpx

from .. import config

BASES = {"sandbox": "https://api-m.sandbox.paypal.com",
         "live": "https://api-m.paypal.com"}


class DryMode(Exception):
    """Raised when a live call is attempted while PAYPAL_MODE=dry."""


class PayPalClient:
    def __init__(self, mode: str | None = None):
        self.mode = mode or config.PAYPAL_MODE
        self.base = BASES[config.PAYPAL_ENV]
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._lock = threading.Lock()

    # -- auth ---------------------------------------------------------------
    def _auth_header(self) -> str:
        raw = f"{config.PAYPAL_CLIENT_ID}:{config.PAYPAL_SECRET}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            r = httpx.post(f"{self.base}/v1/oauth2/token",
                           headers={"Authorization": self._auth_header()},
                           data={"grant_type": "client_credentials"},
                           timeout=30)
            r.raise_for_status()
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 3600))
            return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json"}

    # -- orders ---------------------------------------------------------------
    def create_order(self, amount: float, currency: str, payee_email: str,
                     description: str, reference_id: str = "") -> dict:
        """Returns {order_id, status, approve_url}."""
        if self.mode == "dry":
            return {"order_id": f"dry-{int(time.time()*1000)}",
                    "status": "CREATED",
                    "approve_url": "https://sandbox.paypal.com/dry-approve"}
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                "payee": {"email_address": payee_email},
                "description": description[:127],
            }],
            "payment_source": {"paypal": {
                "experience_context": {
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "brand_name": "Leash",
                }}},
        }
        if reference_id:
            body["purchase_units"][0]["reference_id"] = reference_id[:127]
        r = httpx.post(f"{self.base}/v2/checkout/orders",
                       headers=self._headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        approve = next((l["href"] for l in data.get("links", [])
                        if l["rel"] in ("payer-action", "approve")), "")
        return {"order_id": data["id"], "status": data["status"],
                "approve_url": approve}

    def capture(self, order_id: str) -> dict:
        """Capture an APPROVED order. 422 ORDER_NOT_APPROVED means the payer
        has not clicked the approve link yet — surfaced, not hidden."""
        if self.mode == "dry":
            return {"order_id": order_id, "status": "COMPLETED", "dry": True}
        r = httpx.post(f"{self.base}/v2/checkout/orders/{order_id}/capture",
                       headers=self._headers(), timeout=30)
        if r.status_code == 422:
            return {"order_id": order_id, "status": "NOT_APPROVED",
                    "detail": "payer has not approved yet"}
        r.raise_for_status()
        data = r.json()
        return {"order_id": order_id, "status": data.get("status", "UNKNOWN")}
