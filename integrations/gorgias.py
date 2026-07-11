"""Minimal Gorgias REST API client — fetch tickets, post replies, normalize into our SupportTicket model."""

from typing import Any, Dict, List, Optional

import httpx
import structlog

from agent.config import settings
from agent.models import SupportTicket, TicketChannel

logger = structlog.get_logger(__name__)


class GorgiasNotConfigured(Exception):
    pass


class GorgiasClient:
    def __init__(self):
        self.enabled = bool(
            settings.GORGIAS_DOMAIN and settings.GORGIAS_EMAIL and settings.GORGIAS_API_KEY
        )
        if self.enabled:
            self.base_url = f"https://{settings.GORGIAS_DOMAIN}.gorgias.com/api"
            self.auth = (settings.GORGIAS_EMAIL, settings.GORGIAS_API_KEY.get_secret_value())

    async def list_open_tickets(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.enabled:
            raise GorgiasNotConfigured("Gorgias credentials not set in .env")
        async with httpx.AsyncClient(timeout=15, auth=self.auth) as client:
            resp = await client.get(
                f"{self.base_url}/tickets",
                params={"status": "open", "limit": limit},
            )
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def get_ticket_messages(self, ticket_id: str) -> List[Dict[str, Any]]:
        if not self.enabled:
            raise GorgiasNotConfigured("Gorgias credentials not set in .env")
        async with httpx.AsyncClient(timeout=15, auth=self.auth) as client:
            resp = await client.get(f"{self.base_url}/tickets/{ticket_id}/messages")
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def post_reply(self, ticket_id: str, body_html: str, channel: str = "email") -> Dict[str, Any]:
        """Posts a reply that actually sends to the customer. Only call this for auto-send-eligible tickets."""
        if not self.enabled:
            raise GorgiasNotConfigured("Gorgias credentials not set in .env")
        async with httpx.AsyncClient(timeout=15, auth=self.auth) as client:
            resp = await client.post(
                f"{self.base_url}/tickets/{ticket_id}/messages",
                json={
                    "channel": channel,
                    "via": channel,
                    "from_agent": True,
                    "body_html": body_html,
                    "source": {"type": channel},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def add_internal_note(self, ticket_id: str, note: str) -> Dict[str, Any]:
        """For low-confidence tickets: attach the AI draft as an internal note instead of sending it."""
        if not self.enabled:
            raise GorgiasNotConfigured("Gorgias credentials not set in .env")
        async with httpx.AsyncClient(timeout=15, auth=self.auth) as client:
            resp = await client.post(
                f"{self.base_url}/tickets/{ticket_id}/messages",
                json={
                    "channel": "internal-note",
                    "via": "internal-note",
                    "from_agent": True,
                    "body_html": note,
                    "source": {"type": "internal-note"},
                },
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def normalize_webhook_payload(payload: Dict[str, Any]) -> SupportTicket:
        """Gorgias 'ticket-created' webhook payload -> our SupportTicket model."""
        ticket = payload.get("ticket", payload)
        customer = ticket.get("customer", {}) or {}
        messages = ticket.get("messages", []) or []
        first_message = messages[0] if messages else {}

        return SupportTicket(
            id=f"gorgias_{ticket.get('id')}",
            gorgias_ticket_id=str(ticket.get("id")),
            customer_email=customer.get("email", "unknown@example.com"),
            customer_name=customer.get("name"),
            subject=ticket.get("subject") or "(no subject)",
            body=first_message.get("body_text") or first_message.get("stripped_text") or "",
            channel=TicketChannel.GORGIAS,
        )
