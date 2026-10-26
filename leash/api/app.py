"""The Leash gate API: mandates, /spend, holds, receipts, watchdog, console.

Every payment attempt flows through POST /spend -> deterministic verdict ->
signed receipt -> ledger. ALLOWs create a real PayPal order (payer approves at
PayPal); HOLDs open a human gate and (optionally) fire a Zapier webhook.
The watchdog scans the ledger stream for drift; disputes are drafted from
verified evidence by the LLM layer (or deterministic templates).
"""
import datetime as _dt
import hashlib
import json
import os
import threading
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..agent import rex as rex_agent
from ..engine import drift
from ..engine.policy import Decision, Mandate, PaymentRequest, PolicyEngine
from ..engine.receipts import ReceiptChain
from ..llm import explainer
from ..paypal.client import PayPalClient
from ..store.ledger import Store

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "web")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class MandateIn(BaseModel):
    agent: str
    payees: list[str] = Field(min_length=1)
    per_tx_limit: float = Field(gt=0)
    daily_limit: float = Field(gt=0)
    monthly_limit: float = Field(gt=0)
    purposes: list[str] = []
    expires_at: str | None = None
    currency: str = "USD"
    mandate_id: str | None = None


class SpendIn(BaseModel):
    agent: str
    payee: str
    amount: float = Field(gt=0)
    currency: str = "USD"
    purpose: str = "general"
    description: str = ""


class HoldResolution(BaseModel):
    approve: bool


def _zapier_alert(payload: dict) -> None:
    """Best-effort fire-and-forget; never blocks or breaks a payment."""
    url = config.ZAPIER_HOOK_URL
    if not url:
        return

    def _fire():
        try:
            httpx.post(url, json=payload, timeout=10)
        except httpx.HTTPError:
            pass

    threading.Thread(target=_fire, daemon=True).start()


