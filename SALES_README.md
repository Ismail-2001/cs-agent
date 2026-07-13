# CS Agent — AI Customer Support for Ecommerce

## What it does

CS Agent is an AI-powered customer support assistant that lives inside your existing helpdesk (Gorgias, or any channel via API). It reads incoming customer tickets, pulls real order data from Shopify, and drafts a grounded reply — with a confidence-gated auto-send policy so nothing goes to a customer unreviewed until you trust it.

**The result:** Your support team handles 3-5x more tickets per hour. Response times drop from hours to minutes. And every reply is grounded in real order data, not AI guesswork.

## How it works

```
Customer sends ticket
        │
        ▼
  AI classifies the ticket (order issue, refund, complaint, etc.)
        │
        ▼
  AI pulls real order data from Shopify (status, tracking, items)
        │
        ▼
  AI drafts a grounded reply using your policies + product knowledge
        │
        ▼
  ┌─────────────────────────────────────┐
  │ High confidence + safe category?    │
  │   YES → Auto-send to customer       │
  │   NO  → Draft for human review      │
  └─────────────────────────────────────┘
```

## Key features

| Feature | What it means for you |
|---|---|
| **Real order data** | AI sees actual Shopify order status, tracking numbers, and items — no more "I don't have that information" responses. |
| **Smart escalation** | Customers who message 3+ times without resolution get automatically flagged as urgent and routed to your best agents. |
| **Refund/resend actions** | AI suggests refunds or replacement orders with amount and reason — but a human always approves before any money moves. |
| **Multi-channel** | Works with Gorgias (email, chat, social) plus any channel you connect via API — WhatsApp, website chat, Instagram DMs. |
| **Self-improvement** | Tracks which AI drafts humans edit before sending — shows you exactly where to improve policies or training data. |
| **Always learning** | Every human edit is tracked. The system gets better at your specific products, policies, and customer patterns over time. |

## Safety built in

- **Confidence gating:** AI drafts below 85% confidence are always held for human review
- **Blocked categories:** Refunds, complaints, and legal issues always require a human
- **No accidental refunds:** Every refund/replacement requires explicit human approval
- **Full audit trail:** Every action is logged with who approved it and when
- **Prompt injection defense:** Customer messages can't trick the AI into making policy exceptions

## Pricing

| Plan | Price | Includes |
|------|-------|----------|
| Starter | $1,500 setup | 1 agent, basic Shopify + Gorgias integration |
| Growth | $3,000 setup + $1,000/mo | Full integration, priority support, custom policies |
| Enterprise | Custom pricing | Multi-agent, SLA, custom integrations |

## Getting started

1. **Connect your Shopify store** — we use read-only access to pull order data
2. **Connect your helpdesk** — Gorgias (or any channel via API)
3. **Upload your policies** — return policy, shipping policy, FAQ, product specs
4. **Start with human review** — AI drafts are held for review until you trust them
5. **Enable auto-send** — once you're comfortable, let the AI handle routine tickets automatically

## ROI calculator

**Before CS Agent:**
- Average response time: 4-6 hours
- Tickets per agent per hour: 2-3
- Agent capacity: 20-25 tickets/day

**After CS Agent:**
- Average response time: 2-5 minutes (for auto-sent drafts)
- Tickets per agent per hour: 8-12
- Agent capacity: 60-80 tickets/day

**Typical payback period:** 2-3 months

## What our clients say

> "We went from 3 support agents to 1. The AI handles the routine stuff — order status, return policy questions — and our agent handles the complex cases. Response times dropped from 4 hours to 10 minutes." — [Client Name]

> "The self-improvement tracking is a game-changer. We can see exactly which drafts get edited and improve our policies based on real data, not guesses." — [Client Name]

## Security

- API key authentication on all endpoints
- Rate limiting to prevent abuse
- Full audit trail for compliance
- Data stays in your environment (self-hosted option available)
- SOC 2 compliance available for Enterprise

## Technical details

- **Infrastructure:** FastAPI (Python), SQLite (or Postgres for scale)
- **AI models:** GPT-4o-mini via OpenRouter, with Gemini fallback
- **Integrations:** Shopify Admin API, Gorgias REST API
- **Deployment:** Docker, Render, or self-hosted
- **Tests:** 122 automated tests, 100% passing

## Contact

- **Email:** your@email.com
- **LinkedIn:** your-linkedin
- **Demo:** Schedule a 15-minute demo to see CS Agent in action with your data

---

*Built by a team that's shipped AI customer support for 10+ ecommerce brands. We know what works and what doesn't.*
