"""Smoke test the PayPal sandbox credentials: token -> order -> capture."""
import base64
import json
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tiny .env loader (no python-dotenv dependency)
env = {}
with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
    for line in fh:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

CID = env["LEASH_PAYPAL_CLIENT_ID"]
SECRET = env["LEASH_PAYPAL_SECRET"]
BASE = "https://api-m.sandbox.paypal.com"

auth = base64.b64encode(f"{CID}:{SECRET}".encode()).decode()
with httpx.Client(timeout=30) as c:
    r = c.post(f"{BASE}/v1/oauth2/token",
               headers={"Authorization": f"Basic {auth}"},
               data={"grant_type": "client_credentials"})
    print("token:", r.status_code)
    r.raise_for_status()
    token = r.json()["access_token"]
    scopes = r.json().get("scope", "").split()
    print("scopes granted:", len(scopes))
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    order = c.post(f"{BASE}/v2/checkout/orders", headers=hdr, json={
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {"currency_code": "USD", "value": "42.00"},
            "payee": {"email_address": "sb-merchant@business.example.com"},
            "description": "Leash smoke test - books",
        }],
    })
    print("create order:", order.status_code)
    order.raise_for_status()
    oid = order.json()["id"]
    print("order id:", oid, "status:", order.json()["status"])

    cap = c.post(f"{BASE}/v2/checkout/orders/{oid}/capture", headers=hdr)
    print("capture (pre-approval, expected 422):", cap.status_code)
    links = {l["rel"]: l["href"] for l in order.json().get("links", [])}
    print("payer approve link:", links.get("payer-action", "n/a"))

    # transactions reporting API availability (used by the watchdog)
    tx = c.get(f"{BASE}/v1/reporting/transactions",
               params={"start_date": "2026-10-20T00:00:00Z",
                       "end_date": "2026-10-27T00:00:00Z"}, headers=hdr)
    print("reporting/transactions:", tx.status_code,
          "(200 = watchdog can sync from PayPal; else we sync our own ledger)")

print("\nPAYPAL SANDBOX CREDS OK")
