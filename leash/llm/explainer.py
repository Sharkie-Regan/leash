"""Provider-agnostic explainer + dispute drafter.

LLMs only EXPLAIN and DRAFT here — never decide (the policy engine is
deterministic by design). No key configured? Deterministic templates render
the same content. Providers: anthropic | openai | gemini (free tiers fine).
"""
import json
import os

import httpx

PROVIDER = os.environ.get("LEASH_LLM_PROVIDER", "none")
KEY = os.environ.get("LEASH_LLM_KEY", "")
MODEL = os.environ.get("LEASH_LLM_MODEL", {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
}.get(PROVIDER, ""))


# ---------------------------------------------------------------- templates
def explain_verdict_template(verdict: dict) -> str:
    d = verdict.get("decision", "?")
    reasons = "; ".join(verdict.get("reasons") or ["no rule violations"])
    if d == "ALLOW":
        return (f"Leash ALLOWED this payment: it stayed inside every rule of "
                f"the agent's mandate ({reasons}). A PayPal order was created; "
                f"the human approves it at PayPal.")
    if d == "HOLD_FOR_HUMAN":
        return (f"Leash HELD this payment for human review: {reasons}. "
                f"No money moves until a person approves.")
    return (f"Leash DENIED this payment outright: {reasons}. "
            f"The attempt is receipted and logged.")


def draft_dispute_template(alert: dict, receipts: list[dict]) -> dict:
    ev = alert.get("evidence", {})
    chain_ok = all(r.get("hash") for r in receipts)
    kind = alert.get("kind", "ANOMALY")
    body = (
        f"To the PayPal Resolution Team,\n\n"
        f"I am disputing the following transaction pattern detected by my "
        f"automated payment-control system, Leash, which gates every payment "
        f"my AI agent attempts.\n\n"
        f"ISSUE: {alert.get('summary', 'unauthorized pattern')}\n\n"
        f"EVIDENCE (machine-verified, HMAC-signed receipt chain attached):\n"
        f"- Pattern type: {kind}\n"
        f"- Agent: {alert.get('agent', 'unknown')}\n"
        f"- Purpose: {alert.get('purpose', 'unknown')}\n"
        f"- Detail: {json.dumps(ev, sort_keys=True)}\n"
        f"- Receipt chain integrity: {'VERIFIED' if chain_ok else 'CHECK'} "
        f"({len(receipts)} receipts)\n\n"
        f"I did not knowingly authorize this change. I request reversal of the "
        f"affected transactions and closure of the affected mandate.\n\n"
        f"Respectfully,\nThe account holder (via Leash)")
    return {"title": f"Leash evidence bundle - {kind} "
                     f"[{alert.get('id', '')}]",
            "body": body, "drafted_by": "template"}


# ---------------------------------------------------------------- llm path
def _ask_llm(system: str, user: str) -> str | None:
    if PROVIDER == "none" or not KEY:
        return None
    try:
        if PROVIDER == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages",
                           headers={"x-api-key": KEY,
                                    "anthropic-version": "2023-06-01"},
                           json={"model": MODEL, "max_tokens": 800,
                                 "system": system,
                                 "messages": [{"role": "user",
                                               "content": user}]},
                           timeout=30)
            return r.json()["content"][0]["text"]
        if PROVIDER == "openai":
            r = httpx.post("https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {KEY}"},
                           json={"model": MODEL,
                                 "messages": [
                                     {"role": "system", "content": system},
                                     {"role": "user", "content": user}]},
                           timeout=30)
            return r.json()["choices"][0]["message"]["content"]
        if PROVIDER == "gemini":
            r = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{MODEL}:generateContent?key={KEY}",
                json={"contents": [{"parts": [{"text": user}]}],
                      "system_instruction": {"parts": [
                          {"text": system}]}},
                timeout=30)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (httpx.HTTPError, KeyError, IndexError):
        return None
    return None


def explain_verdict(verdict: dict) -> str:
    out = _ask_llm(
        "You explain Leash payment-gate verdicts to a non-technical account "
        "holder in 2-3 plain sentences. Never invent facts beyond the JSON.",
        json.dumps(verdict, sort_keys=True))
    return out or explain_verdict_template(verdict)


def draft_dispute(alert: dict, receipts: list[dict]) -> dict:
    llm = _ask_llm(
        "You draft concise, factual PayPal dispute letters from verified "
        "machine evidence. Only use facts in the JSON. Return plain text.",
        f"ALERT:\n{json.dumps(alert, sort_keys=True)}\n\nRECEIPTS (chain):\n"
        f"{json.dumps(receipts[:10], sort_keys=True)}")
    if llm:
        return {"title": f"Leash evidence bundle - {alert.get('kind', '')} "
                         f"[{alert.get('id', '')}]",
                "body": llm, "drafted_by": f"llm:{MODEL}"}
    return draft_dispute_template(alert, receipts)
