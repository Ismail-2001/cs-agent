# Customer Support AI Employee

AI-powered customer support automation for ecommerce. Classifies incoming tickets,
pulls real order data from Shopify, drafts a grounded reply, and syncs with Gorgias —
with a confidence-gated auto-send policy so nothing goes to a customer unreviewed
until you trust it.

## Memory (conversation threads)

Every ticket has a persisted message thread (`messages` table in SQLite). A brand-new ticket
seeds the thread with the customer's first message. Every follow-up — via
`POST /support/tickets/{id}/messages` or the Gorgias `message-created` webhook — appends to
the SAME thread and re-runs classification + drafting with the **full conversation as context**,
not just the newest message. This means:
- A customer who escalates on message 3 gets treated as escalating, not as a fresh neutral ticket.
- The AI won't repeat information it already gave in message 1.
- Human-sent replies (`/respond`) and auto-sent AI replies both get logged into the thread, so the
  next customer message is answered with full awareness of what's already been said.

Gorgias needs **two separate webhooks** configured (see setup below) — `ticket-created` for new
tickets and `message-created` for everything after. Point them at:
```
POST /support/webhooks/gorgias/ticket-created
POST /support/webhooks/gorgias/message-created
```

## Deployment model — one instance per client

This is a **single-tenant** system by design. Every deployed instance serves exactly
one client — one `.env`, one SQLite database, one Shopify store, one Gorgias account.

Do NOT route multiple clients through the same instance. `TENANT_NAME` is a
deployment-time label bound into every structlog log line so log aggregation is
unambiguous, but it is NOT a row-level isolation filter — there is no shared-database
mode here. Adding shared multi-tenant support would require a separate migration
(row-level tenant_id filtering + a connection-pooled Postgres/Supabase backend).

The startup auto-prefixes `DB_PATH` with `TENANT_NAME` when using the default
`cs_agent.db`, so a copy-pasted `.env` won't silently share a database file between
clients on the same filesystem.

## Architecture

```
Gorgias ticket-created webhook   ─┐
Gorgias message-created webhook ──┤
Manual API call (any channel)   ──┘
                                    │
                                    ▼
                     CustomerSupportAgent.handle_ticket() / .handle_followup()
                                    │
                                    ▼
                     1. Append message to persisted thread (agent/storage.py)
                                    │
                                    ▼
                      2. TicketClassifier (gpt-4o-mini via OpenRouter) — sees the FULL thread
                        category / priority / sentiment
                        + extracts order number if present
                                    │
                                    ▼
                     3. ShopifyClient
                        real order status/tracking/items
                        (only if category is order-related)
                                    │
                                    ▼
                      4. ResponseGenerationEngine (gpt-4o-mini via OpenRouter) — sees the FULL thread
                        drafts reply grounded in real data, aware of what was
                        already said earlier in the conversation
                        + confidence score
                                    │
                                    ▼
                     5. Auto-send gate (agent/support_agent.py)
                        confidence >= threshold
                        AND category not in blocklist
                        AND AUTO_SEND_ENABLED=true
                                    │
                       ┌────────────┴─────────────┐
                       ▼                           ▼
         Sent to customer via Gorgias   Internal note in Gorgias,
         (also logged into thread)      awaiting human approval
```

## Setup

### 1. Get your API keys

- **OpenRouter**: get a key at https://openrouter.ai/keys (or use Google Gemini free tier at https://aistudio.google.com/apikey if you prefer)
- **Shopify**: Admin > Settings > Apps and sales channels > Develop apps > create a custom app
  with the `read_orders` scope, install it, copy the Admin API access token.
- **Gorgias**: Settings > REST API > create an API key.

```bash
cp .env.example .env
# fill in .env with the keys above
```

### 2. Run locally

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8001
```

Visit `http://localhost:8001/docs` for interactive API docs.

### 3. Or run with Docker

```bash
docker compose up -d
```

### 4. Connect Gorgias

