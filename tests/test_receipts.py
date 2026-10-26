"""Receipt chain: signatures, tamper detection, chain integrity."""
import copy

from leash.engine.receipts import ReceiptChain


def issue_three(chain, agent="rex"):
    return [
        chain.issue(agent, "ALLOW", "books@example.com", 42.0, "md-1", [],
                    "evhash1", ts="2026-10-26T10:00:00+00:00"),
        chain.issue(agent, "HOLD_FOR_HUMAN", "cloud@example.com", 120.0, "md-1",
                    ["over the per-transaction limit"], "evhash2",
                    ts="2026-10-26T11:00:00+00:00"),
        chain.issue(agent, "DENY", "sketchy@example.com", 300.0, "md-1",
                    ["no active mandate"], "evhash3",
                    ts="2026-10-26T12:00:00+00:00"),
    ]


def test_signature_roundtrip_and_tamper_detection():
    chain = ReceiptChain(secret="unit-secret")
    receipt = chain.issue("rex", "ALLOW", "books@example.com", 42.0,
                          "md-1", [], "evh", ts="2026-10-26T10:00:00+00:00")
    assert ReceiptChain.verify_signature(receipt, secret="unit-secret")

    forged = copy.deepcopy(receipt)
    forged["payload"]["amount"] = 0.01
    assert not ReceiptChain.verify_signature(forged, secret="unit-secret")


def test_chain_links_and_detects_reordering_and_removal():
    chain = ReceiptChain(secret="unit-secret")
    receipts = issue_three(chain)
    head = chain.head("rex")
    assert chain.verify_chain("rex", receipts, expected_head=head)

    # tail truncation: a valid prefix, but the head check catches the missing end
    assert chain.verify_chain("rex", receipts[:2])
    assert not chain.verify_chain("rex", receipts[:2], expected_head=head)

    reordered = [receipts[1], receipts[0], receipts[2]]
    assert not chain.verify_chain("rex", reordered)             # swapped order

    tampered = copy.deepcopy(receipts[1])
    tampered["payload"]["amount"] = 1.0
    assert not chain.verify_chain("rex", [receipts[0], tampered, receipts[2]])


def test_agents_have_independent_chains():
    chain = ReceiptChain(secret="unit-secret")
    issue_three(chain, agent="rex")
    issue_three(chain, agent="hal")
    assert chain.verify_chain("rex", chain.chain_for("rex"))
    assert chain.verify_chain("hal", chain.chain_for("hal"))
    assert chain.chain_for("rex")[0]["payload"]["prev_hash"] == "GENESIS"
    assert chain.chain_for("hal")[0]["payload"]["prev_hash"] == "GENESIS"
