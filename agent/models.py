from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TicketCategory(str, Enum):
    ORDER_STATUS = "order_status"
    SHIPPING = "shipping"
    RETURNS = "returns"
    REFUND = "refund"
    PRODUCT_QUESTION = "product_question"
    COMPLAINT = "complaint"
    TECHNICAL = "technical"
    OTHER = "other"


class TicketPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_CUSTOMER = "awaiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketChannel(str, Enum):
    EMAIL = "email"
    CHAT = "chat"
    GORGIAS = "gorgias"
    SOCIAL = "social"
    PHONE = "phone"


class Sentiment(str, Enum):
    VERY_NEGATIVE = "very_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    VERY_POSITIVE = "very_positive"


class SupportTicket(BaseModel):
    id: str
    shop_domain: Optional[str] = None
    customer_email: str
    customer_name: Optional[str] = None
    subject: str
    body: str
    channel: TicketChannel = TicketChannel.EMAIL
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    product_id: Optional[str] = None
    gorgias_ticket_id: Optional[str] = None
    status: TicketStatus = TicketStatus.OPEN
    category: Optional[TicketCategory] = None
    priority: Optional[TicketPriority] = None
    sentiment: Optional[Sentiment] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class MessageSender(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"       # human agent
    AI = "ai"              # this bot, when it auto-sent


class TicketMessage(BaseModel):
    id: Optional[int] = None
    ticket_id: str
    sender_type: MessageSender
    content: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ClassificationResult(BaseModel):
    category: TicketCategory
    priority: TicketPriority
    sentiment: Sentiment
    extracted_order_number: Optional[str] = Field(
        default=None,
        description="Order number mentioned in the ticket body, e.g. '#1042' or '1042'. Null if none found.",
    )
    reasoning: str = Field(description="One short sentence explaining the classification.")


class ResponseSuggestion(BaseModel):
    ticket_id: str
    suggested_response: str
    confidence: float
    reasoning: str
    requires_human_review: bool
    follow_up_questions: List[str] = Field(default_factory=list)
    suggested_action: Optional["SuggestedAction"] = None


class ActionType(str, Enum):
    REFUND = "refund"
    RESEND_ORDER = "resend_order"
    NONE = "none"


class SuggestedAction(BaseModel):
    type: ActionType = ActionType.NONE
    order_id: Optional[str] = None
    amount: Optional[float] = None
    reason: Optional[str] = None
    # Actions are ALWAYS human-approved regardless of response confidence — see
    # api/customer_support.py's /actions/refund endpoint. This flag is informational only.
    requires_approval: bool = True


class KnowledgeChunk(BaseModel):
    id: Optional[int] = None
    source: str                      # e.g. "policy:returns", "product:blue-hoodie"
    title: str
    content: str
    score: Optional[float] = None    # similarity score, populated only on search results


class EditRecord(BaseModel):
    ticket_id: str
    ai_suggestion: str
    final_response: str
    was_edited: bool
    similarity: float
    category: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class AgentDecision(BaseModel):
    ticket_id: str
    classification: ClassificationResult
    suggestion: ResponseSuggestion
    order_context_used: bool
    auto_sent: bool


class SupportAnalytics(BaseModel):
    total_tickets: int
    open_tickets: int
    avg_response_time_hours: Optional[float] = None
    avg_resolution_time_hours: Optional[float] = None
    satisfaction_score: Optional[float] = None
    first_contact_resolution_rate: Optional[float] = None
    escalation_rate: Optional[float] = None
    category_breakdown: Dict[str, int] = Field(default_factory=dict)
    priority_breakdown: Dict[str, int] = Field(default_factory=dict)
    channel_breakdown: Dict[str, int] = Field(default_factory=dict)
    sentiment_distribution: Dict[str, int] = Field(default_factory=dict)
