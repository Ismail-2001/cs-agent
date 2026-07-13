"""
Customer Support API Routes.
Endpoints for ticket ingestion (manual + Gorgias webhook), suggestions, responses, analytics.
"""

from datetime import datetime
from typing import Any, Dict, Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from agent.auth import check_shared_secret, verify_api_key
from agent.config import settings
from agent.knowledge_base import knowledge_base
from agent.models import (
    ActionType,
    MessageSender,
    SupportAnalytics,
    SupportTicket,
    TicketChannel,
)
from agent.rate_limit import rate_limit_default, rate_limit_refund, rate_limit_resend
from agent.storage import store
from agent.support_agent import CustomerSupportAgent
from integrations.gorgias import GorgiasClient, GorgiasNotConfigured
from integrations.shopify import ShopifyNotConfigured

logger = structlog.get_logger(__name__)

# Protected: everything a dashboard/admin/agency operator calls. Requires X-API-Key + rate limited.
router = APIRouter(
    prefix="/support", tags=["customer-support"],
    dependencies=[Depends(verify_api_key), Depends(rate_limit_default)],
)

# Public but secret-gated: what Gorgias/Twilio/a chat widget calls. No X-API-Key (those
# services can't easily send one) — each endpoint checks its own shared secret instead.
webhook_router = APIRouter(prefix="/support", tags=["webhooks"], dependencies=[Depends(rate_limit_default)])

# Fully public: uptime monitors need this to work with no credentials at all.
public_router = APIRouter(prefix="/support", tags=["public"])

_agent = CustomerSupportAgent()
_gorgias = GorgiasClient()


class TicketCreateRequest(BaseModel):
    shop_domain: Optional[str] = None
    customer_email: str
    customer_name: Optional[str] = None
    subject: str
    body: str
    channel: TicketChannel = TicketChannel.EMAIL
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    product_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    resolution_notes: Optional[str] = None


class ResponseRequest(BaseModel):
    response: str
    send_via_gorgias: bool = False


# ── Whoami (tenant identity check) ─────────────────────────


@router.get("/whoami")
async def whoami():
    """Return which client's instance this is — run this before any destructive action
    (especially refunds) to confirm you're hitting the right tenant."""
    return {
        "tenant_name": settings.TENANT_NAME,
        "shopify_domain": settings.SHOPIFY_SHOP_DOMAIN,
        "gorgias_domain": settings.GORGIAS_DOMAIN,
    }


# ── Ticket Management ──────────────────────────────────────


@router.post("/tickets")
async def create_ticket(req: TicketCreateRequest):
    """Create a ticket and run it through the full agent pipeline synchronously."""
    ticket_id = f"ticket_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    ticket = SupportTicket(
        id=ticket_id,
        shop_domain=req.shop_domain or settings.SHOPIFY_SHOP_DOMAIN,
        customer_email=req.customer_email,
        customer_name=req.customer_name,
        subject=req.subject,
        body=req.body,
        channel=req.channel,
        order_id=req.order_id,
        order_number=req.order_number,
        product_id=req.product_id,
        metadata=req.metadata or {},
    )

    decision = await _agent.handle_ticket(ticket)

    return {
        "ticket_id": ticket_id,
        "status": "processed",
        "classification": decision.classification.model_dump(),
        "suggestion": decision.suggestion.model_dump(),
        "order_context_used": decision.order_context_used,
        "auto_sent": decision.auto_sent,
    }


