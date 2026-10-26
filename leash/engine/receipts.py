"""Tamper-evident receipt chains.

Every Leash decision is receipted. Receipts are HMAC-signed and chained per
agent (each references the previous receipt's hash), so any after-the-fact
edit breaks the chain visibly — the audit trail auditors can actually trust.
"""
import datetime as _dt
import hashlib
import hmac
import json
import uuid

from .. import config


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


class ReceiptChain:
    def __init__(self, secret: str | None = None):
        self.secret = (secret or config.RECEIPT_SECRET).encode("utf-8")
        self._heads: dict[str, str] = {}       # agent -> last receipt hash
        self._receipts: dict[str, dict] = {}   # receipt_id -> record

    def _sign(self, payload: dict) -> str:
        return hmac.new(self.secret, _canonical(payload),
                        hashlib.sha256).hexdigest()

    def issue(self, agent: str, decision: str, payee: str, amount: float,
              mandate_id: str | None, reasons: list[str],
              evidence_hash: str, ts: str | None = None) -> dict:
        payload = {
            "receipt_id": f"rc-{uuid.uuid4().hex[:10]}",
            "agent": agent,
            "decision": decision,
            "payee": payee,
            "amount": round(float(amount), 2),
            "mandate_id": mandate_id,
            "reasons": reasons,
            "evidence_hash": evidence_hash,
            "ts": ts or _dt.datetime.now(_dt.timezone.utc)
                       .isoformat(timespec="seconds"),
            "prev_hash": self._heads.get(agent, "GENESIS"),
        }
        receipt = {"payload": payload, "hash": self._sign(payload)}
        self._heads[agent] = receipt["hash"]
        self._receipts[payload["receipt_id"]] = receipt
        return receipt

    @staticmethod
    def verify_signature(receipt: dict, secret: str | None = None) -> bool:
        secret = (secret or config.RECEIPT_SECRET).encode("utf-8")
        expected = hmac.new(secret, _canonical(receipt["payload"]),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.get("hash", ""))

    def verify_chain(self, agent: str, receipts: list[dict],
                     expected_head: str | None = None) -> bool:
        """Recomputes every signature and checks the per-agent hash links.

        Note: a chain truncated from the END is a valid prefix by construction
        — pass ``expected_head`` (the agent's current head hash) to detect
        missing tail receipts.
        """
        secret = self.secret.decode("utf-8")
        prev = "GENESIS"
        for receipt in receipts:
            if receipt["payload"].get("prev_hash") != prev:
                return False
            if not self.verify_signature(receipt, secret=secret):
                return False
            prev = receipt["hash"]
        if expected_head is not None and receipts:
            return receipts[-1]["hash"] == expected_head
        return True

    def head(self, agent: str) -> str:
        return self._heads.get(agent, "")

    def chain_for(self, agent: str) -> list[dict]:
        return [r for r in self._receipts.values()
                if r["payload"]["agent"] == agent]
