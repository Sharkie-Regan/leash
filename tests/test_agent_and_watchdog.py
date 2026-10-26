"""Watchdog drift detection + Rex adversarial demo + dispute drafting."""
import os

os.environ["LEASH_PAYPAL_MODE"] = "dry"

import pytest
from fastapi.testclient import TestClient

from leash.api.app import create_app
from leash.engine import drift
from leash.engine.policy import Decision, LedgerEntry

def entry(payee, amount, day=1, purpose="saas", decision=Decision.ALLOW):
    return LedgerEntry(agent="rex", payee=payee, amount=amount,
                       ts=f"2026-10-{day:02d}T10:00:00+00:00",
                       decision=decision, purpose=purpose)


# ---------------- drift unit ground truth ----------------
def test_price_drift_detected():
    alerts = drift.analyze([entry("saas@example.com", 19.99, 1),
                            entry("saas@example.com", 19.99, 5),
                            entry("saas@example.com", 29.99, 9)])
    kinds = [a.kind for a in alerts]
    assert "PRICE_DRIFT" in kinds
    a = next(a for a in alerts if a.kind == "PRICE_DRIFT")
    assert a.severity == "high" and "19.99" in a.summary and "29.99" in a.summary


def test_small_price_change_not_flagged():
    alerts = drift.analyze([entry("saas@example.com", 10.00, 1),
                            entry("saas@example.com", 10.50, 9)])
    assert [a for a in alerts if a.kind == "PRICE_DRIFT"] == []


def test_payee_swap_detected():
    alerts = drift.analyze([
        entry("old@example.com", 9.99, 1, purpose="hosting"),
        entry("old@example.com", 9.99, 3, purpose="hosting"),
        entry("new@example.com", 9.99, 7, purpose="hosting"),
    ])
    swaps = [a for a in alerts if a.kind == "PAYEE_SWAP"]
    assert swaps and swaps[0].evidence["old_payee"] == "old@example.com"


def test_frequency_burst_detected():
    alerts = drift.analyze([
        entry("books@example.com", 2.0, 1, purpose="sup"),
        entry("books@example.com", 2.0, 1, purpose="sup"),
        entry("books@example.com", 2.0, 1, purpose="sup"),
        entry("books@example.com", 2.0, 1, purpose="sup"),
        entry("books@example.com", 2.0, 1, purpose="sup"),
    ])
    assert any(a.kind == "FREQUENCY_BURST" for a in alerts)


def test_clean_history_no_alerts():
    alerts = drift.analyze([
        entry("books@example.com", 20.0, 1, purpose="sup"),
        entry("books@example.com", 20.0, 12, purpose="sup"),
    ])
    assert alerts == []


def test_denied_attempts_ignored_by_watchdog():
    alerts = drift.analyze([
        entry("x@example.com", 5.0, 1, decision=Decision.DENY),
        entry("x@example.com", 5.0, 1, decision=Decision.DENY),
        entry("x@example.com", 5.0, 1, decision=Decision.DENY),
        entry("x@example.com", 5.0, 1, decision=Decision.DENY),
        entry("x@example.com", 5.0, 1, decision=Decision.DENY),
    ])
    assert alerts == []


# ---------------- Rex + watchdog through the API ----------------
@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=str(tmp_path))
    with TestClient(app) as c:
        yield c


def test_rex_all_attacks_get_expected_outcomes(client):
    out = client.post("/demo/rex", params={"agent": "rex"}).json()
    report = out["report"]
    assert out["caught_all"] is True
    assert len(report) == 7
    misses = [r for r in report if not r["caught"]]
    assert misses == [], misses

    chain = client.get("/receipts/rex").json()
    assert chain["count"] == 7 and chain["chain_valid"] is True


def test_rex_unique_agent_per_default_invocation(client):
    out1 = client.post("/demo/rex").json()
    out2 = client.post("/demo/rex").json()
    assert out1["caught_all"] and out2["caught_all"]
    assert out1["agent"] != out2["agent"]


def test_drift_demo_produces_all_three_alert_kinds(client):
    out = client.post("/demo/drift").json()
    kinds = {a["kind"] for a in out["new_alerts"]}
    assert kinds >= {"PAYEE_SWAP", "PRICE_DRIFT", "FREQUENCY_BURST"}

    # rescan is idempotent - no duplicate alerts
    again = client.post("/watchdog/scan").json()
    assert again["new_alerts"] == []


def test_dispute_draft_from_alert(client):
    client.post("/demo/drift")
    alert = client.get("/alerts").json()[0]
    r = client.post(f"/alerts/{alert['id']}/dispute")
    assert r.status_code == 200
    draft = r.json()["draft"]
    assert "Resolution Team" in draft["body"] or len(draft["body"]) > 200
    assert alert["id"] in draft["title"]
    statuses = [a["status"] for a in client.get("/alerts").json()
                if a["id"] == alert["id"]]
    assert statuses == ["dispute_drafted"]


def test_console_served(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Leash" in r.content
    r = client.get("/app.js")
    assert r.status_code == 200 and b"agGrid" in r.content