@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    rows = await store.list(status=status, category=category, priority=priority, page=page, limit=limit)
    return {
        "tickets": [r["ticket"] for r in rows],
        "total": len(rows),
        "page": page,
        "limit": limit,
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {**row["ticket"], "suggestion": row["suggestion"], "auto_sent": row["auto_sent"]}


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, req: TicketUpdateRequest):
    updated = await store.update_status(
        ticket_id, status=req.status, priority=req.priority, resolution_notes=req.resolution_notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket_id": ticket_id, "ticket": updated["ticket"]}


class FollowUpMessageRequest(BaseModel):
    body: str


@router.post("/tickets/{ticket_id}/messages")
async def add_followup_message(ticket_id: str, req: FollowUpMessageRequest):
    """A new customer message on an existing ticket. Re-runs classification + drafting with
    the full thread as context — this is the endpoint a chat widget or Gorgias message-created
    webhook should call for anything after the first message."""
    decision = await _agent.handle_followup(ticket_id, req.body)
    if not decision:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {
        "ticket_id": ticket_id,
        "classification": decision.classification.model_dump(),
        "suggestion": decision.suggestion.model_dump(),
        "order_context_used": decision.order_context_used,
        "auto_sent": decision.auto_sent,
    }


@router.get("/tickets/{ticket_id}/messages")
async def get_thread(ticket_id: str):
    messages = await store.get_messages(ticket_id)
    if not messages:
        row = await store.get(ticket_id)
        if not row:
            raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket_id": ticket_id, "messages": [m.model_dump() for m in messages]}


# ── Response Management ────────────────────────────────────


@router.get("/tickets/{ticket_id}/suggestion")
async def get_response_suggestion(ticket_id: str):
    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not row["suggestion"]:
        raise HTTPException(status_code=404, detail="No suggestion generated for this ticket yet")
    return {"ticket_id": ticket_id, "suggestion": row["suggestion"]}


@router.post("/tickets/{ticket_id}/respond")
async def respond_to_ticket(ticket_id: str, req: ResponseRequest):
    """Human approves (possibly edits) and sends the reply. This is the human-in-the-loop endpoint —
    call it from your agent dashboard's 'Send' button."""
    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")

    gorgias_ticket_id = row["ticket"].get("gorgias_ticket_id")
    if req.send_via_gorgias:
        if not gorgias_ticket_id:
            raise HTTPException(status_code=400, detail="Ticket has no linked Gorgias ticket")
        try:
            await _gorgias.post_reply(gorgias_ticket_id, req.response)
        except GorgiasNotConfigured:
            raise HTTPException(status_code=400, detail="Gorgias is not configured")

    # Self-improvement tracking: if this ticket had an AI draft, compare it to what was
    # actually sent. High edit rates on a category are the signal to improve that prompt
    # or add more knowledge base content for it — see /support/analytics/quality.
    if row["suggestion"] and row["suggestion"].get("suggested_response"):
        await store.log_edit(
            ticket_id=ticket_id,
            ai_suggestion=row["suggestion"]["suggested_response"],
            final_response=req.response,
            category=row["ticket"].get("category"),
            confidence=row["suggestion"].get("confidence"),
        )

    await store.add_message(ticket_id, MessageSender.AGENT.value, req.response)
    await store.update_status(ticket_id, status="resolved")
    return {"ticket_id": ticket_id, "status": "response_sent", "sent_at": datetime.utcnow().isoformat()}


# ── Actions (human-approved, money/fulfillment-moving) ──────


class RefundActionRequest(BaseModel):
    amount: float
    reason: str = "Approved by support team"
    notify_customer: bool = True


@router.post("/tickets/{ticket_id}/actions/refund", dependencies=[Depends(rate_limit_refund)])
async def approve_refund(
    ticket_id: str,
    req: RefundActionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    """Executes a REAL refund on the linked Shopify order. This endpoint is never called
    automatically by the agent — auto-send is hard-blocked whenever a suggested_action is
    present (see support_agent._should_auto_send). A human always calls this explicitly,
    e.g. by clicking 'Approve refund' on the AI's suggested_action in your dashboard.

    Requires an `Idempotency-Key` header (e.g. a UUID your dashboard generates once per
    click). If the same key is sent twice — a retried request, a double-click, a network
    retry — the second call returns the FIRST call's result instead of refunding twice.

    The requested amount is capped at the order's total_price. This does not track
    cumulative prior partial refunds on the same order — for a client doing partial
    refunds regularly, extend this check to subtract already-refunded amounts (Shopify's
    order object includes a `refunds` array you can sum)."""
    existing = await store.get_refund_audit(idempotency_key)
    if existing:
        logger.info("refund_idempotent_replay", idempotency_key=idempotency_key, ticket_id=ticket_id)
        return {"ticket_id": existing["ticket_id"], "order_id": existing["order_id"],
                "refund": existing["shopify_response"], "replayed": True}

    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    order_id = row["ticket"].get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Ticket has no linked order_id — look up the order first")

    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be positive")

    try:
        order = await _agent.shopify.get_order_by_id(order_id)
    except ShopifyNotConfigured:
        raise HTTPException(status_code=400, detail="Shopify is not configured")

    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found in Shopify")

    order_total = float(order.get("total_price", 0))
    if req.amount > order_total:
        raise HTTPException(
            status_code=400,
            detail=f"Refund amount {req.amount} exceeds order total {order_total} — refusing to process",
        )

    try:
        result = await _agent.shopify.create_refund(
            order_id=order_id, amount=req.amount, reason=req.reason, notify_customer=req.notify_customer
        )
    except Exception as e:
        logger.error("refund_failed", ticket_id=ticket_id, order_id=order_id, error=str(e))
        await store.record_refund_audit(
            idempotency_key, ticket_id, order_id, req.amount, req.reason, status="failed", error=str(e)
        )
        raise HTTPException(status_code=502, detail=f"Refund failed: {e}")

    await store.record_refund_audit(
        idempotency_key, ticket_id, order_id, req.amount, req.reason,
        status="succeeded", shopify_response=result,
    )
    await store.add_message(
        ticket_id, MessageSender.AGENT.value,
        f"[Action taken] Refund of {req.amount} approved and processed. Reason: {req.reason}",
    )
    await store.update_status(ticket_id, status="resolved")
    logger.info("refund_approved_and_processed", ticket_id=ticket_id, order_id=order_id, amount=req.amount)
    return {"ticket_id": ticket_id, "order_id": order_id, "refund": result, "replayed": False}


class ResendOrderActionRequest(BaseModel):
    notify_customer: bool = True
    reason: str = "Replacement order — item not received"


@router.post("/tickets/{ticket_id}/actions/resend-order", dependencies=[Depends(rate_limit_resend)])
async def approve_resend_order(
    ticket_id: str,
    req: ResendOrderActionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    """Creates a new Shopify order with the same items as the original, then completes it
    so a replacement is shipped. This endpoint is never called automatically — a human
    always calls this explicitly, e.g. by clicking 'Approve resend' on the AI's
    suggested_action in your dashboard.

    Requires an `Idempotency-Key` header. If the same key is sent twice, the second call
    returns the FIRST call's result instead of creating a duplicate order."""
    existing = await store.get_resend_audit(idempotency_key)
    if existing:
        logger.info("resend_idempotent_replay", idempotency_key=idempotency_key, ticket_id=ticket_id)
        return {"ticket_id": existing["ticket_id"], "order_id": existing["order_id"],
                "resend": existing["shopify_response"], "replayed": True}

    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    order_id = row["ticket"].get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Ticket has no linked order_id — look up the order first")

    try:
        order = await _agent.shopify.get_order_by_id(order_id)
    except ShopifyNotConfigured:
        raise HTTPException(status_code=400, detail="Shopify is not configured")

    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found in Shopify")

    try:
        result = await _agent.shopify.create_reorder(order_id=order_id, notify_customer=req.notify_customer)
    except Exception as e:
        logger.error("resend_failed", ticket_id=ticket_id, order_id=order_id, error=str(e))
        await store.record_resend_audit(
            idempotency_key, ticket_id, order_id, status="failed", error=str(e)
        )
        raise HTTPException(status_code=502, detail=f"Resend failed: {e}")

    await store.record_resend_audit(
        idempotency_key, ticket_id, order_id,
        status="succeeded", shopify_response=result,
    )
    await store.add_message(
        ticket_id, MessageSender.AGENT.value,
        f"[Action taken] Replacement order created. Reason: {req.reason}",
    )
    await store.update_status(ticket_id, status="resolved")
    logger.info("resend_approved_and_processed", ticket_id=ticket_id, order_id=order_id)
    return {"ticket_id": ticket_id, "order_id": order_id, "resend": result, "replayed": False}


# ── Knowledge Base (RAG) ─────────────────────────────────────


class KBIngestRequest(BaseModel):
    source: str
    title: str
    content: str


@router.post("/knowledge-base")
async def ingest_knowledge(req: KBIngestRequest):
    """Add or replace a knowledge base document (policy page, FAQ, product spec sheet).
    Re-ingesting the same `source` replaces the old chunks instead of duplicating them."""
    await knowledge_base.delete_source(req.source)
    chunk_count = await knowledge_base.ingest(req.source, req.title, req.content)
    return {"source": req.source, "chunks_created": chunk_count}


@router.post("/knowledge-base/sync-shopify")
async def sync_knowledge_from_shopify():
    """Pulls Settings > Policies and active product descriptions from Shopify and ingests
    them as knowledge base content. Run this once after setup and again whenever policies
    or the catalog change meaningfully."""
    if not _agent.shopify.enabled:
        raise HTTPException(status_code=400, detail="Shopify is not configured")

    total_chunks = 0
    try:
        policies = await _agent.shopify.get_shop_policies()
        for title, body in policies.items():
            source = f"policy:{title.lower().replace(' ', '-')}"
            await knowledge_base.delete_source(source)
            total_chunks += await knowledge_base.ingest(source, title, body)

        products = await _agent.shopify.get_products(limit=50)
        for p in products:
            source = f"product:{p.get('handle', p.get('id'))}"
            content = f"{p.get('title', '')}\n\n{p.get('body_html', '')}"
            await knowledge_base.delete_source(source)
            total_chunks += await knowledge_base.ingest(source, p.get("title", "Untitled product"), content)
    except ShopifyNotConfigured:
        raise HTTPException(status_code=400, detail="Shopify is not configured")

    return {"status": "synced", "total_chunks": total_chunks}


@router.get("/knowledge-base")
async def knowledge_base_status():
    return {"chunk_count": await knowledge_base.count()}


class KBSearchRequest(BaseModel):
    query: str
    top_k: int = 3


@router.post("/knowledge-base/search")
async def test_knowledge_search(req: KBSearchRequest):
    """Debug endpoint — test what the KB would retrieve for a given customer question,
    without running the full ticket pipeline."""
    results = await knowledge_base.search(req.query, top_k=req.top_k)
    return {"query": req.query, "results": [r.model_dump() for r in results]}


# ── Generic Inbound (non-Gorgias channels: chat widget, WhatsApp via Twilio, etc.) ──


class InboundMessageRequest(BaseModel):
    channel: TicketChannel
    customer_email: str
    customer_name: Optional[str] = None
    subject: Optional[str] = "Chat conversation"
    body: str
    thread_id: Optional[str] = None  # pass the same thread_id on follow-ups from the same customer/session


@webhook_router.post("/webhooks/inbound")
async def generic_inbound_message(
    req: InboundMessageRequest, x_webhook_secret: Optional[str] = Header(None)
):
    """One endpoint for any channel that isn't Gorgias — a website chat widget, a WhatsApp
    number via Twilio/Meta Cloud API, an Instagram DM bridge, etc. Pass the same `thread_id`
    (e.g. the customer's phone number or session id) on every message from the same
    conversation so it's treated as one thread instead of a new ticket each time.

    Set INBOUND_WEBHOOK_SECRET in .env and have whatever's calling this send the same value
    in an X-Webhook-Secret header — this endpoint has no API key, so the secret is the only
    guard against random internet traffic hitting it."""
    check_shared_secret(x_webhook_secret, settings.INBOUND_WEBHOOK_SECRET, "inbound webhook")

    if req.thread_id:
        existing = await store.get(f"inbound_{req.thread_id}")
        if existing:
            decision = await _agent.handle_followup(f"inbound_{req.thread_id}", req.body)
            return {
                "ticket_id": f"inbound_{req.thread_id}",
                "suggestion": decision.suggestion.model_dump() if decision else None,
                "auto_sent": decision.auto_sent if decision else False,
            }

    ticket_id = f"inbound_{req.thread_id}" if req.thread_id else f"inbound_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    ticket = SupportTicket(
        id=ticket_id, customer_email=req.customer_email, customer_name=req.customer_name,
        subject=req.subject or "Chat conversation", body=req.body, channel=req.channel,
    )
    decision = await _agent.handle_ticket(ticket)
    return {
        "ticket_id": ticket_id,
        "suggestion": decision.suggestion.model_dump(),
        "auto_sent": decision.auto_sent,
    }


# ── Gorgias Inbound Webhook ─────────────────────────────────


def _extract_event_id(payload: Dict[str, Any]) -> Optional[str]:
    """Extract a unique event id from a Gorgias webhook payload.

    Gorgias places it at the top-level ``id`` for some event types and at
    ``event_data.id`` for others. Return ``None`` when neither is present so
    the caller can still process the request (without dedup protection)."""
    event_id = payload.get("id") or (payload.get("event_data") or {}).get("id")
    if not event_id:
        logger.warning("webhook_missing_event_id", payload_keys=list(payload.keys()))
    return event_id


@webhook_router.post("/webhooks/gorgias/ticket-created")
async def gorgias_ticket_created_webhook(request: Request, x_webhook_secret: Optional[str] = Header(None)):
    """Point Gorgias's 'ticket-created' event webhook at this endpoint.
    Set GORGIAS_WEBHOOK_SECRET and configure the same value as a custom header in Gorgias's
    webhook settings — Gorgias doesn't sign payloads, so a shared secret is the guard here."""
    check_shared_secret(x_webhook_secret, settings.GORGIAS_WEBHOOK_SECRET, "Gorgias webhook")

    payload = await request.json()
    event_id = _extract_event_id(payload)
    if event_id:
        existing = await store.get_processed_webhook_event(event_id, "gorgias")
        if existing:
            logger.info("webhook_duplicate_skipped", event_id=event_id, endpoint="ticket-created")
            return {"received": True, "duplicate": True}

    ticket = GorgiasClient.normalize_webhook_payload(payload)

    decision = await _agent.handle_ticket(ticket)
    await _dispatch_gorgias_reply(ticket.gorgias_ticket_id, decision)

    if event_id:
        await store.record_processed_webhook_event(event_id, "gorgias")

    return {"received": True, "ticket_id": ticket.id, "auto_sent": decision.auto_sent}


@webhook_router.post("/webhooks/gorgias/message-created")
async def gorgias_message_created_webhook(request: Request, x_webhook_secret: Optional[str] = Header(None)):
    """Point Gorgias's 'message-created' event webhook at this endpoint (separate webhook in
    Gorgias's settings from ticket-created). Handles follow-up customer messages on tickets we've
    already seen — appends to the same thread instead of creating a duplicate ticket."""
    check_shared_secret(x_webhook_secret, settings.GORGIAS_WEBHOOK_SECRET, "Gorgias webhook")

    payload = await request.json()
    event_id = _extract_event_id(payload)
    if event_id:
        existing = await store.get_processed_webhook_event(event_id, "gorgias")
        if existing:
            logger.info("webhook_duplicate_skipped", event_id=event_id, endpoint="message-created")
            return {"received": True, "duplicate": True}

    raw = payload.get("event_data", payload)
    gorgias_ticket_id = str(raw.get("ticket_id") or raw.get("ticket", {}).get("id", ""))
    message_body = raw.get("body_text") or raw.get("stripped_text") or ""

    if not gorgias_ticket_id or not message_body:
        return {"received": True, "skipped": "missing ticket_id or message body"}

    # Only react to customer messages — ignore webhook echoes of our own agent replies.
    if raw.get("from_agent") is True:
        return {"received": True, "skipped": "message was from an agent, not the customer"}

    ticket = await store.get_ticket_by_gorgias_id(gorgias_ticket_id)
    if not ticket:
        logger.warning("gorgias_followup_unknown_ticket", gorgias_ticket_id=gorgias_ticket_id)
        return {"received": True, "skipped": "no matching ticket on file for this Gorgias ticket"}

    decision = await _agent.handle_followup(ticket.id, message_body)
    await _dispatch_gorgias_reply(gorgias_ticket_id, decision)

    if event_id:
        await store.record_processed_webhook_event(event_id, "gorgias")

    return {"received": True, "ticket_id": ticket.id, "auto_sent": decision.auto_sent if decision else False}


async def _dispatch_gorgias_reply(gorgias_ticket_id: Optional[str], decision) -> None:
    """High-confidence + policy-clear -> send straight back to the customer via Gorgias.
    Everything else -> attach as an internal note so a human sees the draft in Gorgias itself."""
    if not (decision and _gorgias.enabled and gorgias_ticket_id):
        return
    try:
        if decision.auto_sent:
            await _gorgias.post_reply(gorgias_ticket_id, decision.suggestion.suggested_response)
        else:
            note = (
                f"AI draft (confidence {decision.suggestion.confidence:.2f}, "
                f"review required):\n\n{decision.suggestion.suggested_response}"
            )
            await _gorgias.add_internal_note(gorgias_ticket_id, note)
    except Exception as e:
        logger.error("gorgias_reply_failed", gorgias_ticket_id=gorgias_ticket_id, error=str(e))


# ── Analytics ──────────────────────────────────────────────


@router.get("/analytics", response_model=SupportAnalytics)
async def get_support_analytics(days: int = Query(7, ge=1, le=90)):
    rows = await store.all()
    total = len(rows)
    category_breakdown: Dict[str, int] = {}
    priority_breakdown: Dict[str, int] = {}
    channel_breakdown: Dict[str, int] = {}
    sentiment_distribution: Dict[str, int] = {}
    open_count = 0
    auto_sent_count = 0

    for r in rows:
        t = r["ticket"]
        if t.get("status") in ("open", "in_progress", None):
            open_count += 1
        if r["auto_sent"]:
            auto_sent_count += 1
        for field, bucket in (
            ("category", category_breakdown),
            ("priority", priority_breakdown),
            ("channel", channel_breakdown),
            ("sentiment", sentiment_distribution),
        ):
            val = t.get(field)
            if val:
                bucket[val] = bucket.get(val, 0) + 1

    return SupportAnalytics(
        total_tickets=total,
        open_tickets=open_count,
        first_contact_resolution_rate=(auto_sent_count / total) if total else None,
        category_breakdown=category_breakdown,
        priority_breakdown=priority_breakdown,
        channel_breakdown=channel_breakdown,
        sentiment_distribution=sentiment_distribution,
    )


# ── Health ─────────────────────────────────────────────────


@router.get("/analytics/quality")
async def get_quality_analytics():
    """Self-improvement signal: how often humans edit the AI's drafts before sending, broken
    down by category. A category with a high edit_rate is telling you exactly where to spend
    your next hour — better prompt instructions, more knowledge base content, or leave that
    category as human-only for now."""
    return await store.get_edit_stats()


@router.get("/analytics/calibration")
async def get_calibration_report():
    """Confidence calibration: is the model's self-reported confidence actually trustworthy?
    Buckets past AI drafts by confidence and shows edit rate per bucket. If edit_rate doesn't
    drop as confidence rises, AUTO_SEND_MIN_CONFIDENCE is not doing what you think it's doing —
    raise it, or don't trust auto-send for that category yet."""
    return await store.get_calibration_report()


@router.get("/analytics/costs")
async def get_cost_analytics(days: int = Query(7, ge=1, le=90)):
    """Real LLM spend, not an estimate — pulled from the llm_costs table populated on every
    classification/response call. today_usd is what DAILY_COST_CAP_USD is compared against."""
    return await store.get_cost_report(days=days)


# ── Observability (debugging "why did it say that") ──────────


@router.get("/tickets/{ticket_id}/trace")
async def get_ticket_trace(ticket_id: str):
    """Full pipeline trace for one ticket: every LLM call's exact input (transcript, order
    context, KB context) and output (classification/suggestion), latency, tokens, and cost —
    in call order. This is what you pull up when a client asks 'why did the bot say that?'"""
    row = await store.get(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    traces = await store.get_traces(ticket_id)
    return {"ticket_id": ticket_id, "trace_count": len(traces), "traces": traces}


# ── Health ─────────────────────────────────────────────────


@public_router.get("/health")
async def customer_support_health():
    return {
        "status": "healthy",
        "shopify_connected": _agent.shopify.enabled,
        "gorgias_connected": _gorgias.enabled,
        "auto_send_enabled": settings.AUTO_SEND_ENABLED,
        "timestamp": datetime.utcnow().isoformat(),
    }
