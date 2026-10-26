"""SQLite persistence: mandates, attempts, receipts, holds. Stdlib only."""
import datetime as _dt
import json
import os
import sqlite3
import threading

from ..engine.policy import Decision, LedgerEntry, Mandate

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
  id TEXT PRIMARY KEY, agent TEXT, payees TEXT, per_tx REAL, daily REAL,
  monthly REAL, purposes TEXT, expires_at TEXT, currency TEXT);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, payee TEXT,
  amount REAL, purpose TEXT, decision TEXT, reasons TEXT, mandate_id TEXT,
  receipt_id TEXT, order_id TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS receipts (
  receipt_id TEXT PRIMARY KEY, agent TEXT, seq INTEGER, doc TEXT);
CREATE TABLE IF NOT EXISTS holds (
  receipt_id TEXT PRIMARY KEY, attempt_id INTEGER, status TEXT,
  resolved_at TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, kind TEXT, agent TEXT, purpose TEXT, severity TEXT,
  summary TEXT, evidence TEXT, status TEXT DEFAULT 'open', ts TEXT);
"""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "leash.db")
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    # -- mandates --------------------------------------------------------------
    def save_mandate(self, m: Mandate) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO mandates VALUES (?,?,?,?,?,?,?,?,?)",
                (m.id, m.agent, json.dumps(sorted(m.payees_allowlist)),
                 m.per_tx_limit, m.daily_limit, m.monthly_limit,
                 json.dumps(sorted(m.purposes)), m.expires_at, m.currency))

    def load_mandates(self) -> dict[str, Mandate]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mandates").fetchall()
        out = {}
        for r in rows:
            out[r["id"]] = Mandate(
                id=r["id"], agent=r["agent"],
                payees_allowlist=set(json.loads(r["payees"])),
                per_tx_limit=r["per_tx"], daily_limit=r["daily"],
                monthly_limit=r["monthly"],
                purposes=set(json.loads(r["purposes"])),
                expires_at=r["expires_at"], currency=r["currency"])
        return out

    # -- attempts / ledger -------------------------------------------------------
    def add_attempt(self, *, ts: str, agent: str, payee: str, amount: float,
                    purpose: str, decision: Decision, reasons: list[str],
                    mandate_id: str | None, receipt_id: str,
                    order_id: str = "", evidence: dict | None = None) -> int:
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO attempts (ts,agent,payee,amount,purpose,decision,"
                "reasons,mandate_id,receipt_id,order_id,evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, agent, payee, amount, purpose, decision.value,
                 json.dumps(reasons), mandate_id, receipt_id, order_id,
                 json.dumps(evidence or {})))
            return int(cur.lastrowid)

    def update_attempt(self, attempt_id: int, *, decision: Decision,
                       order_id: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE attempts SET decision=?, order_id=? WHERE id=?",
                       (decision.value, order_id, attempt_id))

    def history_for(self, agent: str) -> list[LedgerEntry]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM attempts WHERE agent=? ORDER BY id",
                (agent,)).fetchall()
        return [LedgerEntry(agent=r["agent"], payee=r["payee"],
                            amount=r["amount"], ts=r["ts"],
                            decision=Decision(r["decision"]),
                            purpose=r["purpose"]) for r in rows]

    def attempts(self, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM attempts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- receipts -----------------------------------------------------------------
    def save_receipt(self, receipt: dict) -> None:
        with self._lock, self._connect() as db:
            seq = db.execute("SELECT COUNT(*) FROM receipts WHERE agent=?",
                             (receipt["payload"]["agent"],)).fetchone()[0] + 1
            db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)",
                       (receipt["payload"]["receipt_id"],
                        receipt["payload"]["agent"], seq,
                        json.dumps(receipt, sort_keys=True)))

    def chain_for(self, agent: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT doc FROM receipts WHERE agent=? ORDER BY seq",
                (agent,)).fetchall()
        return [json.loads(r["doc"]) for r in rows]

    # -- holds ----------------------------------------------------------------------
    def open_hold(self, receipt_id: str, attempt_id: int) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO holds VALUES (?,?,?,?)",
                       (receipt_id, attempt_id, "pending", ""))

    def seed_history(self, entries: list[LedgerEntry]) -> None:
        """Pre-load ALLOWED history (watchdog demo). No receipts: raw facts."""
        with self._lock, self._connect() as db:
            for h in entries:
                db.execute(
                    "INSERT INTO attempts (ts,agent,payee,amount,purpose,"
                    "decision,reasons,mandate_id,receipt_id,order_id,evidence)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (h.ts, h.agent, h.payee, h.amount, h.purpose,
                     h.decision.value, "[]", None, "seed", "", "{}"))

    # -- alerts -----------------------------------------------------------------------
    def save_alert(self, alert: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?)",
                       (alert["id"], alert["kind"], alert["agent"],
                        alert["purpose"], alert["severity"], alert["summary"],
                        json.dumps(alert.get("evidence", {}), sort_keys=True),
                        alert.get("status", "open"), alert["ts"]))

    def alert_exists(self, summary: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM alerts WHERE summary=?",
                             (summary,)).fetchone()
            return row is not None

    def alerts(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM alerts"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY ts DESC"
        with self._connect() as db:
            rows = db.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            out.append(d)
        return out

    def set_alert_status(self, alert_id: str, status: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE alerts SET status=? WHERE id=?",
                       (status, alert_id))

    def agents(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT agent FROM attempts").fetchall()
        return [r["agent"] for r in rows]

    def resolve_hold(self, receipt_id: str, status: str) -> int | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT attempt_id FROM holds WHERE receipt_id=?",
                             (receipt_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE holds SET status=?, resolved_at=? "
                       "WHERE receipt_id=?", (status, _now(), receipt_id))
            return int(row["attempt_id"])

    def open_holds(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT h.receipt_id, h.status, a.agent, a.payee, a.amount, "
                "a.reasons, a.id AS attempt_id FROM holds h "
                "JOIN attempts a ON a.id = h.attempt_id "
                "WHERE h.status='pending' ORDER BY a.id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["reasons"] = json.loads(d["reasons"])
            out.append(d)
        return out
