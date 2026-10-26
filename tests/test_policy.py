"""Ground-truth tests for the deterministic approval gate."""
import datetime as dt

from leash.engine.policy import (Decision, LedgerEntry, Mandate,
                                 PaymentRequest, PolicyEngine)

NOW = "2026-10-26T12:00:00+00:00"


def mandate(**kw):
    base = dict(id="md-1", agent="rex",
                payees_allowlist={"books@example.com", "cloud@example.com"},
                per_tx_limit=100.0, daily_limit=200.0, monthly_limit=1000.0)
    base.update(kw)
    return Mandate(**base)


def req(**kw):
    base = dict(agent="rex", payee="books@example.com", amount=50.0,
                purpose="office-supplies", ts=NOW)
    base.update(kw)
    return PaymentRequest(**base)


def entry(**kw):
    base = dict(agent="rex", payee="books@example.com", amount=50.0,
                ts=NOW, decision=Decision.ALLOW, purpose="office-supplies")
    base.update(kw)
    return LedgerEntry(**base)


def engine(m=None):
    m = m or mandate()
    return PolicyEngine({m.id: m})


def test_happy_path_allows():
    v = engine().evaluate(req(), [])
    assert v.decision is Decision.ALLOW and not v.reasons
    assert v.evidence["daily_spent_before"] == 0.0


def test_no_mandate_denies():
    v = engine().evaluate(req(agent="stranger"), [])
    assert v.decision is Decision.DENY
    assert "no active mandate" in v.reasons[0]


def test_expired_mandate_denies():
    m = mandate(expires_at="2026-10-01T00:00:00+00:00")
    v = engine(m).evaluate(req(), [])
    assert v.decision is Decision.DENY and "expired" in v.reasons[0]


def test_unlisted_payee_holds():
    v = engine().evaluate(req(payee="sketchy-shop@example.com"), [])
    assert v.decision is Decision.HOLD_FOR_HUMAN
    assert "not in the mandate allowlist" in v.reasons[0]


def test_purpose_outside_scope_holds():
    m = mandate(purposes={"office-supplies"})
    v = engine(m).evaluate(req(purpose="luxury-cat-toys"), [])
    assert v.decision is Decision.HOLD_FOR_HUMAN
    assert "outside mandate scope" in v.reasons[0]


def test_slight_overspend_holds_but_far_overspend_denies():
    v_hold = engine().evaluate(req(amount=150.0), [])   # 1.5x limit
    assert v_hold.decision is Decision.HOLD_FOR_HUMAN
    assert any("over the per-transaction limit" in r for r in v_hold.reasons)

    v_deny = engine().evaluate(req(amount=250.0), [])   # 2.5x limit
    assert v_deny.decision is Decision.DENY
    assert any("by more than" in r for r in v_deny.reasons)


def test_daily_budget_hold_counts_allowed_spend_only():
    history = [entry(amount=160.0, ts="2026-10-26T09:00:00+00:00")]
    v = engine().evaluate(req(amount=50.0), history)
    assert v.decision is Decision.HOLD_FOR_HUMAN
    assert any("daily budget" in r for r in v.reasons)
    assert v.evidence["daily_spent_before"] == 160.0

    # a DENIED attempt earlier must not count toward the budget
    history_denied = [entry(amount=160.0, decision=Decision.DENY)]
    v2 = engine().evaluate(req(amount=50.0), history_denied)
    assert v2.decision is Decision.ALLOW


def test_monthly_budget_hold():
    history = [entry(amount=980.0, ts="2026-10-02T09:00:00+00:00")]
    v = engine().evaluate(req(amount=50.0), history)
    assert v.decision is Decision.HOLD_FOR_HUMAN
    assert any("monthly budget" in r for r in v.reasons)


def test_split_purchase_evasion_detected():
    history = [
        entry(amount=35.0, ts="2026-10-26T11:45:00+00:00"),
        entry(amount=30.0, ts="2026-10-26T11:52:00+00:00"),
    ]
    v = engine().evaluate(req(amount=40.0), history)  # 3rd attempt, sum 105
    assert v.decision is Decision.HOLD_FOR_HUMAN
    assert any("split-purchase evasion" in r for r in v.reasons)
    assert v.evidence["same_payee_attempts_in_window"] == 2


def test_old_attempts_outside_window_do_not_trigger_split():
    history = [
        entry(amount=35.0, ts="2026-10-26T09:00:00+00:00"),   # >15 min ago
        entry(amount=30.0, ts="2026-10-26T09:05:00+00:00"),
    ]
    v = engine().evaluate(req(amount=40.0), history)
    assert v.decision is Decision.ALLOW


def test_small_sum_does_not_trigger_split():
    history = [
        entry(amount=5.0, ts="2026-10-26T11:45:00+00:00"),
        entry(amount=5.0, ts="2026-10-26T11:52:00+00:00"),
    ]
    v = engine().evaluate(req(amount=5.0), history)   # sum 15 << 80% of 100
    assert v.decision is Decision.ALLOW


def test_multiple_reasons_combine_with_worst_wins():
    v = engine().evaluate(req(payee="sketchy@example.com", amount=250.0), [])
    assert v.decision is Decision.DENY                  # worst of HOLD+DENY
    assert len(v.reasons) >= 2