In Gorgias: Settings > REST API > Webhooks, add a webhook for `ticket-created` pointing at:
```
https://your-deployed-url.com/support/webhooks/gorgias
```
Add a custom header `x-webhook-secret` with the same value as `GORGIAS_WEBHOOK_SECRET` in your `.env`.

## What's in here now

| Feature | How it works |
|---|---|
| **Memory** | Every ticket is a persisted thread. Follow-ups re-classify with the full conversation, not just the latest message. |
| **RAG knowledge base** | Local vector search (SQLite + embeddings, zero extra infra) over your policies + product catalog. Replies ground policy/product claims in real content instead of guessing. |
| **Real actions** | The AI can *suggest* a refund/resend with an amount and reason — but never executes it. A human calls `POST /tickets/{id}/actions/refund` to actually move money via Shopify. Any suggested_action hard-blocks auto-send, no matter the confidence. |
| **Multi-channel** | Gorgias (email/chat/social it aggregates) + a generic `/webhooks/inbound` endpoint for anything else — WhatsApp via Twilio, a website chat widget, Instagram DM bridges — using the same thread/memory system. |
| **Escalation intelligence** | A customer on their 3rd+ unresolved message gets force-escalated to `urgent` and forced into human review, in code — not left to the model's mood that turn. |
| **Self-improvement tracking** | Every human-sent reply is diffed against the AI's draft. `/support/analytics/quality` shows edit rate by category — that's your signal for which categories need prompt work or more knowledge base content next. This doesn't retrain the model automatically; it tells you exactly where to spend the next hour. |

## Setting up the knowledge base

```bash
# Auto-pull your Shopify policies + product catalog:
curl -X POST http://localhost:8001/support/knowledge-base/sync-shopify

# Or add custom FAQ content manually:
curl -X POST http://localhost:8001/support/knowledge-base \
  -H "Content-Type: application/json" \
  -d '{"source": "faq:sizing", "title": "Sizing Guide", "content": "Our hoodies run true to size..."}'

# Test what it retrieves for a question, before it hits a real ticket:
curl -X POST http://localhost:8001/support/knowledge-base/search \
  -H "Content-Type: application/json" -d '{"query": "is this waterproof"}'
```

## Testing follow-up messages (memory) locally

```bash
# 1. Create a ticket, note the returned ticket_id
curl -X POST http://localhost:8001/support/tickets -H "Content-Type: application/json" \
  -d '{"customer_email":"test@example.com","subject":"Order question","body":"Where is my order #1001?"}'

# 2. Send a follow-up on the SAME ticket_id
curl -X POST http://localhost:8001/support/tickets/{ticket_id}/messages \
  -H "Content-Type: application/json" -d '{"body":"Still nothing after 3 days!"}'

# 3. View the full thread
curl http://localhost:8001/support/tickets/{ticket_id}/messages
```

## The safety model — read this before you flip `AUTO_SEND_ENABLED`

This is the difference between a tool that saves a support team hours and one that
gets a client's brand in trouble. Ship every new client with `AUTO_SEND_ENABLED=false`
for at least the first 1–2 weeks. Every AI draft lands as an **internal note** on the
Gorgias ticket instead of being sent — a human reviews and clicks send. Watch the
`/support/analytics` endpoint and the drafts themselves. Once you've seen enough
drafts to trust the categories it gets right, flip `AUTO_SEND_ENABLED=true`.

Even with auto-send on, these are hard-coded in `agent/support_agent.py` and can't be
overridden by the model talking itself into a high confidence score:
- `refund`, `complaint`, `legal` categories never auto-send (edit `AUTO_SEND_BLOCKED_CATEGORIES`)
- confidence must clear `AUTO_SEND_MIN_CONFIDENCE` (default 0.85)
- `very_negative` sentiment always requires a human

