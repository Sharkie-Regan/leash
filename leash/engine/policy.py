"""The deterministic approval gate — the heart of Leash.

Pure function of (mandates, request, history) -> verdict. No LLM here on
purpose: money decisions must be reproducible and testable; the LLM layer only
explains and drafts, it never decides.
"""
import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum

from .. import config


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Decision(str, Enum):
    ALLOW = "ALLOW"
    HOLD_FOR_HUMAN = "HOLD_FOR_HUMAN"
    DENY = "DENY"


@dataclass
class Mandate:
    """A human-approved spending contract for one agent."""
    id: str
    agent: str
    payees_allowlist: set[str]
    per_tx_limit: float
    daily_limit: float
    monthly_limit: float
    purposes: set[str] = field(default_factory=set)   # empty = any purpose
    expires_at: str | None = None                      # ISO-8601, UTC
    currency: str = "USD"


@dataclass
class PaymentRequest:
    agent: str
    payee: str
    amount: float
    currency: str = "USD"
    purpose: str = "general"
    idempotency_key: str = ""
    ts: str = ""                                        # ISO-8601; now if empty
    meta: dict = field(default_factory=dict)


@dataclass
class LedgerEntry:
    """One evaluated request (allowed or not) — the audit trail input."""
    agent: str
    payee: str
    amount: float
    ts: str
    decision: Decision
    purpose: str = "general"


@dataclass
class Verdict:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    mandate_id: str | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class PolicyEngine:
    def __init__(self, mandates: dict[str, Mandate]):
        self.mandates = mandates

    def evaluate(self, req: PaymentRequest,
                 history: list[LedgerEntry]) -> Verdict:
        ts = _dt.datetime.fromisoformat(req.ts) if req.ts else _now()

        mandate = next(
            (m for m in self.mandates.values()
             if m.agent == req.agent), None)

        if mandate is None:
            return Verdict(Decision.DENY, [f"no active mandate for agent '{req.agent}'"])

        if mandate.expires_at:
            exp = _dt.datetime.fromisoformat(mandate.expires_at)
            if ts >= exp:
                return Verdict(Decision.DENY,
                               [f"mandate {mandate.id} expired at {mandate.expires_at}"],
                               mandate_id=mandate.id)

        reasons: list[str] = []
        worst = Decision.ALLOW

        def raise_to(d: Decision):
            nonlocal worst
            if d is Decision.DENY or (d is Decision.HOLD_FOR_HUMAN
                                      and worst is Decision.ALLOW):
                worst = d

        # -- payee allowlist --------------------------------------------------
        if req.payee not in mandate.payees_allowlist:
            reasons.append(f"payee '{req.payee}' is not in the mandate allowlist")
            raise_to(Decision.HOLD_FOR_HUMAN)

        # -- purpose scope ----------------------------------------------------
        if mandate.purposes and req.purpose not in mandate.purposes:
            reasons.append(f"purpose '{req.purpose}' outside mandate scope "
                           f"{sorted(mandate.purposes)}")
            raise_to(Decision.HOLD_FOR_HUMAN)

        # -- per-transaction limit ---------------------------------------------
        if req.amount > mandate.per_tx_limit:
            if req.amount > mandate.per_tx_limit * config.OVERSHOOT_HOLD_FACTOR:
                reasons.append(
                    f"{req.amount:.2f} exceeds the per-transaction limit "
                    f"{mandate.per_tx_limit:.2f} by more than "
                    f"{int((config.OVERSHOOT_HOLD_FACTOR - 1) * 100)}%")
                raise_to(Decision.DENY)
            else:
                reasons.append(
                    f"{req.amount:.2f} is over the per-transaction limit "
                    f"{mandate.per_tx_limit:.2f} (within human-review tolerance)")
                raise_to(Decision.HOLD_FOR_HUMAN)

        # -- budgets (count ALLOWED spend only) --------------------------------
        day_key = ts.strftime("%Y-%m-%d")
        month_key = ts.strftime("%Y-%m")
        allowed = [h for h in history
                   if h.agent == req.agent and h.decision is Decision.ALLOW]

        def _spent(key_fn) -> float:
            return sum(h.amount for h in allowed
                       if _dt.datetime.fromisoformat(h.ts).strftime(key_fn[0]) == key_fn[1])

        daily_spent = _spent(("%Y-%m-%d", day_key))
        monthly_spent = _spent(("%Y-%m", month_key))

        if daily_spent + req.amount > mandate.daily_limit:
            reasons.append(
                f"daily budget: {daily_spent:.2f} spent + {req.amount:.2f} requested "
                f"exceeds {mandate.daily_limit:.2f}")
            raise_to(Decision.HOLD_FOR_HUMAN)
        if monthly_spent + req.amount > mandate.monthly_limit:
            reasons.append(
                f"monthly budget: {monthly_spent:.2f} spent + {req.amount:.2f} requested "
                f"exceeds {mandate.monthly_limit:.2f}")
            raise_to(Decision.HOLD_FOR_HUMAN)

        # -- split-purchase evasion ---------------------------------------------
        # Only attempts at or under the per-tx limit count as "slices": a
        # DENIED over-limit attempt is a probe, not a slice.
        window_start = ts - _dt.timedelta(seconds=config.SPLIT_WINDOW_SECONDS)
        same_payee_attempts = [
            h for h in history
            if h.agent == req.agent and h.payee == req.payee
            and h.amount <= mandate.per_tx_limit
            and _dt.datetime.fromisoformat(h.ts) >= window_start
        ]
        attempt_sum = sum(h.amount for h in same_payee_attempts) + req.amount
        if (len(same_payee_attempts) + 1 >= config.SPLIT_MIN_ATTEMPTS
                and attempt_sum > mandate.per_tx_limit * config.SPLIT_SUM_FRACTION):
            reasons.append(
                f"possible split-purchase evasion: {len(same_payee_attempts) + 1} "
                f"requests to '{req.payee}' within "
                f"{config.SPLIT_WINDOW_SECONDS // 60} min total {attempt_sum:.2f} "
                f"(> {int(config.SPLIT_SUM_FRACTION * 100)}% of the per-transaction limit)")
            raise_to(Decision.HOLD_FOR_HUMAN)

        return Verdict(worst, reasons, mandate_id=mandate.id, evidence={
            "per_tx_limit": mandate.per_tx_limit,
            "daily_spent_before": round(daily_spent, 2),
            "daily_limit": mandate.daily_limit,
            "monthly_spent_before": round(monthly_spent, 2),
            "monthly_limit": mandate.monthly_limit,
            "same_payee_attempts_in_window": len(same_payee_attempts),
        })
