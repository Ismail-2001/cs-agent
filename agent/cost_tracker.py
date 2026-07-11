"""Model pricing table — the single source of truth for cost math.

Actual cost recording/tracking lives in agent/observability.py (record_llm_call), which
persists to the `llm_costs` SQLite table so spend survives restarts and can be queried by
day/stage via GET /support/analytics/costs. This file just holds the price list so both
observability.py and anything else that needs to estimate cost can share one number.
"""

MODEL_PRICING = {
    # per 1M tokens, USD
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "text-embedding-004": {"input": 0.0, "output": 0.0},  # free as of writing — verify current pricing
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},  # OpenRouter slug, same rate
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}