> **AUTO_SEND_MIN_CONFIDENCE = 0.85 was set during Gemini evaluation and must be
> re-validated against real OpenRouter/gpt-4o-mini traffic.** LLM self-reported
> confidence is not inherently calibrated — the Gemini model's confidence distribution
> may differ from gpt-4o-mini's. After you have sufficient volume, check
> `/support/analytics/calibration` and verify the edit rate in the 0.85–0.90 bucket
> is meaningfully lower than in the 0.5–0.6 bucket. If the confidence numbers are
> poorly calibrated for this model, raise or lower the threshold accordingly. See
> "Confidence calibration" in the Observability section for details.

## Security

Every `/support/*` endpoint EXCEPT webhooks requires an `X-API-Key` header matching `API_KEY`
in your `.env`. Generate a real one before deploying anywhere public:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Protection | What it covers |
|---|---|
| **API key auth** | All ticket/KB/analytics/action endpoints. Constant-time comparison (no timing attacks). |
| **Webhook secrets** | Gorgias (`GORGIAS_WEBHOOK_SECRET`) and generic inbound (`INBOUND_WEBHOOK_SECRET`) each need their own shared secret sent as a header, since those callers can't send an API key. |
| **Rate limiting** | Per-IP sliding window, 60/min default on everything, 10/min on the refund endpoint specifically. Returns 429. |
| **Refund idempotency** | Every refund call requires an `Idempotency-Key` header. The same key sent twice replays the first result instead of refunding twice — protects against retries, double-clicks, and network blips. |
| **Refund amount cap** | The requested amount is checked against the real Shopify order total before any refund is attempted; a request over the order total is rejected outright. |
| **Refund audit trail** | Every attempt (success or failure) is logged in the `refund_audit` table — who, when, how much, and the raw Shopify response. |
| **Prompt injection defense-in-depth** | Both the classifier and response engine are told customer text is untrusted data, not instructions — on top of the hard-coded (not prompt-based) auto-send/action gates that don't trust the model's word for it either way. |
| **CORS** | Set `ALLOWED_ORIGINS` to your dashboard's domain(s); empty means no browser JavaScript can call this API at all (server-to-server calls are unaffected). |
| **No leaked internals** | A global exception handler logs the real error server-side and returns a generic `{"detail": "Internal server error"}` to the client — no stack traces or exception text ever reach a caller. |

