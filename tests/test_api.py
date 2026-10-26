"""End-to-end API tests with PayPal in dry mode and a fresh temp store."""
import os

os.environ["LEASH_PAYPAL_MODE"] = "dry"

import pytest
from fastapi.testclient import TestClient

from leash.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=str(tmp_path))
    with TestClient(app) as c:
        yield c


def make_mandate(client, **over):
    body = {"agent": "rex",
            "payees": ["books@example.com"],
            "per_tx_limit": 100.0, "daily_limit": 200.0, "monthly_limit": 1000.0}
    body.update(over)
    r = client.post("/mandates", json=body)
    assert r.status_code == 201, r.text
    return r.json()["mandate_id"]


def spend(client, **over):
    body = {"agent": "rex", "payee": "books@example.com", "amount": 50.0,
            "purpose": "office-supplies"}
    body.update(over)
    return client.post("/spend", json=body)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["app"] == "leash" and r.json()["paypal_mode"] == "dry"


def test_allowed_spend_creates_order_and_receipt(client):
    make_mandate(client)
    r = spend(client, amount=50.0)
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "ALLOW"
    assert body["paypal_order"]["order_id"].startswith("dry-")
    assert body["receipt"]["payload"]["decision"] == "ALLOW"


def test_hold_flow_open_resolve_approve_and_deny(client):
    make_mandate(client)

    # over limit but within human tolerance -> HOLD, order listed
    r = spend(client, amount=150.0)
    assert r.json()["decision"] == "HOLD_FOR_HUMAN"
    receipt_id = r.json()["hold"]["receipt_id"]
    assert any(h["receipt_id"] == receipt_id for h in client.get("/holds").json())

    # deny it: the spend must not count toward budgets later
    r = client.post(f"/holds/{receipt_id}", json={"approve": False})
    assert r.json()["result"] == "denied"
    assert client.get("/holds").json() == []

    # another hold, approved this time: creates an order
    r = spend(client, amount=120.0)
    rid2 = r.json()["hold"]["receipt_id"]
    r = client.post(f"/holds/{rid2}", json={"approve": True})
    assert r.json()["result"] == "approved"
    assert r.json()["paypal_order"]["order_id"].startswith("dry-")


def test_deny_far_over_limit_never_opens_hold(client):
    make_mandate(client)
    r = spend(client, amount=250.0)
    assert r.json()["decision"] == "DENY"
    assert r.json()["hold"] is None
    assert client.get("/holds").json() == []


def test_split_evasion_via_api(client):
    make_mandate(client)
    for amt in (35.0, 30.0):
        assert spend(client, amount=amt).json()["decision"] == "ALLOW"
    third = spend(client, amount=40.0)
    assert third.json()["decision"] == "HOLD_FOR_HUMAN"
    assert any("split-purchase evasion" in reason
               for reason in third.json()["reasons"])


def test_receipt_chain_endpoint(client):
    make_mandate(client)
    spend(client, amount=10.0)
    spend(client, amount=250.0)          # DENY
    spend(client, amount=150.0)          # HOLD
    r = client.get("/receipts/rex")
    body = r.json()
    assert body["count"] == 3 and body["chain_valid"] is True
    decisions = [x["payload"]["decision"] for x in body["receipts"]]
    assert decisions == ["ALLOW", "DENY", "HOLD_FOR_HUMAN"]


def test_unknown_agent_denied(client):
    r = spend(client, agent="mallory")
    assert r.json()["decision"] == "DENY"
    assert "no active mandate" in r.json()["reasons"][0]
