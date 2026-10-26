"""The watchdog: behavioral drift detection over Leash's own ledger.

Gate rules decide single payments; the watchdog watches the STREAM for things
no single payment reveals: subscription price creep, a payee swap on a
recurring purpose (classic redirection attack), and burst frequency.
"""
import datetime as _dt
import json
import uuid
from dataclasses import asdict, dataclass, field

from .policy import Decision, LedgerEntry

PRICE_DRIFT_FACTOR = 1.20      # recurring amount rose >=20% ...
PRICE_DRIFT_MIN_DELTA = 1.00   # ... and by >= 1.00 currency units
SWAP_WINDOW_DAYS = 30
BURST_COUNT = 5                # >=5 allowed payments to one payee in 24h
BURST_WINDOW_HOURS = 24


@dataclass
class Alert:
    kind: str                  # PRICE_DRIFT | PAYEE_SWAP | FREQUENCY_BURST
    agent: str
    purpose: str
    severity: str              # high | medium
    summary: str
    evidence: dict = field(default_factory=dict)
    id: str = ""
    ts: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"al-{uuid.uuid4().hex[:8]}"
        if not self.ts:
            self.ts = _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds")

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts)


def analyze(history: list[LedgerEntry]) -> list[Alert]:
    allowed = sorted((h for h in history
                      if h.decision is Decision.ALLOW),
                     key=lambda h: _parse(h.ts))
    alerts: list[Alert] = []

    # -- price drift on recurring (agent, purpose, payee) ----------------------
    groups: dict[tuple, list[LedgerEntry]] = {}
    for h in allowed:
        groups.setdefault((h.agent, h.purpose, h.payee), []).append(h)
    for (agent, purpose, payee), entries in groups.items():
        if len(entries) < 2:
            continue
        first, last = entries[0], entries[-1]
        if (last.amount >= first.amount * PRICE_DRIFT_FACTOR
                and last.amount - first.amount >= PRICE_DRIFT_MIN_DELTA):
            alerts.append(Alert(
                kind="PRICE_DRIFT", agent=agent, purpose=purpose,
                severity="high",
                summary=(f"recurring '{purpose}' payment to {payee} rose from "
                         f"{first.amount:.2f} to {last.amount:.2f} "
                         f"(+{(last.amount / first.amount - 1) * 100:.0f}%)"),
                evidence={"payee": payee, "first_amount": first.amount,
                          "last_amount": last.amount,
                          "first_ts": first.ts, "last_ts": last.ts,
                          "occurrences": len(entries)}))

    # -- payee swap on a recurring purpose --------------------------------------
    by_purpose: dict[tuple, list[LedgerEntry]] = {}
    for h in allowed:
        by_purpose.setdefault((h.agent, h.purpose), []).append(h)
    for (agent, purpose), entries in by_purpose.items():
        payees = {}
        for h in entries:
            payees.setdefault(h.payee, []).append(h)
        if len(payees) < 2:
            continue
        ordered = sorted(payees.items(),
                         key=lambda kv: _parse(kv[1][0].ts))
        old_payee, old_entries = ordered[0]
        new_payee, new_entries = ordered[-1]
        if old_payee == new_payee:
            continue
        gap_days = (_parse(new_entries[0].ts)
                    - _parse(old_entries[-1].ts)).total_seconds() / 86400
        if 0 <= gap_days <= SWAP_WINDOW_DAYS:
            alerts.append(Alert(
                kind="PAYEE_SWAP", agent=agent, purpose=purpose,
                severity="high",
                summary=(f"recurring '{purpose}' payments switched payee: "
                         f"{old_payee} -> {new_payee} within {gap_days:.0f}d"),
                evidence={"old_payee": old_payee, "new_payee": new_payee,
                          "old_last_ts": old_entries[-1].ts,
                          "new_first_ts": new_entries[0].ts,
                          "old_amounts": [e.amount for e in old_entries],
                          "new_amounts": [e.amount for e in new_entries]}))

    # -- frequency burst ----------------------------------------------------------
    for (agent, _purpose, payee), entries in groups.items():
        window = _dt.timedelta(hours=BURST_WINDOW_HOURS)
        for i, h in enumerate(entries):
            recent = [e for e in entries[i:]
                      if _parse(e.ts) - _parse(h.ts) <= window]
            if len(recent) >= BURST_COUNT:
                alerts.append(Alert(
                    kind="FREQUENCY_BURST", agent=agent, purpose=_purpose,
                    severity="medium",
                    summary=(f"{len(recent)} payments to {payee} within "
                             f"{BURST_WINDOW_HOURS}h (possible runaway agent)"),
                    evidence={"payee": payee, "count": len(recent),
                              "window_start": recent[0].ts,
                              "window_end": recent[-1].ts,
                              "total": round(sum(e.amount for e in recent), 2)}))
                break

    return alerts


def evidence_json(alert: Alert) -> str:
    return json.dumps(alert.evidence, sort_keys=True)