**What's intentionally NOT hardened yet** — a solo/small-agency-appropriate line to draw, not an oversight:
- No shared multi-tenant database — this is a one-instance-per-client architecture. If you later need a single deployment to serve multiple clients, the work is: row-level `tenant_id` filtering on every query, a connection-pooled Postgres/Supabase backend, and per-tenant rate limiting. That's a separate feature, not a bugfix in this one.
- Rate limiting is in-process (per Python process), not shared across instances — fine for Render's single free-tier instance, not for a multi-instance deploy behind a load balancer (move to Redis-backed limiting if you scale that way).
- No WAF/DDoS layer — that's Render/Cloudflare's job in front of this, not this app's.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
# (Add OPENROUTER_API_KEY=dummy or GOOGLE_API_KEY=dummy in CI — tests mock the LLM)
```

All 113+ tests run with the LLM and Shopify calls faked out — no real API key or network
needed, so this runs the same in CI as it does locally. Covers: memory/threading, the
auto-send safety gates (confidence, blocked categories, suggested_action blocking),
repeat-contact escalation, RAG chunking/search relevance, self-improvement edit tracking,
and every security control above — including a test that specifically proves the same
`Idempotency-Key` sent twice only refunds once.

## Observability & Evals

This is what separates "it worked in my demo" from actually knowing whether the agent is
good. Three pieces:

**1. Tracing — "why did it say that?"**
Every classifier and response-engine call is logged to a `traces` table: the exact
transcript/context that went in, the exact structured output that came out, latency,
tokens, and cost. Pull the full pipeline history for any ticket:
```bash
curl http://localhost:8001/support/tickets/{ticket_id}/trace -H "X-API-Key: $API_KEY"
```

**2. Confidence calibration — is the model's confidence number trustworthy?**
The model self-reports a confidence score, and `AUTO_SEND_MIN_CONFIDENCE` trusts that
number. LLM self-reported confidence is notoriously **not** well-calibrated out of the box.
`/support/analytics/calibration` buckets past AI drafts by confidence and shows the actual
edit rate in each bucket:
```bash
curl http://localhost:8001/support/analytics/calibration -H "X-API-Key: $API_KEY"
```
A well-calibrated model shows edit_rate dropping as confidence rises. If your 0.85-0.90
bucket gets edited just as often as your 0.5-0.6 bucket, the confidence number isn't
doing its job — raise `AUTO_SEND_MIN_CONFIDENCE` above whatever bucket is misbehaving.
This needs real volume (30+ samples per bucket) before it means anything — don't
over-read it in week one.

**3. Cost governance — real spend, with a circuit breaker**
Every LLM call's actual cost (computed from real token usage, not an estimate) is
persisted to `llm_costs`. `DAILY_COST_CAP_USD` in `.env` (default $5) is checked before
every auto-send decision — cross it, and auto-send force-disables itself until a human
investigates (tickets still get classified/drafted normally, just held for review):
```bash
curl http://localhost:8001/support/analytics/costs -H "X-API-Key: $API_KEY"
```

## Evals — does the agent actually work?

`evals/golden_dataset.json` has 15 labeled cases covering every category, escalation,
missing-order-number handling, and — importantly — **two adversarial prompt-injection
cases** that check the model doesn't get talked into confirming a fake refund or
overriding its own confidence score when a customer message tries to.

```bash
pip install -r requirements-dev.txt
python -m evals.run_evals                          # run everything against the configured LLM
python -m evals.run_evals --case refund_request_must_require_review   # run one case
python -m evals.run_evals --json report.json        # save a full report
```

Every full run auto-saves a report to `evals/results/` as
`<timestamp>_<classifier_version>-<response_version>.json`. **Commit these reports
alongside prompt changes** so `evals/results/` builds a versioned, searchable history
of how each prompt version performed.

After a prompt change, the workflow is:

1. Bump `PROMPT_VERSION` in `agent/classifier.py` and/or `agent/response_engine.py`
2. Run `python -m evals.run_evals` — the report auto-saves to `evals/results/`
3. Compare against the previous run:
   ```bash
   python -m evals.compare evals/results/20240710_classifier_v1-response_v1.json \
                           evals/results/20240711_classifier_v2-response_v1.json
   ```
4. The diff shows which cases flipped (pass→fail or fail→pass), the pass rate delta,
   and the category accuracy delta — this is the quantitative answer to "did my prompt
   change help or hurt?"
5. Commit the new report alongside the prompt change:
   ```bash
   git add evals/results/20240711_classifier_v2-response_v1.json agent/classifier.py
   git commit -m "classifier: tighten injection guard in system prompt"
   ```

The `prompt_version` is also recorded in every `traces` table row (via the
`prompt_version` column), so production traces are linked back to the exact prompt
version that produced them — no guessing "was this before or after the tweak".

Run this before merging any prompt change to `classifier.py` or `response_engine.py` — it's
the difference between knowing a prompt tweak helped and guessing. When you notice a
category with a high edit rate in `/analytics/quality`, pull a few of those real (edited)
tickets and add them to `golden_dataset.json` as regression cases — that's how this dataset
should grow over time, not by inventing more synthetic examples.

The eval harness's own scoring logic (`evals/scoring.py`) is unit-tested in
`tests/test_eval_harness.py` — fed deliberately wrong model output to confirm it actually
catches failures, not just deliberately right output to confirm it says PASS. A harness
that never fails is worse than no harness. The compare script and trace prompt_version
inclusion are tested in `tests/test_eval_versioning.py`.

## API reference

| Method | Path | What it does |
|---|---|---|
| POST | `/support/tickets` | Create + fully process a new ticket synchronously |
| GET | `/support/tickets` | List tickets (filter by status/category/priority) |
| GET | `/support/tickets/{id}` | Get one ticket + its AI suggestion |
| PATCH | `/support/tickets/{id}` | Update status/priority/notes |
| POST | `/support/tickets/{id}/messages` | Add a follow-up customer message (same thread) |
| GET | `/support/tickets/{id}/messages` | View the full conversation thread |
| GET | `/support/tickets/{id}/suggestion` | Re-fetch the stored AI draft |
| POST | `/support/tickets/{id}/respond` | Human sends the (possibly edited) reply; logs the edit for self-improvement tracking |
| POST | `/support/tickets/{id}/actions/refund` | Human-approved: executes a real Shopify refund. Requires `Idempotency-Key` header. |
| POST | `/support/knowledge-base` | Add/replace a KB document (policy, FAQ, spec sheet) |
| POST | `/support/knowledge-base/sync-shopify` | Auto-ingest Shopify policies + product catalog |
| GET | `/support/knowledge-base` | KB chunk count |
| POST | `/support/knowledge-base/search` | Debug: test KB retrieval for a query |
| POST | `/support/webhooks/inbound` | Generic entry point for non-Gorgias channels |
| POST | `/support/webhooks/gorgias/ticket-created` | Gorgias new-ticket webhook target |
| POST | `/support/webhooks/gorgias/message-created` | Gorgias follow-up-message webhook target |
| GET | `/support/analytics` | Volume, category/priority/sentiment breakdowns |
| GET | `/support/analytics/quality` | Edit rate by category (self-improvement signal) |
| GET | `/support/analytics/calibration` | Confidence calibration — is the confidence score trustworthy? |
| GET | `/support/analytics/costs` | Real LLM spend by day and stage |
| GET | `/support/tickets/{id}/trace` | Full pipeline trace for one ticket (debug "why did it say that") |
| GET | `/support/health` | Shopify/Gorgias connection status |

## What's stubbed vs. real

Real: classification, memory/threading, Shopify order lookups, RAG knowledge base search,
response drafting, suggested actions, refund execution, the auto-send + escalation gates,
Gorgias reply/internal-note posting, generic multi-channel ingestion, edit-rate tracking,
full pipeline tracing, real per-call cost tracking with a daily cap circuit breaker,
confidence calibration reporting, a golden-dataset eval harness, API key auth, rate
limiting, refund idempotency/audit/amount-capping, SQLite persistence.

Still a stub, by design — build these next as the client roster grows:
- `/support/analytics/agents` (per-human-agent performance) has no data source yet
- `resend_order` suggested_action type exists in the model but has no execution endpoint yet
  (unlike refund) — add one the same way if a client needs it
- SQLite (tickets, KB, traces, costs) is fine for one client on Render's free tier; move to
  Supabase/Postgres + pgvector once you're running multiple clients or need concurrent writers
- No retry/backoff on the Shopify calls yet — add `tenacity` if you see
  transient failures in production (LLM calls already have retry + fallback — see `agent/llm.py`)
- Rate limiting and the cost cap are in-process, not shared across instances — fine for a
  single Render instance, not for a multi-instance deploy (move to Redis if you scale that way)
- The eval dataset (15 cases) is a starting skeleton, not comprehensive coverage — it should
  grow from real edited tickets over time, per the Evals section above
- Gorgias `ticket-created` webhook is not idempotent — Gorgias uses at-least-once delivery,
  so a redelivered event could append a duplicate customer message to the thread and run the
  pipeline twice. The refund endpoint has idempotency protection; this webhook doesn't yet.

## Pricing

| Plan | Price | Includes |
|------|-------|----------|
| Starter | $1,500 setup | 1 agent, basic integration |
| Growth | $3,000 setup + $1,000/mo | Full integration, support |
| Enterprise | Custom | Multi-agent, SLA |

## Contact

- Email: your@email.com
- LinkedIn: your-linkedin