def create_app(data_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="Leash", version="0.1.0",
                  description="The trust layer for agentic commerce")
    store = Store(data_dir or config.DATA_DIR)
    chain = ReceiptChain()
    paypal = PayPalClient()

    @app.get("/healthz")
    def health():
        return {"ok": True, "app": "leash", "paypal_mode": paypal.mode,
                "llm_provider": explainer.PROVIDER,
                "zapier": bool(config.ZAPIER_HOOK_URL)}

    # ---------------- mandates ----------------
    @app.post("/mandates", status_code=201)
    def create_mandate(body: MandateIn):
        m = Mandate(
            id=body.mandate_id or f"md-{uuid.uuid4().hex[:8]}",
            agent=body.agent, payees_allowlist=set(body.payees),
            per_tx_limit=body.per_tx_limit, daily_limit=body.daily_limit,
            monthly_limit=body.monthly_limit, purposes=set(body.purposes),
            expires_at=body.expires_at, currency=body.currency)
        store.save_mandate(m)
        return {"mandate_id": m.id, "agent": m.agent}

    @app.get("/mandates")
    def list_mandates():
        return [{"id": m.id, "agent": m.agent,
                 "payees": sorted(m.payees_allowlist),
                 "per_tx_limit": m.per_tx_limit, "daily_limit": m.daily_limit,
                 "monthly_limit": m.monthly_limit,
                 "purposes": sorted(m.purposes), "expires_at": m.expires_at}
                for m in store.load_mandates().values()]

    # ---------------- the gate ----------------
    def _do_spend(body: SpendIn) -> dict:
        req = PaymentRequest(agent=body.agent, payee=body.payee,
                             amount=body.amount, currency=body.currency,
                             purpose=body.purpose, ts=_now())
        verdict = PolicyEngine(store.load_mandates()).evaluate(
            req, store.history_for(body.agent))

        evidence_hash = hashlib.sha256(json.dumps(
            verdict.evidence, sort_keys=True).encode()).hexdigest()
        receipt = chain.issue(
            agent=body.agent, decision=verdict.decision.value,
            payee=body.payee, amount=body.amount,
            mandate_id=verdict.mandate_id, reasons=verdict.reasons,
            evidence_hash=evidence_hash, ts=req.ts)
        store.save_receipt(receipt)

        order = None
        if verdict.allowed:
            try:
                order = paypal.create_order(
                    amount=body.amount, currency=body.currency,
                    payee_email=body.payee,
                    description=body.description or f"Leash: {body.purpose}",
                    reference_id=receipt["payload"]["receipt_id"])
            except httpx.HTTPError as exc:
                order = {"error": f"paypal order failed: {exc}"}

        attempt_id = store.add_attempt(
            ts=req.ts, agent=body.agent, payee=body.payee,
            amount=body.amount, purpose=body.purpose,
            decision=verdict.decision, reasons=verdict.reasons,
            mandate_id=verdict.mandate_id,
            receipt_id=receipt["payload"]["receipt_id"],
            order_id=(order or {}).get("order_id", ""),
            evidence=verdict.evidence)

        hold = None
        if verdict.decision is Decision.HOLD_FOR_HUMAN:
            store.open_hold(receipt["payload"]["receipt_id"], attempt_id)
            hold = {"receipt_id": receipt["payload"]["receipt_id"],
                    "status": "pending"}
            _zapier_alert({"event": "leash.hold", "agent": body.agent,
                           "payee": body.payee, "amount": body.amount,
                           "reasons": verdict.reasons})

        return {"attempt_id": attempt_id,
                "decision": verdict.decision.value,
                "reasons": verdict.reasons,
                "evidence": verdict.evidence,
                "explanation": explainer.explain_verdict(
                    {"decision": verdict.decision.value,
                     "reasons": verdict.reasons,
                     "evidence": verdict.evidence}),
                "receipt": receipt,
                "paypal_order": order,
                "hold": hold}

    @app.post("/spend")
    def spend(body: SpendIn):
        return _do_spend(body)

    @app.get("/attempts")
    def attempts(limit: int = 200):
        rows = store.attempts(limit)
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
            r["evidence"] = json.loads(r["evidence"])
        return rows

    # ---------------- human gates ----------------
    @app.get("/holds")
    def holds():
        return store.open_holds()

    @app.post("/holds/{receipt_id}")
    def resolve(receipt_id: str, body: HoldResolution):
        attempt_id = store.resolve_hold(
            receipt_id, "approved" if body.approve else "denied")
        if attempt_id is None:
            raise HTTPException(404, "hold not found or already resolved")

        order = None
        if body.approve:
            row = next((a for a in store.attempts(500)
                        if a["id"] == attempt_id), None)
            if row:
                try:
                    order = paypal.create_order(
                        amount=row["amount"], currency="USD",
                        payee_email=row["payee"],
                        description=f"Leash human-approved {receipt_id}",
                        reference_id=receipt_id)
                except httpx.HTTPError as exc:
                    order = {"error": str(exc)}
            store.update_attempt(attempt_id, decision=Decision.ALLOW,
                                 order_id=(order or {}).get("order_id", ""))
            _zapier_alert({"event": "leash.hold.approved",
                           "receipt_id": receipt_id})
        else:
            store.update_attempt(attempt_id, decision=Decision.DENY)
            _zapier_alert({"event": "leash.hold.denied",
                           "receipt_id": receipt_id})

        return {"receipt_id": receipt_id,
                "result": "approved" if body.approve else "denied",
                "paypal_order": order}

    # ---------------- receipts ----------------
    @app.get("/receipts/{agent}")
    def receipts(agent: str):
        receipts = store.chain_for(agent)
        ok = chain.verify_chain(agent, receipts,
                                expected_head=chain.head(agent))
        return {"agent": agent, "count": len(receipts), "chain_valid": ok,
                "receipts": receipts}

    # ---------------- watchdog ----------------
    @app.post("/watchdog/scan")
    def watchdog_scan():
        found = []
        for agent in store.agents():
            for alert in drift.analyze(store.history_for(agent)):
                if not store.alert_exists(alert.summary):
                    store.save_alert(alert.as_dict())
                    _zapier_alert({"event": "leash.alert",
                                   "kind": alert.kind,
                                   "summary": alert.summary})
                    found.append(alert.as_dict())
        return {"new_alerts": found, "total_open": len(store.alerts("open"))}

    @app.get("/alerts")
    def alerts(status: str | None = None):
        return store.alerts(status)

    @app.post("/alerts/{alert_id}/dispute")
    def dispute(alert_id: str):
        match = next((a for a in store.alerts()
                      if a["id"] == alert_id), None)
        if match is None:
            raise HTTPException(404, "alert not found")
        drafted = explainer.draft_dispute(
            match, store.chain_for(match["agent"]))
        store.set_alert_status(alert_id, "dispute_drafted")
        _zapier_alert({"event": "leash.dispute.drafted",
                       "alert_id": alert_id})
        return {"alert_id": alert_id, "draft": drafted}

    # ---------------- demo (Rex) ----------------
    @app.post("/demo/rex")
    def demo_rex(agent: str | None = None):
        # A fresh agent id per invocation keeps every demo run clean without
        # ever deleting ledger history (the audit log is append-only).
        run_as = agent or f"rex-{uuid.uuid4().hex[:4]}"

        def _spend(body: dict) -> dict:
            return _do_spend(SpendIn(**body))

        def _mandate(body: dict) -> dict:
            return create_mandate(MandateIn(**body))

        report = rex_agent.run_rex(_spend, _mandate, agent=run_as)
        return {"agent": run_as, "report": report,
                "caught_all": all(r["caught"] for r in report)}

    @app.post("/demo/drift")
    def demo_drift():
        rex_agent.seed_drift_history(store)
        return watchdog_scan()

    # ---------------- console ----------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    @app.get("/{asset}", include_in_schema=False)
    def static_asset(asset: str):
        if asset not in ("app.js", "style.css"):
            raise HTTPException(404)
        ctype = ("text/javascript" if asset.endswith(".js")
                 else "text/css")
        path = os.path.join(WEB_DIR, asset)
        if not os.path.isfile(path):
            raise HTTPException(404)
        return FileResponse(path, media_type=ctype)

    return app
