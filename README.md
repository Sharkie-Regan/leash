# Leash 🔒

**The trust layer for agentic commerce.** Would you let an AI touch your wallet?
With Leash — yes, because the agent is on a leash: human-approved **mandates**
(spending contracts), a **deterministic approval gate** on every payment attempt,
**tamper-evident receipt chains**, a **watchdog** that detects behavioral drift
(price creep, payee swaps, runaway bursts), and **auto-drafted PayPal disputes**
with the verified evidence attached.

Built for the PayPal AI Hackathon ("Build What's Next with PayPal and AI").
PayPal sandbox Orders v2 moves the money; AI explains and drafts — **it never
decides** (the gate is deterministic, reproducible, and adversarially tested).

**🌐 Live demo: https://leash-tbpe.onrender.com** — free tier, so the first
click after idle takes ~50 s to wake the server. Then hit **🐺 Run Rex**.

## The 60-second demo

1. Click **🐺 Run Rex** — our demo shopping agent tries 7 payments live:
   egregious overspend (DENIED), unlisted merchant (HELD for human),
   out-of-scope "self-upgrade" (HELD), split-purchase evasion — three slices
   under the limit that sum over it (slices 1-2 pass, slice 3 HELD with the
   evasion reason), and one honest purchase that sails through instantly with
   a PayPal order created and an HMAC-signed receipt issued.
2. Click **🩺 Seed drift + scan** — the watchdog reads the ledger stream and
   surfaces three alerts no single-payment rule can see: a payee swap on a
   recurring purpose (redirection attack), subscription price creep, and a
   micro-payment burst.
3. Click **Draft PayPal dispute** on any alert — Leash assembles the evidence
   bundle and drafts the dispute letter (LLM narration with a deterministic
   template fallback).
4. Every attempt, verdict, reason, and receipt is in the AG Grid audit table;
   `GET /receipts/{agent}` re-verifies the whole hash-linked chain live.

## How it works

- **Mandates** — per-agent contracts: payee allowlist, per-transaction /
  daily / monthly limits, purpose scope, expiry.
- **The gate** (`leash/engine/policy.py`) — pure function:
  (mandates, request, history) → ALLOW / HOLD_FOR_HUMAN / DENY with reasons
  and evidence. Rules: allowlist, purpose scope, per-tx overshoot (slight →
  HOLD, >2× → DENY), daily/monthly budgets, and split-purchase evasion
  (≥3 under-limit attempts to one payee in 15 min totaling >80% of the limit —
  only under-limit attempts count as "slices").
- **Receipts** (`leash/engine/receipts.py`) — every decision gets an
  HMAC-SHA256-signed receipt hash-linked to the agent's previous receipt;
  tampering, reordering, and tail-truncation (via head hash) are all detected.
- **PayPal** (`leash/paypal/client.py`) — Orders v2 on ALLOW: order created,
  payer approves at PayPal (the human's one click, by design), capture
  available post-approval. Sandbox-first; `dry` mode for offline tests.
- **Watchdog** (`leash/engine/drift.py`) — stream analysis of ALLOWED history:
  PRICE_DRIFT (≥20% + ≥1.00 rise on a recurring payment), PAYEE_SWAP (payee
  change on a recurring purpose within 30 days), FREQUENCY_BURST (≥5 payments
  to one payee in 24h). Idempotent scans; alerts feed dispute drafting.
- **LLM layer** (`leash/llm/explainer.py`) — provider-agnostic
  (Anthropic / OpenAI / Gemini, or none): plain-language verdict explanations
  and dispute letters, with deterministic templates as the always-available
  fallback. The LLM has zero authority over money.
- **Console** (`web/`) — AG Grid audit table, human gates with
  approve/deny (approval creates the PayPal order), watchdog alerts, Rex demo.
- **Integrations** — Zapier catch-hook webhooks fire on every hold/alert/
  dispute (set `LEASH_ZAPIER_HOOK_URL`); deployed on Render (see below).

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # or .venv/bin/pip on mac/linux
.venv/Scripts/python -m pytest tests                # 32 tests: gate, receipts, API, watchdog, Rex
.venv/Scripts/python -m uvicorn leash.api.app:create_app --factory --port 8000
# open http://127.0.0.1:8000  (PayPal defaults to "dry" mode without creds —
# every feature works; ALLOWs return simulated orders)
```

### Using your own PayPal sandbox

Create a REST app at [developer.paypal.com](https://developer.paypal.com)
(Sandbox), then either export env vars or drop them in a git-ignored `.env`:

```
LEASH_PAYPAL_CLIENT_ID=...
LEASH_PAYPAL_SECRET=...
LEASH_PAYPAL_ENV=sandbox
```

ALLOWed spends now create **real sandbox orders** (returning the payer-approve
URL). Capture requires the payer's approval click at PayPal — that's the
product's trust model, not a limitation of the demo.

### Environment variables

| Var | Purpose |
|---|---|
| `LEASH_PAYPAL_CLIENT_ID` / `LEASH_PAYPAL_SECRET` | PayPal sandbox (or live) credentials |
| `LEASH_PAYPAL_ENV` | `sandbox` (default) or `live` |
| `LEASH_PAYPAL_MODE` | `live` (default when creds exist) or `dry` |
| `LEASH_RECEIPT_SECRET` | HMAC key for the receipt chain — set a strong one in prod |
| `LEASH_ZAPIER_HOOK_URL` | Zapier catch-hook for instant alerts |
| `LEASH_LLM_PROVIDER` / `LEASH_LLM_KEY` / `LEASH_LLM_MODEL` | `anthropic` / `openai` / `gemini`, or omit for templates |
| `LEASH_DATA_DIR` | SQLite ledger location (default `./data`) |

## Deploy on Render

`render.yaml` included. Create the service, set the env vars above in the
dashboard (never commit them), done. Dockerfile builds the same image locally:

```bash
docker build -t leash . && docker run -p 8000:8000 --env-file .env leash
```

## Security posture

- No secrets in code; `.env` is git-ignored; judges use their own sandbox creds.
- Money decisions are 100% deterministic and testable — the LLM only explains.
- Every attempt (allowed, held, or denied) is receipted: denials are evidence too.
- The receipt chain makes the audit log tamper-evident, not just append-only.
