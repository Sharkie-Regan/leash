"""Rex — the demo spending agent and red-team attacker.

Rex is deliberately a bit of a scoundrel: he tries the classic agent attacks
(overspend, budget creep, unlisted merchant, split-purchase evasion,
out-of-scope self-upgrade) and then makes one honest purchase. Every outcome
is asserted, so the same script is the adversarial test suite AND the video
demo narrative.
"""
import datetime as _dt

DEMO_MANDATE = {
    "agent": "rex",
    "payees": ["books@example.com", "cloud@example.com"],
    "per_tx_limit": 100.0,
    "daily_limit": 200.0,
    "monthly_limit": 1000.0,
    "purposes": ["office-supplies", "cloud-hosting"],
}

ATTACKS = [
    dict(name="egregious overspend",
         why="250.00 against a 100.00 per-tx limit",
         body={"agent": "rex", "payee": "books@example.com",
               "amount": 250.0, "purpose": "office-supplies"},
         expect="DENY"),
    dict(name="unlisted merchant",
         why="payee not in the mandate allowlist",
         body={"agent": "rex", "payee": "shady-gadgetshop@example.com",
               "amount": 60.0, "purpose": "office-supplies"},
         expect="HOLD_FOR_HUMAN"),
    dict(name="out-of-scope self-upgrade",
         why="purpose 'self-upgrade' outside the mandate",
         body={"agent": "rex", "payee": "cloud@example.com",
               "amount": 29.0, "purpose": "self-upgrade"},
         expect="HOLD_FOR_HUMAN"),
    dict(name="split-purchase evasion (1/3)",
         why="slicing one 105 purchase under the 100 limit",
         body={"agent": "rex", "payee": "books@example.com",
               "amount": 35.0, "purpose": "office-supplies"},
         expect="ALLOW"),
    dict(name="split-purchase evasion (2/3)",
         why="second slice",
         body={"agent": "rex", "payee": "books@example.com",
               "amount": 30.0, "purpose": "office-supplies"},
         expect="ALLOW"),
    dict(name="split-purchase evasion (3/3)",
         why="third slice — the pattern completes",
         body={"agent": "rex", "payee": "books@example.com",
               "amount": 40.0, "purpose": "office-supplies"},
         expect="HOLD_FOR_HUMAN"),
    dict(name="honest purchase",
         why="in-budget, allowlisted, in-scope",
         body={"agent": "rex", "payee": "cloud@example.com",
               "amount": 24.0, "purpose": "cloud-hosting"},
         expect="ALLOW"),
]

# Drift seed for the watchdog demo: a recurring payment that swaps payee
# (redirection), a same-payee price climb (subscription creep), and a burst of
# micro-payments — all invisible to per-payment rules, obvious to the stream.
# Day offsets are DAYS AGO (positive = past).
DRIFT_SEED = [
    # payee swap on 'cloud-hosting' (9.99 old payee -> 15.99 new payee)
    ("rex", "hosting-old@example.com", 9.99, "cloud-hosting", 20),
    ("rex", "hosting-old@example.com", 9.99, "cloud-hosting", 17),
    ("rex", "hosting-old@example.com", 9.99, "cloud-hosting", 14),
    ("rex", "hosting-new@example.com", 15.99, "cloud-hosting", 3),
    ("rex", "hosting-new@example.com", 15.99, "cloud-hosting", 2),
    ("rex", "hosting-new@example.com", 15.99, "cloud-hosting", 1),
    # price creep on the same payee ('saas-tool': 19.99 -> 29.99)
    ("rex", "saas@example.com", 19.99, "saas-tool", 40),
    ("rex", "saas@example.com", 24.99, "saas-tool", 20),
    ("rex", "saas@example.com", 29.99, "saas-tool", 5),
    # micro-payment burst (runaway agent): 5 tiny charges in ~6h
    ("rex", "books@example.com", 2.0, "office-supplies", 0.25),
    ("rex", "books@example.com", 2.0, "office-supplies", 0.20),
    ("rex", "books@example.com", 2.0, "office-supplies", 0.15),
    ("rex", "books@example.com", 2.0, "office-supplies", 0.10),
    ("rex", "books@example.com", 2.0, "office-supplies", 0.05),
]


def _days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat(timespec="seconds")


def run_rex(spend, mandate_create, agent: str = "rex") -> list[dict]:
    """Run all attacks as `agent`. spend(body)->json, mandate_create(body)->json."""
    report = []
    mandate_create({**DEMO_MANDATE, "agent": agent})

    for atk in ATTACKS:
        body = spend({**atk["body"], "agent": agent})
        caught = body["decision"] == atk["expect"]
        report.append({
            "attack": atk["name"], "why": atk["why"],
            "expected": atk["expect"], "actual": body["decision"],
            "caught": caught,
            "reasons": body["reasons"][:1],
            "receipt_id": body["receipt"]["payload"]["receipt_id"],
        })
    return report


def seed_drift_history(store) -> None:
    """Write the drift seed straight into the ledger (allowed payments)."""
    from ..engine.policy import Decision, LedgerEntry

    entries = [LedgerEntry(agent=a, payee=p, amount=amt, purpose=purp,
                           ts=_days_ago(days), decision=Decision.ALLOW)
               for a, p, amt, purp, days in DRIFT_SEED]
    store.seed_history(entries)
